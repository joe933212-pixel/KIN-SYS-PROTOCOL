# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""End-to-end MINAH -> IVM -> CEE lifecycle and recovery tests."""
import tempfile
import subprocess
import sqlite3
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol_pipeline import (
    ExecutionConflict, KinSysProtocolPipeline, LIFECYCLE, LifecycleViolation,
)
from minah_profile_governance import APPROVAL_VERSION, MinahApprovalViolation, profile_digest
from governed_contracts import compute_payload_digest


def request(**overrides):
    value = {
        "operation_id": "OP-001",
        "event_id": "EV-001",
        "process_id": "PROC-001",
        "actor_id": "AUTHORITY-001",
        "event_type": "ACTIVATE",
        "jurisdiction": "UG-DEMO",
        "rule_set": "KIN-SYS-DEMO-RULES",
        "rule_version": "1.0",
        "evidence": {
            "consignment_id": "PROC-001",
            "commodity": "SYNTHETIC_MAIZE",
            "origin": "UGANDA-DEMO",
            "destination": "BORDER-DEMO",
            "rule_refs": ["SYNTHETIC-RULE-1"],
        },
    }
    value.update(overrides)
    return value


class ProtocolPipelineTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = KinSysProtocolPipeline()
        self.addCleanup(self.pipeline.close)

    def _profile_approval_fixture(self, revoked_state):
        profile = {
            "profile_version": "kin-sys-minah-profile-0.1",
            "deployment_id": "approval-recovery-test",
            "owner_scope": "INSTITUTION",
            "owner_ref": "INSTITUTION-APPROVAL-01",
            "use_case": "RECOVERY_TEST",
            "jurisdictions": ["UG-DEMO"],
            "policy_pack_version": "POLICY-1",
            "source_pack_version": "SOURCES-1",
            "model_ref": "model-1",
            "deployment_mode": "SELF_HOSTED",
            "may_authorise": False,
        }
        approval = {
            "approval_version": APPROVAL_VERSION,
            "approval_id": "approval-recovery-001",
            "signer_ref": "trusted-owner-key-01",
            "algorithm": "TEST-ONLY",
            "profile_digest": profile_digest(profile),
            "profile_owner_scope": profile["owner_scope"],
            "profile_owner_ref": profile["owner_ref"],
            "approved_at": "2026-11-01T00:00:00Z",
            "expires_at": "2027-11-01T00:00:00Z",
            "signature": "test-signature",
        }
        def signature_verifier(signer_ref, algorithm, payload, signature):
            return signer_ref == "trusted-owner-key-01" and algorithm == "TEST-ONLY" and bool(payload) and signature == "test-signature"
        def revocation_checker(approval_id):
            return revoked_state["revoked"]
        return profile, approval, signature_verifier, revocation_checker

    def test_recovery_rejects_profile_approval_revoked_after_initial_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            cee_db = str(Path(tmp) / "cee.sqlite3")
            ivm_db = str(Path(tmp) / "ivm.sqlite3")
            revoked = {"revoked": False}
            profile, approval, verifier, revocation = self._profile_approval_fixture(revoked)
            first = KinSysProtocolPipeline(
                cee_database=cee_db, ivm_database=ivm_db,
                minah_profile=profile, minah_profile_approval=approval,
                profile_signature_verifier=verifier,
                profile_revocation_checker=revocation, require_profile_approval=True,
            )
            original_advance = first.cee.advance
            def interrupt_after_authority(operation_id, phase, **kwargs):
                result = original_advance(operation_id, phase, **kwargs)
                if operation_id == "PROFILE-REVOKED-DURING-RECOVERY" and phase == "AUTHORITY_VERIFIED":
                    raise RuntimeError("INJECTED_FAILURE_AFTER_AUTHORITY_VERIFIED")
                return result
            first.cee.advance = interrupt_after_authority
            try:
                with self.assertRaisesRegex(RuntimeError, "AUTHORITY_VERIFIED"):
                    first.execute(request(operation_id="PROFILE-REVOKED-DURING-RECOVERY"))
            finally:
                first.close()

            revoked["revoked"] = True
            resumed = KinSysProtocolPipeline(
                cee_database=cee_db, ivm_database=ivm_db,
                minah_profile=profile, minah_profile_approval=approval,
                profile_signature_verifier=verifier,
                profile_revocation_checker=revocation, require_profile_approval=True,
            )
            try:
                with self.assertRaisesRegex(MinahApprovalViolation, "APPROVAL_REVOKED"):
                    resumed.recover("PROFILE-REVOKED-DURING-RECOVERY")
                self.assertIsNone(resumed.ivm.event("EV-001"))
                observation = resumed.cee.observations("PROFILE-REVOKED-DURING-RECOVERY")[-1]
                self.assertEqual(observation["kind"], "MINAH_PROFILE_APPROVAL_REVALIDATION")
                self.assertEqual(observation["status"], "REJECTED")
            finally:
                resumed.close()

    def test_recovery_reconciles_already_durable_ivm_event_even_if_profile_approval_later_revoked(self):
        with tempfile.TemporaryDirectory() as tmp:
            cee_db = str(Path(tmp) / "cee.sqlite3")
            ivm_db = str(Path(tmp) / "ivm.sqlite3")
            revoked = {"revoked": False}
            profile, approval, verifier, revocation = self._profile_approval_fixture(revoked)
            first = KinSysProtocolPipeline(
                cee_database=cee_db, ivm_database=ivm_db,
                minah_profile=profile, minah_profile_approval=approval,
                profile_signature_verifier=verifier,
                profile_revocation_checker=revocation, require_profile_approval=True,
            )
            try:
                with self.assertRaisesRegex(RuntimeError, "INJECTED_FAILURE_AFTER_IVM_COMMIT"):
                    first.execute(
                        request(operation_id="PROFILE-REVOKED-AFTER-IVM-COMMIT"),
                        fail_after_ivm_commit=True,
                    )
            finally:
                first.close()

            revoked["revoked"] = True
            resumed = KinSysProtocolPipeline(
                cee_database=cee_db, ivm_database=ivm_db,
                minah_profile=profile, minah_profile_approval=approval,
                profile_signature_verifier=verifier,
                profile_revocation_checker=revocation, require_profile_approval=True,
            )
            try:
                result = resumed.recover("PROFILE-REVOKED-AFTER-IVM-COMMIT")
                self.assertEqual(result["status"], "COMMITTED")
                self.assertTrue(result["duplicate"])
                self.assertEqual(len(resumed.ivm.history("PROC-001")), 1)
                self.assertFalse(any(
                    item["kind"] == "MINAH_PROFILE_APPROVAL_REVALIDATION"
                    for item in result["cee_observations"]
                ))
            finally:
                resumed.close()

    def test_operation_records_its_institution_owned_minah_profile(self):
        profile = {
            "profile_version": "kin-sys-minah-profile-0.1",
            "deployment_id": "enterprise-finance-minah-02",
            "owner_scope": "ENTERPRISE",
            "owner_ref": "FINANCE-ENTITY-02",
            "use_case": "PAYMENT_APPROVAL",
            "jurisdictions": ["UG"],
            "policy_pack_version": "FINANCE-POLICY-7",
            "source_pack_version": "FINANCE-SOURCES-4",
            "model_ref": "enterprise-selected-model-v3",
            "deployment_mode": "ON_PREMISES",
            "may_authorise": False,
        }
        pipeline = KinSysProtocolPipeline(minah_profile=profile)
        self.addCleanup(pipeline.close)
        result = pipeline.execute(request(operation_id="PROFILE-PIN-01", jurisdiction="UG"))
        recorded = result["minah_disposition"]["deployment_profile"]
        self.assertEqual(recorded["deployment_id"], "enterprise-finance-minah-02")
        self.assertEqual(recorded["owner_scope"], "ENTERPRISE")
        self.assertEqual(recorded["policy_pack_version"], "FINANCE-POLICY-7")
        reviewed = next(event for event in result["cee_events"] if event["phase"] == "MINAH_REVIEWED")
        self.assertEqual(reviewed["detail"]["minah_deployment_id"], "enterprise-finance-minah-02")
        self.assertEqual(reviewed["detail"]["minah_profile_version"], "kin-sys-minah-profile-0.1")

    def test_profile_jurisdiction_mismatch_stops_before_minah_and_ivm(self):
        class MustNotRunMinah:
            called = False
            def review(self, evidence):
                self.called = True
                raise AssertionError('MINAH must not run outside its configured jurisdiction')

        profile = {
            'profile_version': 'kin-sys-minah-profile-0.1',
            'deployment_id': 'uganda-enterprise-minah',
            'owner_scope': 'ENTERPRISE',
            'owner_ref': 'ENTERPRISE-UG-01',
            'use_case': 'CROSS_BORDER_OPERATIONS',
            'jurisdictions': ['UG'],
            'policy_pack_version': 'POLICY-1',
            'source_pack_version': 'SOURCES-1',
            'model_ref': 'institution-agent-v1',
            'deployment_mode': 'ON_PREMISES',
            'may_authorise': False,
        }
        agent = MustNotRunMinah()
        pipeline = KinSysProtocolPipeline(minah_profile=profile, minah_adapter=agent)
        try:
            result = pipeline.execute(request(operation_id='SCOPE-MISMATCH-01', jurisdiction='KE'))
            self.assertEqual(result['status'], 'REJECTED')
            self.assertFalse(agent.called)
            self.assertIsNone(result['ivm_decision'])
            phases = [event['phase'] for event in result['cee_events']]
            self.assertIn('MINAH_REVIEWED', phases)
            self.assertNotIn('IDENTITY_VERIFIED', phases)
            self.assertNotIn('ADMITTED', phases)
            self.assertEqual(result['minah_disposition']['reason'], 'MINAH_PROFILE_JURISDICTION_MISMATCH')
        finally:
            pipeline.close()

    def test_retry_cannot_switch_minah_deployment_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            cee_db = str(Path(directory) / "cee.sqlite")
            ivm_db = str(Path(directory) / "ivm.sqlite")
            profile_a = {
                "profile_version": "kin-sys-minah-profile-0.1",
                "deployment_id": "institution-minah-a",
                "owner_scope": "INSTITUTION",
                "owner_ref": "INSTITUTION-A",
                "use_case": "CASE_REVIEW",
                "jurisdictions": ["UG-DEMO"],
                "policy_pack_version": "POLICY-1",
                "source_pack_version": "SOURCES-1",
                "model_ref": "model-a",
                "deployment_mode": "SELF_HOSTED",
                "may_authorise": False,
            }
            profile_b = dict(profile_a, deployment_id="institution-minah-b", model_ref="model-b")
            first = KinSysProtocolPipeline(
                cee_database=cee_db, ivm_database=ivm_db, minah_profile=profile_a
            )
            try:
                first.execute(request(operation_id="PROFILE-SWITCH-01"))
            finally:
                first.close()
            second = KinSysProtocolPipeline(
                cee_database=cee_db, ivm_database=ivm_db, minah_profile=profile_b
            )
            try:
                with self.assertRaisesRegex(ExecutionConflict, "OPERATION_ID_REUSED_WITH_DIFFERENT_CONTENT"):
                    second.execute(request(operation_id="PROFILE-SWITCH-01"))
            finally:
                second.close()

    def test_rejects_minah_profile_that_claims_institutional_authority(self):
        profile = {
            "profile_version": "kin-sys-minah-profile-0.1",
            "deployment_id": "invalid-profile",
            "owner_scope": "INSTITUTION",
            "owner_ref": "INSTITUTION-01",
            "use_case": "CASE_REVIEW",
            "jurisdictions": ["UG"],
            "policy_pack_version": "POLICY-1",
            "source_pack_version": "SOURCES-1",
            "model_ref": "model-1",
            "deployment_mode": "SELF_HOSTED",
            "may_authorise": True,
        }
        with self.assertRaisesRegex(ValueError, "MINAH_MUST_NOT_GRANT_INSTITUTIONAL_AUTHORITY"):
            KinSysProtocolPipeline(minah_profile=profile)

    def test_minah_rejection_and_unknown_stop_before_identity_authority_and_cee(self):
        class FixedMinah:
            def __init__(self, status):
                self.status = status

            def review(self, evidence):
                return {
                    "status": self.status,
                    "missing_fields": [],
                    "source_refs": ["TEST-SOURCE-REF"],
                    "uncertainty": "HIGH" if self.status == "UNKNOWN" else "LOW",
                    "may_authorise": False,
                    "model_version": "test-minah-1",
                }

        for status in ("REJECTED", "UNKNOWN"):
            with self.subTest(status=status):
                pipeline = KinSysProtocolPipeline(minah_adapter=FixedMinah(status))
                try:
                    result = pipeline.execute(request(operation_id="MINAH-GATE-" + status))
                    self.assertEqual(result["status"], status)
                    self.assertIsNone(result["ivm_decision"])
                    self.assertFalse(result["funds_moved"])
                    phases = [event["phase"] for event in result["cee_events"]]
                    self.assertIn("MINAH_REVIEWED", phases)
                    self.assertNotIn("IDENTITY_VERIFIED", phases)
                    self.assertNotIn("AUTHORITY_VERIFIED", phases)
                    self.assertNotIn("ADMITTED", phases)
                    self.assertEqual(result["minah_disposition"]["status"], status)
                finally:
                    pipeline.close()

    def test_institution_owned_minah_can_coordinate_identity_and_authority_checks(self):
        class InstitutionAgent:
            def __init__(self):
                self.calls = []
            def review(self, evidence):
                self.calls.append('review')
                return {'status': 'ADVISORY_ONLY', 'missing_fields': [], 'source_refs': ['OWNER-POLICY-1'], 'uncertainty': 'LOW', 'may_authorise': False, 'model_version': 'institution-agent-v1'}
            def verify_principal(self, payload):
                self.calls.append('identity_api')
                return {
                    'verified': True, 'mode': 'INSTITUTION_MINAH_IDENTITY_API',
                    'evidence_ref': 'identity-evidence:opaque-001',
                    'subject_ref': 'subject:pseudonymous-001',
                    'verified_at': '2026-11-05T08:00:00Z',
                    'raw_document_number': 'TEST-ONLY-SENSITIVE-FIELD',
                }
            def verify_action(self, payload):
                self.calls.append('authority_api')
                return {
                    'verified': True, 'mode': 'INSTITUTION_MINAH_AUTHORITY_API',
                    'mandate_ref': 'mandate:opaque-001',
                    'policy_version': 'FINANCE-POLICY-7',
                    'mandate_version': '3',
                    'scope_ref': 'scope:payments:approve',
                    'valid_until': '2026-11-05T09:00:00Z',
                    'raw_authority_document': 'TEST-ONLY-SECRET-FIELD',
                }

        agent = InstitutionAgent()
        pipeline = KinSysProtocolPipeline(minah_adapter=agent)
        try:
            result = pipeline.execute(request())
            self.assertEqual(result['status'], 'COMMITTED')
            self.assertEqual(agent.calls, ['review', 'identity_api', 'authority_api', 'authority_api'])
            phases = [event['phase'] for event in result['cee_events']]
            self.assertLess(phases.index('MINAH_REVIEWED'), phases.index('IDENTITY_VERIFIED'))
            self.assertLess(phases.index('IDENTITY_VERIFIED'), phases.index('AUTHORITY_VERIFIED'))
            identity_event = next(event for event in result['cee_events'] if event['phase'] == 'IDENTITY_VERIFIED')
            authority_event = next(event for event in result['cee_events'] if event['phase'] == 'AUTHORITY_VERIFIED')
            self.assertEqual(identity_event['detail']['identity_mode'], 'INSTITUTION_MINAH_IDENTITY_API')
            self.assertEqual(authority_event['detail']['authority_mode'], 'INSTITUTION_MINAH_AUTHORITY_API')
            identity_evidence = identity_event['detail']['identity_evidence']
            self.assertEqual(identity_evidence['metadata']['evidence_ref'], 'identity-evidence:opaque-001')
            self.assertTrue(identity_evidence['evidence_reference_supplied'])
            self.assertNotIn('raw_document_number', identity_evidence['metadata'])
            authority_evidence = authority_event['detail']['authority_evidence']
            self.assertEqual(authority_evidence['metadata']['mandate_ref'], 'mandate:opaque-001')
            self.assertEqual(authority_evidence['metadata']['mandate_version'], '3')
            self.assertTrue(authority_evidence['evidence_reference_supplied'])
            self.assertNotIn('raw_authority_document', authority_evidence['metadata'])
            revalidation = result['cee_observations'][-1]
            self.assertEqual(revalidation['kind'], 'AUTHORITY_REVALIDATION')
            self.assertEqual(revalidation['status'], 'VERIFIED')
            self.assertEqual(revalidation['detail']['metadata']['mandate_ref'], 'mandate:opaque-001')
        finally:
            pipeline.close()

    def test_identity_adapter_contradictions_fail_closed_and_sanitise_result(self):
        class Adapter:
            def __init__(self, response):
                self.response = response
            def review(self, evidence):
                return {"status": "ADVISORY_ONLY", "missing_fields": [], "source_refs": [],
                        "uncertainty": "LOW", "may_authorise": False, "model_version": "test"}
            def verify_principal(self, payload):
                return self.response
            def verify_action(self, payload):
                raise AssertionError("authority check must not run after identity failure")

        cases = (
            ({"verified": True, "status": "REVOKED", "raw_document": "SECRET"}, "REJECTED"),
            ({"verified": True, "status": "EXPIRED", "raw_document": "SECRET"}, "REJECTED"),
            ({"verified": True, "status": "UNKNOWN", "raw_document": "SECRET"}, "UNKNOWN"),
            ({"verified": "true", "raw_document": "SECRET"}, "UNKNOWN"),
            ({"status": "VERIFIED", "raw_document": "SECRET"}, "UNKNOWN"),
        )
        for index, (response, expected) in enumerate(cases):
            with self.subTest(response=response):
                pipeline = KinSysProtocolPipeline(minah_adapter=Adapter(response))
                try:
                    result = pipeline.execute(request(operation_id=f"IDENTITY-STRICT-{index}"))
                    self.assertEqual(result["status"], expected)
                    self.assertIsNone(result["ivm_decision"])
                    self.assertNotIn("raw_document", result["identity_result"])
                    self.assertEqual(result["identity_result"]["status"], expected)
                    self.assertEqual(pipeline.cee.get(f"IDENTITY-STRICT-{index}")["phase"], "FINALIZED")
                finally:
                    pipeline.close()

    def test_authority_adapter_contradictions_fail_closed_and_sanitise_result(self):
        class Adapter:
            def __init__(self, response):
                self.response = response
            def review(self, evidence):
                return {"status": "ADVISORY_ONLY", "missing_fields": [], "source_refs": [],
                        "uncertainty": "LOW", "may_authorise": False, "model_version": "test"}
            def verify_principal(self, payload):
                return {"verified": True, "mode": "TEST_IDENTITY"}
            def verify_action(self, payload):
                return self.response

        cases = (
            ({"verified": True, "status": "REVOKED", "raw_mandate": "SECRET"}, "REJECTED"),
            ({"verified": True, "status": "EXPIRED", "raw_mandate": "SECRET"}, "REJECTED"),
            ({"verified": True, "status": "UNKNOWN", "raw_mandate": "SECRET"}, "UNKNOWN"),
            ({"verified": "true", "raw_mandate": "SECRET"}, "UNKNOWN"),
            ({"status": "VERIFIED", "raw_mandate": "SECRET"}, "UNKNOWN"),
        )
        for index, (response, expected) in enumerate(cases):
            with self.subTest(response=response):
                pipeline = KinSysProtocolPipeline(minah_adapter=Adapter(response))
                try:
                    result = pipeline.execute(request(operation_id=f"AUTHORITY-STRICT-{index}"))
                    self.assertEqual(result["status"], expected)
                    self.assertIsNone(result["ivm_decision"])
                    self.assertNotIn("raw_mandate", result["authority_result"])
                    self.assertEqual(result["authority_result"]["status"], expected)
                    phases = [event["phase"] for event in result["cee_events"]]
                    self.assertNotIn("ADMITTED", phases)
                finally:
                    pipeline.close()

    def test_recovery_revalidation_rejects_verified_true_with_revoked_status(self):
        class ContradictoryAuthority:
            def verify_action(self, payload):
                return {
                    "verified": True, "status": "REVOKED",
                    "mode": "TEST_AUTHORITY", "raw_mandate_document": "SECRET",
                }

        pipeline = KinSysProtocolPipeline(authority_adapter=ContradictoryAuthority())
        try:
            payload = request(
                operation_id="RECOVERY-CONTRADICTORY-AUTHORITY",
                event_id="RECOVERY-CONTRADICTORY-EVENT",
                process_id="RECOVERY-CONTRADICTORY-PROCESS",
            )
            pinned = {**payload, "_minah_deployment_profile": pipeline.minah_profile}
            minah = {
                "status": "ADVISORY_ONLY", "missing_fields": [], "source_refs": [],
                "uncertainty": "LOW", "may_authorise": False,
                "model_version": "deterministic-minah-mock",
            }
            pipeline.cee.start(payload["operation_id"], pinned)
            pipeline.cee.advance(payload["operation_id"], "MINAH_REVIEWED", minah=minah)
            pipeline.cee.advance(payload["operation_id"], "IDENTITY_VERIFIED")
            pipeline.cee.advance(payload["operation_id"], "AUTHORITY_VERIFIED")
            pipeline.cee.advance(payload["operation_id"], "ADMITTED")
            pipeline.cee.advance(payload["operation_id"], "CONTEXT_ESTABLISHED")
            pipeline.cee.advance(payload["operation_id"], "AUTHORITY_EVALUATED", minah=minah)
            pipeline.cee.advance(payload["operation_id"], "EXECUTING")
            pipeline.cee.advance(payload["operation_id"], "PREPARED")
            pipeline.cee.advance(payload["operation_id"], "COMMITTING")

            result = pipeline.recover(payload["operation_id"])
            self.assertEqual(result["status"], "REJECTED")
            self.assertEqual(result["reason"], "AUTHORITY_REVOKED_OR_NOT_VALID_AT_EXECUTION")
            self.assertNotIn("raw_mandate_document", result["authority_result"])
            self.assertIsNone(pipeline.ivm.event(payload["event_id"]))
            self.assertEqual(result["cee_observations"][-1]["status"], "REJECTED")
        finally:
            pipeline.close()

    def test_minah_verification_api_outage_returns_unknown_and_stops(self):
        class UnavailableInstitutionAgent:
            def review(self, evidence):
                return {"status": "ADVISORY_ONLY", "missing_fields": [], "source_refs": [], "uncertainty": "LOW", "may_authorise": False, "model_version": "institution-agent-v1"}
            def verify_principal(self, payload):
                raise TimeoutError("identity API unavailable")
            def verify_action(self, payload):
                raise AssertionError("authority check must not run after identity uncertainty")

        pipeline = KinSysProtocolPipeline(minah_adapter=UnavailableInstitutionAgent())
        try:
            result = pipeline.execute(request(operation_id="MINAH-IDENTITY-OUTAGE"))
            self.assertEqual(result["status"], "UNKNOWN")
            self.assertEqual(result["reason"], "IDENTITY_VERIFICATION_UNAVAILABLE")
            self.assertIsNone(result["ivm_decision"])
            phases = [event["phase"] for event in result["cee_events"]]
            self.assertNotIn("AUTHORITY_VERIFIED", phases)
            self.assertNotIn("ADMITTED", phases)
        finally:
            pipeline.close()

    def test_authority_revocation_before_ivm_commit_fails_closed(self):
        class RevokingInstitutionAgent:
            def __init__(self):
                self.authority_checks = 0
            def review(self, evidence):
                return {
                    "status": "ADVISORY_ONLY", "missing_fields": [],
                    "source_refs": ["POLICY-1"], "uncertainty": "LOW",
                    "may_authorise": False, "model_version": "institution-agent-v1",
                }
            def verify_principal(self, payload):
                return {"verified": True, "mode": "IDENTITY_API"}
            def verify_action(self, payload):
                self.authority_checks += 1
                if self.authority_checks == 1:
                    return {"verified": True, "mode": "AUTHORITY_API"}
                return {"verified": False, "status": "REVOKED", "mode": "AUTHORITY_API"}

        agent = RevokingInstitutionAgent()
        pipeline = KinSysProtocolPipeline(minah_adapter=agent)
        try:
            result = pipeline.execute(request(operation_id="AUTHORITY-REVOKED-BEFORE-COMMIT"))
            self.assertEqual(result["status"], "REJECTED")
            self.assertEqual(result["reason"], "AUTHORITY_REVOKED_OR_NOT_VALID_AT_EXECUTION")
            self.assertIsNone(result["ivm_decision"])
            self.assertIsNone(pipeline.ivm.event("EV-001"))
            self.assertEqual(agent.authority_checks, 2)
            self.assertEqual(result["cee_observations"][-1]["kind"], "AUTHORITY_REVALIDATION")
            self.assertEqual(result["cee_observations"][-1]["status"], "REJECTED")
            self.assertEqual(pipeline.cee.get("AUTHORITY-REVOKED-BEFORE-COMMIT")["phase"], "FINALIZED")
        finally:
            pipeline.close()

    def test_recovery_revalidates_authority_before_uncommitted_ivm_transition(self):
        class RevokedAuthority:
            def verify_action(self, payload):
                return {"verified": False, "status": "REVOKED", "mode": "INSTITUTION_AUTHORITY_API"}

        pipeline = KinSysProtocolPipeline(authority_adapter=RevokedAuthority())
        try:
            payload = request(
                operation_id="RECOVERY-AUTHORITY-REVOKED",
                event_id="RECOVERY-AUTHORITY-EVENT",
                process_id="RECOVERY-AUTHORITY-PROCESS",
            )
            pinned = {**payload, "_minah_deployment_profile": pipeline.minah_profile}
            minah = {
                "status": "ADVISORY_ONLY", "missing_fields": [], "source_refs": [],
                "uncertainty": "LOW", "may_authorise": False,
                "model_version": "deterministic-minah-mock",
            }
            pipeline.cee.start(payload["operation_id"], pinned)
            pipeline.cee.advance(payload["operation_id"], "MINAH_REVIEWED", minah=minah)
            pipeline.cee.advance(payload["operation_id"], "IDENTITY_VERIFIED")
            pipeline.cee.advance(payload["operation_id"], "AUTHORITY_VERIFIED")
            pipeline.cee.advance(payload["operation_id"], "ADMITTED")
            pipeline.cee.advance(payload["operation_id"], "CONTEXT_ESTABLISHED")
            pipeline.cee.advance(payload["operation_id"], "AUTHORITY_EVALUATED", minah=minah)
            pipeline.cee.advance(payload["operation_id"], "EXECUTING")
            pipeline.cee.advance(payload["operation_id"], "PREPARED")
            pipeline.cee.advance(payload["operation_id"], "COMMITTING")

            result = pipeline.recover(payload["operation_id"])
            self.assertEqual(result["status"], "REJECTED")
            self.assertEqual(result["reason"], "AUTHORITY_REVOKED_OR_NOT_VALID_AT_EXECUTION")
            self.assertIsNone(pipeline.ivm.event(payload["event_id"]))
            self.assertEqual(pipeline.cee.get(payload["operation_id"])["phase"], "FINALIZED")
            self.assertEqual(result["cee_observations"][-1]["kind"], "AUTHORITY_REVALIDATION")
            self.assertEqual(result["cee_observations"][-1]["status"], "REJECTED")
        finally:
            pipeline.close()

    def test_authority_verification_api_outage_returns_unknown_and_stops(self):
        class AuthorityUnavailableAgent:
            def review(self, evidence):
                return {
                    "status": "ADVISORY_ONLY",
                    "missing_fields": [],
                    "source_refs": ["OWNER-POLICY-1"],
                    "uncertainty": "LOW",
                    "may_authorise": False,
                    "model_version": "institution-agent-v1",
                }

            def verify_action(self, payload):
                raise TimeoutError("authority API unavailable")

        pipeline = KinSysProtocolPipeline(minah_adapter=AuthorityUnavailableAgent())
        try:
            result = pipeline.execute(request(operation_id="MINAH-AUTHORITY-OUTAGE"))
            self.assertEqual(result["status"], "UNKNOWN")
            self.assertEqual(result["reason"], "AUTHORITY_VERIFICATION_UNAVAILABLE")
            self.assertIsNone(result["ivm_decision"])
            self.assertFalse(result["funds_moved"])
            phases = [event["phase"] for event in result["cee_events"]]
            self.assertIn("IDENTITY_VERIFIED", phases)
            self.assertNotIn("AUTHORITY_VERIFIED", phases)
            self.assertNotIn("ADMITTED", phases)
            self.assertNotIn("EXECUTING", phases)
        finally:
            pipeline.close()

    def test_complete_path_runs_minah_ivm_and_cee_in_order(self):
        result = self.pipeline.execute(request())
        self.assertEqual(result["status"], "COMMITTED")
        self.assertFalse(result["funds_moved"])
        self.assertEqual(result["minah_disposition"]["status"], "ADVISORY_ONLY")
        self.assertFalse(result["minah_disposition"]["may_authorise"])
        self.assertEqual(result["ivm_decision"]["to_state"], "ACTIVE")
        self.assertEqual([event["phase"] for event in result["cee_events"]], list(LIFECYCLE))
        self.assertEqual(result["cee_events"][0]["phase"], "REQUESTED")
        self.assertEqual(result["cee_events"][1]["phase"], "MINAH_REVIEWED")
        self.assertEqual([event["phase"] for event in result["cee_events"][:5]], ["REQUESTED", "MINAH_REVIEWED", "IDENTITY_VERIFIED", "AUTHORITY_VERIFIED", "ADMITTED"])
        components = [event["component"] for event in result["component_trace"]]
        self.assertLess(components.index("MINAH"), components.index("IDENTITY"))
        self.assertLess(components.index("IDENTITY"), components.index("AUTHORITY"))
        self.assertLess(components.index("AUTHORITY"), components.index("IVM"))
        self.assertEqual(components[-1], "CEE")
        self.assertEqual(result["component_trace"][-1]["detail"]["phase"], "FINALIZED")
        self.assertEqual(result["commit_scope"], "LOCAL_IVM_SQLITE_ONLY_NOT_DISTRIBUTED_FINALITY")

    def test_minah_cannot_self_authorise_or_bypass_ivm_boundary(self):
        class SelfAuthorisingMinah:
            def review(self, evidence):
                return {
                    "status": "ADVISORY_ONLY",
                    "missing_fields": [],
                    "source_refs": [],
                    "uncertainty": "LOW",
                    "may_authorise": True,
                    "model_version": "hostile-test-model",
                }

        hostile = KinSysProtocolPipeline(minah_adapter=SelfAuthorisingMinah())
        try:
            result = hostile.execute(request())
            self.assertEqual(result["status"], "REJECTED")
            self.assertEqual(result["reason"], "MINAH_DISPOSITION_CONTRACT_VIOLATION")
            self.assertIsNone(result["ivm_decision"])
            self.assertEqual(hostile.cee.get("OP-001")["phase"], "FINALIZED")
            with self.assertRaises(Exception):
                hostile.ivm.process("PROC-001")
        finally:
            hostile.close()

    def test_missing_evidence_defers_before_ivm_execution(self):
        result = self.pipeline.execute(request(evidence={"commodity": "SYNTHETIC_MAIZE"}))
        self.assertEqual(result["status"], "DEFERRED")
        self.assertIsNone(result["ivm_decision"])
        self.assertIn("MINAH", [event["component"] for event in result["component_trace"]])
        self.assertNotIn("IVM", [event["component"] for event in result["component_trace"]])
        self.assertEqual(self.pipeline.cee.get("OP-001")["phase"], "FINALIZED")
        self.assertEqual(self.pipeline.cee.events("OP-001")[-1]["status"], "DEFERRED")
        with self.assertRaises(Exception):
            self.pipeline.ivm.process("PROC-001")

    def test_wrong_actor_is_rejected_before_cee_admission_and_ivm(self):
        result = self.pipeline.execute(request(actor_id="UNAUTHORISED-ACTOR"))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["reason"], "AUTHORITY_NOT_VERIFIED")
        self.assertIsNone(result["ivm_decision"])
        phases = [event["phase"] for event in result["cee_events"]]
        self.assertNotIn("ADMITTED", phases)
        with self.assertRaises(Exception):
            self.pipeline.ivm.process("PROC-001")

    def test_revoked_authority_is_rejected(self):
        revoked = KinSysProtocolPipeline(authority_active=False)
        try:
            result = revoked.execute(request())
            self.assertEqual(result["status"], "REJECTED")
            self.assertEqual(result["reason"], "AUTHORITY_NOT_VERIFIED")
            with self.assertRaises(Exception):
                revoked.ivm.process("PROC-001")
        finally:
            revoked.close()

    def test_rule_ineligibility_is_rejected(self):
        evidence = request()["evidence"]
        evidence["commodity"] = "SYNTHETIC_PROHIBITED_COMMODITY"
        result = self.pipeline.execute(request(evidence=evidence))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["reason"], "RULE_REJECTED")
        self.assertEqual(self.pipeline.ivm.process("PROC-001")["state"], "PENDING")

    def test_operation_id_cannot_be_reused_for_different_payload(self):
        self.pipeline.execute(request())
        changed = request()
        changed["evidence"] = {**changed["evidence"], "origin": "DIFFERENT-ORIGIN"}
        with self.assertRaises(ExecutionConflict):
            self.pipeline.execute(changed)

    def test_event_id_conflict_is_rejected_by_ivm(self):
        self.pipeline.execute(request())
        with self.assertRaises(Exception):
            self.pipeline.execute(request(operation_id="OP-002", event_id="EV-001", process_id="PROC-002"))

    def test_retry_after_ivm_commit_before_cee_ack_recovers_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            cee_db = str(Path(tmp) / "cee.sqlite3")
            ivm_db = str(Path(tmp) / "ivm.sqlite3")
            pipeline = KinSysProtocolPipeline(cee_database=cee_db, ivm_database=ivm_db)
            try:
                with self.assertRaisesRegex(RuntimeError, "INJECTED_FAILURE_AFTER_IVM_COMMIT"):
                    pipeline.execute(request(), fail_after_ivm_commit=True)
            finally:
                pipeline.close()
            resumed = KinSysProtocolPipeline(cee_database=cee_db, ivm_database=ivm_db)
            try:
                result = resumed.recover("OP-001")
                self.assertEqual(result["status"], "COMMITTED")
                self.assertTrue(result["duplicate"])
                self.assertEqual(resumed.ivm.process("PROC-001")["state"], "ACTIVE")
                self.assertEqual(len(resumed.ivm.history("PROC-001")), 1)
                self.assertEqual(result["cee_observations"][-1]["kind"], "AUTHORITY_REVALIDATION")
                self.assertEqual(result["cee_observations"][-1]["status"], "NOT_REQUIRED")
                self.assertEqual([e["phase"] for e in resumed.cee.events("OP-001")][-2:], ["COMMITTED", "FINALIZED"])
            finally:
                resumed.close()





    def test_recovery_refuses_inconsistent_ivm_event_and_history_evidence(self):
        for corruption, expected_reason in (
            ("delete_event", "IVM_HISTORY_WITHOUT_EVENT"),
            ("change_digest", "IVM_EVENT_DIGEST_MISMATCH"),
        ):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as tmp:
                cee_db = str(Path(tmp) / "cee.sqlite3")
                ivm_db = str(Path(tmp) / "ivm.sqlite3")
                first = KinSysProtocolPipeline(cee_database=cee_db, ivm_database=ivm_db)
                try:
                    with self.assertRaisesRegex(RuntimeError, "INJECTED_FAILURE_AFTER_IVM_COMMIT"):
                        first.execute(request(), fail_after_ivm_commit=True)
                finally:
                    first.close()

                damaged = sqlite3.connect(ivm_db)
                try:
                    if corruption == "delete_event":
                        damaged.execute("DELETE FROM events WHERE event_id=?", ("EV-001",))
                    else:
                        damaged.execute(
                            "UPDATE events SET request_digest=? WHERE event_id=?",
                            ("corrupted-digest", "EV-001"),
                        )
                    damaged.commit()
                finally:
                    damaged.close()

                recovered = KinSysProtocolPipeline(cee_database=cee_db, ivm_database=ivm_db)
                try:
                    with self.assertRaisesRegex(ExecutionConflict, expected_reason):
                        recovered.recover("OP-001")
                    self.assertEqual(recovered.cee.get("OP-001")["phase"], "COMMITTING")
                    self.assertEqual(recovered.ivm.process("PROC-001")["state"], "ACTIVE")
                    self.assertEqual(len(recovered.ivm.history("PROC-001")), 1)
                finally:
                    recovered.close()

    def test_recovery_matrix_resumes_every_durable_post_minah_phase(self):
        recoverable_phases = (
            "MINAH_REVIEWED", "IDENTITY_VERIFIED", "AUTHORITY_VERIFIED",
            "ADMITTED", "CONTEXT_ESTABLISHED", "AUTHORITY_EVALUATED",
            "EXECUTING", "PREPARED", "COMMITTING", "COMMITTED",
        )
        for phase in recoverable_phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                cee_db = str(Path(tmp) / "cee.sqlite3")
                ivm_db = str(Path(tmp) / "ivm.sqlite3")
                operation_id = "RECOVER-MATRIX-" + phase
                process_id = "PROC-" + phase
                event_id = "EV-" + phase
                payload = request(
                    operation_id=operation_id,
                    process_id=process_id,
                    event_id=event_id,
                    evidence={
                        "consignment_id": process_id,
                        "commodity": "SYNTHETIC_MAIZE",
                        "origin": "UGANDA-DEMO",
                        "destination": "BORDER-DEMO",
                        "rule_refs": ["SYNTHETIC-RULE-1"],
                    },
                )
                first = KinSysProtocolPipeline(cee_database=cee_db, ivm_database=ivm_db)
                original_advance = first.cee.advance

                def interrupt_after_phase(op_id, next_phase, **kwargs):
                    result = original_advance(op_id, next_phase, **kwargs)
                    if op_id == operation_id and next_phase == phase:
                        raise RuntimeError("INJECTED_FAILURE_AFTER_DURABLE_PHASE:" + phase)
                    return result

                first.cee.advance = interrupt_after_phase
                try:
                    with self.assertRaisesRegex(RuntimeError, phase):
                        first.execute(payload)
                    durable = first.cee.get(operation_id)
                    self.assertEqual(durable["phase"], phase)
                finally:
                    first.close()

                resumed = KinSysProtocolPipeline(cee_database=cee_db, ivm_database=ivm_db)
                try:
                    result = resumed.recover(operation_id)
                    self.assertEqual(result["status"], "COMMITTED")
                    self.assertEqual(resumed.cee.get(operation_id)["phase"], "FINALIZED")
                    self.assertEqual(resumed.ivm.process(process_id)["state"], "ACTIVE")
                    self.assertEqual(len(resumed.ivm.history(process_id)), 1)
                finally:
                    resumed.close()

    def test_process_termination_after_ivm_commit_recovers_without_duplicate_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            cee_db = str(Path(tmp) / "cee.sqlite3")
            ivm_db = str(Path(tmp) / "ivm.sqlite3")
            payload = request()
            script = f"""
import os, sys
sys.path.insert(0, {str(ROOT)!r})
from protocol_pipeline import KinSysProtocolPipeline
pipeline = KinSysProtocolPipeline(cee_database={cee_db!r}, ivm_database={ivm_db!r})
original_execute = pipeline.ivm.execute
def commit_then_terminate(event, **kwargs):
    decision = original_execute(event, **kwargs)
    if decision.status == "ACCEPTED" and not decision.duplicate:
        os._exit(73)
    return decision
pipeline.ivm.execute = commit_then_terminate
pipeline.execute({payload!r})
os._exit(74)
"""
            child = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(child.returncode, 73, child.stderr)

            recovered = KinSysProtocolPipeline(cee_database=cee_db, ivm_database=ivm_db)
            try:
                self.assertEqual(recovered.cee.get("OP-001")["phase"], "COMMITTING")
                self.assertIsNotNone(recovered.ivm.event("EV-001"))
                self.assertEqual(recovered.ivm.process("PROC-001")["state"], "ACTIVE")
                self.assertEqual(len(recovered.ivm.history("PROC-001")), 1)

                result = recovered.recover("OP-001")
                self.assertEqual(result["status"], "COMMITTED")
                self.assertTrue(result["duplicate"])
                self.assertEqual(recovered.cee.get("OP-001")["phase"], "FINALIZED")
                self.assertEqual(recovered.ivm.process("PROC-001")["state"], "ACTIVE")
                self.assertEqual(len(recovered.ivm.history("PROC-001")), 1)
            finally:
                recovered.close()


    def test_process_termination_before_ivm_commit_recovers_transition_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            cee_db = str(Path(tmp) / "cee.sqlite3")
            ivm_db = str(Path(tmp) / "ivm.sqlite3")
            payload = request()
            script = f"""
import os, sys
sys.path.insert(0, {str(ROOT)!r})
from protocol_pipeline import KinSysProtocolPipeline
pipeline = KinSysProtocolPipeline(cee_database={cee_db!r}, ivm_database={ivm_db!r})
def terminate_before_ivm_commit(event, **kwargs):
    os._exit(72)
pipeline.ivm.execute = terminate_before_ivm_commit
pipeline.execute({payload!r})
os._exit(74)
"""
            child = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(child.returncode, 72, child.stderr)
            recovered = KinSysProtocolPipeline(cee_database=cee_db, ivm_database=ivm_db)
            try:
                self.assertEqual(recovered.cee.get("OP-001")["phase"], "COMMITTING")
                self.assertIsNone(recovered.ivm.event("EV-001"))
                result = recovered.recover("OP-001")
                self.assertEqual(result["status"], "COMMITTED")
                self.assertEqual(recovered.cee.get("OP-001")["phase"], "FINALIZED")
                self.assertEqual(len(recovered.ivm.history("PROC-001")), 1)
            finally:
                recovered.close()

    def test_process_termination_after_cee_commit_finalizes_without_reapplying_ivm(self):
        with tempfile.TemporaryDirectory() as tmp:
            cee_db = str(Path(tmp) / "cee.sqlite3")
            ivm_db = str(Path(tmp) / "ivm.sqlite3")
            payload = request()
            script = f"""
import os, sys
sys.path.insert(0, {str(ROOT)!r})
from protocol_pipeline import KinSysProtocolPipeline
pipeline = KinSysProtocolPipeline(cee_database={cee_db!r}, ivm_database={ivm_db!r})
original_advance = pipeline.cee.advance
def advance_then_terminate(operation_id, next_phase, **kwargs):
    result = original_advance(operation_id, next_phase, **kwargs)
    if next_phase == "COMMITTED":
        os._exit(74)
    return result
pipeline.cee.advance = advance_then_terminate
pipeline.execute({payload!r})
os._exit(75)
"""
            child = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(child.returncode, 74, child.stderr)
            recovered = KinSysProtocolPipeline(cee_database=cee_db, ivm_database=ivm_db)
            try:
                self.assertEqual(recovered.cee.get("OP-001")["phase"], "COMMITTED")
                self.assertEqual(len(recovered.ivm.history("PROC-001")), 1)
                result = recovered.recover("OP-001")
                self.assertEqual(result["status"], "COMMITTED")
                self.assertEqual(recovered.cee.get("OP-001")["phase"], "FINALIZED")
                self.assertEqual(len(recovered.ivm.history("PROC-001")), 1)
            finally:
                recovered.close()

    def test_process_termination_before_minah_disposition_stays_manual(self):
        with tempfile.TemporaryDirectory() as tmp:
            cee_db = str(Path(tmp) / "cee.sqlite3")
            ivm_db = str(Path(tmp) / "ivm.sqlite3")
            payload = request()
            script = f"""
import os, sys
sys.path.insert(0, {str(ROOT)!r})
from protocol_pipeline import KinSysProtocolPipeline
pipeline = KinSysProtocolPipeline(cee_database={cee_db!r}, ivm_database={ivm_db!r})
original_start = pipeline.cee.start
def start_then_terminate(operation_id, request):
    result = original_start(operation_id, request)
    os._exit(75)
pipeline.cee.start = start_then_terminate
pipeline.execute({payload!r})
os._exit(76)
"""
            child = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(child.returncode, 75, child.stderr)
            recovered = KinSysProtocolPipeline(cee_database=cee_db, ivm_database=ivm_db)
            try:
                with self.assertRaisesRegex(
                    ExecutionConflict, "MINAH_DISPOSITION_NOT_DURABLE"
                ):
                    recovered.recover("OP-001")
                operation = recovered.cee.get("OP-001")
                self.assertEqual(operation["phase"], "REQUESTED")
                self.assertIsNone(operation["minah_json"])
                self.assertIsNone(recovered.ivm.event("EV-001"))
            finally:
                recovered.close()


    def _contract_envelope_for_internal_request(self, **overrides):
        envelope = {
            "contract_version": "kin-sys-governed-contract-0.1",
            "request_id": "request-binding-001",
            "operation_id": "OP-001",
            "idempotency_key": "idempotency-binding-001",
            "principal_ref": "AUTHORITY-001",
            "authority_ref": "authority-check:binding",
            "action": "ACTIVATE",
            "target_ref": "PROC-001",
            "purpose": "test execution envelope binding",
            "jurisdiction": "UG-DEMO",
            "submitted_at": "2026-10-27T10:00:00+03:00",
            "evidence": {
                "consignment_id": "PROC-001",
                "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
                "rule_refs": ["SYNTHETIC-RULE-1"],
            },
            "payload_digest": "sha256:" + ("0" * 64),
            "rule_pack_version": "KIN-SYS-DEMO-RULES:1.0",
        }
        envelope.update(overrides)
        envelope["payload_digest"] = compute_payload_digest(envelope)
        return envelope

    def test_pipeline_rejects_contract_envelope_operation_mismatch(self):
        envelope = self._contract_envelope_for_internal_request(operation_id="OTHER-OPERATION")
        with self.assertRaisesRegex(ValueError, "CONTRACT_BINDING_MISMATCH:operation_id"):
            self.pipeline._validate(request(_contract_envelope=envelope))

    def test_pipeline_derives_ivm_event_id_from_contract_idempotency_key(self):
        envelope = self._contract_envelope_for_internal_request()
        with self.assertRaisesRegex(ValueError, "CONTRACT_BINDING_MISMATCH:idempotency_key"):
            self.pipeline._validate(request(_contract_envelope=envelope))

    def test_recovery_refuses_to_repeat_minah_when_disposition_was_not_persisted(self):
        payload = request(operation_id="RECOVERY-AMBIGUOUS", event_id="RECOVERY-EVENT", process_id="RECOVERY-PROC")
        self.pipeline.cee.start("RECOVERY-AMBIGUOUS", {
            **payload,
            "_minah_deployment_profile": self.pipeline.minah_profile,
        })
        with self.assertRaisesRegex(ExecutionConflict, "MINAH_DISPOSITION_NOT_DURABLE"):
            self.pipeline.recover("RECOVERY-AMBIGUOUS")
        self.assertEqual(self.pipeline.cee.get("RECOVERY-AMBIGUOUS")["phase"], "REQUESTED")
        self.assertIsNone(self.pipeline.ivm.event("RECOVERY-EVENT"))


    def test_recovery_rejects_replacement_request_even_for_finalized_cee(self):
        self.pipeline.execute(request())
        replacement = request(event_id="REPLACEMENT-EVENT")
        with self.assertRaisesRegex(
            ExecutionConflict, "RECOVERY_REQUEST_DIFFERS_FROM_DURABLE_INGRESS_REQUEST"
        ):
            self.pipeline.recover("OP-001", expected_request=replacement)

    def test_recovery_refuses_changed_minah_profile_for_nonfinal_operation(self):
        payload = request(operation_id="RECOVERY-PROFILE", event_id="RECOVERY-PROFILE-EVENT", process_id="RECOVERY-PROFILE-PROC")
        with self.assertRaisesRegex(RuntimeError, "INJECTED_FAILURE_AFTER_IVM_COMMIT"):
            self.pipeline.execute(payload, fail_after_ivm_commit=True)
        changed_profile = dict(self.pipeline.minah_profile)
        changed_profile["deployment_id"] = "different-institution-profile"
        self.pipeline.minah_profile = changed_profile
        with self.assertRaisesRegex(ExecutionConflict, "RECOVERY_PROFILE_DIFFERS_FROM_PINNED_PROFILE"):
            self.pipeline.recover("RECOVERY-PROFILE")

    def test_finalized_cee_result_remains_reconcilable_after_profile_change(self):
        payload = request(operation_id="FINAL-PROFILE-CHANGE", event_id="FINAL-PROFILE-EVENT", process_id="FINAL-PROFILE-PROC")
        self.pipeline.execute(payload)
        changed_profile = dict(self.pipeline.minah_profile)
        changed_profile["deployment_id"] = "later-institution-profile"
        self.pipeline.minah_profile = changed_profile
        result = self.pipeline.recover("FINAL-PROFILE-CHANGE")
        self.assertEqual(result["status"], "COMMITTED")
        self.assertTrue(result["duplicate"])
        self.assertEqual(self.pipeline.cee.get("FINAL-PROFILE-CHANGE")["phase"], "FINALIZED")

    def test_finalized_operation_is_idempotent(self):
        first = self.pipeline.execute(request())
        second = self.pipeline.execute(request())
        self.assertEqual(second["status"], "COMMITTED")
        self.assertTrue(second["duplicate"])
        self.assertEqual(len(self.pipeline.ivm.history("PROC-001")), 1)

    def test_illegal_lifecycle_transition_is_rejected(self):
        self.pipeline.cee.start("OP-X", request(operation_id="OP-X", event_id="EV-X", process_id="PROC-X"))
        with self.assertRaises(LifecycleViolation):
            self.pipeline.cee.advance("OP-X", "COMMITTED")


    def test_recovery_refuses_to_resume_before_authority_gate_is_durable(self):
        payload = request(operation_id="EARLY-RECOVERY", event_id="EV-EARLY", process_id="PROC-EARLY")
        self.pipeline.cee.start("EARLY-RECOVERY", {
            **payload, "_minah_deployment_profile": self.pipeline.minah_profile,
        })
        with self.assertRaisesRegex(ExecutionConflict, "MINAH_DISPOSITION_NOT_DURABLE"):
            self.pipeline.recover("EARLY-RECOVERY")
        self.assertEqual(self.pipeline.ivm.history("PROC-EARLY"), [])

    def test_legacy_unfinished_operation_without_minah_disposition_fails_closed(self):
        payload = request(operation_id="LEGACY-OP", event_id="LEGACY-EV", process_id="LEGACY-PROC")
        self.pipeline.cee.start("LEGACY-OP", payload)
        # Simulate a durable row created under the pre-MINAH-first lifecycle.
        self.pipeline.cee.db.execute(
            "UPDATE cee_operations SET phase='CONTEXT_ESTABLISHED' WHERE operation_id=?",
            ("LEGACY-OP",),
        )
        self.pipeline.cee.db.commit()
        with self.assertRaisesRegex(ExecutionConflict, "LEGACY_OPERATION_REQUIRES_RECONCILIATION"):
            self.pipeline.execute(payload)


    def test_cee_start_rolls_back_operation_when_initial_event_persistence_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "cee-start-fault.sqlite3")
            pipeline = KinSysProtocolPipeline(cee_database=db)
            self.addCleanup(pipeline.close)
            pipeline.cee.db.execute("""
                CREATE TRIGGER reject_requested_event
                BEFORE INSERT ON cee_events
                WHEN NEW.phase = 'REQUESTED'
                BEGIN SELECT RAISE(ABORT, 'INJECTED_CEE_EVENT_WRITE_FAILURE'); END;
            """)
            pipeline.cee.db.commit()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "INJECTED_CEE_EVENT_WRITE_FAILURE"):
                pipeline.cee.start("CEE-START-FAULT", request(
                    operation_id="CEE-START-FAULT", event_id="CEE-START-EVENT",
                    process_id="CEE-START-PROCESS",
                ))
            self.assertIsNone(pipeline.cee.get("CEE-START-FAULT"))
            self.assertEqual(pipeline.cee.events("CEE-START-FAULT"), [])

    def test_cee_phase_advance_rolls_back_state_when_event_persistence_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "cee-advance-fault.sqlite3")
            pipeline = KinSysProtocolPipeline(cee_database=db)
            self.addCleanup(pipeline.close)
            payload = request(
                operation_id="CEE-ADVANCE-FAULT", event_id="CEE-ADVANCE-EVENT",
                process_id="CEE-ADVANCE-PROCESS",
            )
            pipeline.cee.start("CEE-ADVANCE-FAULT", payload)
            pipeline.cee.db.execute("""
                CREATE TRIGGER reject_minah_event
                BEFORE INSERT ON cee_events
                WHEN NEW.phase = 'MINAH_REVIEWED'
                BEGIN SELECT RAISE(ABORT, 'INJECTED_CEE_PHASE_EVENT_FAILURE'); END;
            """)
            pipeline.cee.db.commit()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "INJECTED_CEE_PHASE_EVENT_FAILURE"):
                pipeline.cee.advance("CEE-ADVANCE-FAULT", "MINAH_REVIEWED", minah={"status": "ADVISORY_ONLY"})
            operation = pipeline.cee.get("CEE-ADVANCE-FAULT")
            self.assertEqual(operation["phase"], "REQUESTED")
            self.assertIsNone(operation["minah_json"])
            self.assertEqual([event["phase"] for event in pipeline.cee.events("CEE-ADVANCE-FAULT")], ["REQUESTED"])

    def test_cee_observation_write_failure_leaves_no_partial_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "cee-observation-fault.sqlite3")
            pipeline = KinSysProtocolPipeline(cee_database=db)
            self.addCleanup(pipeline.close)
            payload = request(
                operation_id="CEE-OBSERVATION-FAULT", event_id="CEE-OBSERVATION-EVENT",
                process_id="CEE-OBSERVATION-PROCESS",
            )
            pipeline.cee.start("CEE-OBSERVATION-FAULT", payload)
            pipeline.cee.db.execute("""
                CREATE TRIGGER reject_observation
                BEFORE INSERT ON cee_observations
                BEGIN SELECT RAISE(ABORT, 'INJECTED_CEE_OBSERVATION_FAILURE'); END;
            """)
            pipeline.cee.db.commit()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "INJECTED_CEE_OBSERVATION_FAILURE"):
                pipeline.cee.record_observation(
                    "CEE-OBSERVATION-FAULT", "FAULT_TEST", "UNKNOWN", {"safe": True}
                )
            self.assertEqual(pipeline.cee.observations("CEE-OBSERVATION-FAULT"), [])


    def test_recovery_stops_when_cee_claims_commit_but_ivm_event_is_missing(self):
        self.pipeline.execute(request())
        self.pipeline.ivm.db.execute(
            "DELETE FROM transition_history WHERE event_id = ?", ("EV-001",)
        )
        self.pipeline.ivm.db.execute(
            "DELETE FROM events WHERE event_id = ?", ("EV-001",)
        )
        self.pipeline.ivm.db.commit()
        with self.assertRaisesRegex(
            ExecutionConflict,
            "CEE_COMMIT_CLAIM_WITHOUT_DURABLE_IVM_EVENT_REQUIRES_MANUAL_RECONCILIATION",
        ):
            self.pipeline.recover("OP-001")

    def test_recovery_stops_when_ivm_event_exists_before_cee_commit_boundary(self):
        self.pipeline.execute(request())
        self.pipeline.cee.db.execute(
            "UPDATE cee_operations SET phase = 'EXECUTING' WHERE operation_id = ?",
            ("OP-001",),
        )
        self.pipeline.cee.db.commit()
        with self.assertRaisesRegex(
            ExecutionConflict,
            "IVM_EVENT_EXISTS_BEFORE_CEE_COMMIT_BOUNDARY_REQUIRES_MANUAL_RECONCILIATION",
        ):
            self.pipeline.recover("OP-001")

    def test_recovery_stops_when_cee_ivm_decision_differs_from_durable_event(self):
        self.pipeline.execute(request())
        self.pipeline.cee.db.execute(
            "UPDATE cee_operations SET ivm_json = ? WHERE operation_id = ?",
            ('{"status":"REJECTED","event_id":"EV-001","process_id":"PROC-001"}', "OP-001"),
        )
        self.pipeline.cee.db.commit()
        with self.assertRaisesRegex(
            ExecutionConflict,
            "CEE_IVM_DECISION_DIFFERS_FROM_DURABLE_EVENT_REQUIRES_MANUAL_RECONCILIATION",
        ):
            self.pipeline.recover("OP-001")


if __name__ == "__main__":
    unittest.main()
