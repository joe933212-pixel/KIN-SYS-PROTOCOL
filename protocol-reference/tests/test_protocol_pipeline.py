# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""End-to-end MINAH -> IVM -> CEE lifecycle and recovery tests."""
import tempfile
import subprocess
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol_pipeline import (
    ExecutionConflict, KinSysProtocolPipeline, LIFECYCLE, LifecycleViolation,
)


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
                return {'verified': True, 'mode': 'INSTITUTION_MINAH_IDENTITY_API'}
            def verify_action(self, payload):
                self.calls.append('authority_api')
                return {'verified': True, 'mode': 'INSTITUTION_MINAH_AUTHORITY_API'}

        agent = InstitutionAgent()
        pipeline = KinSysProtocolPipeline(minah_adapter=agent)
        try:
            result = pipeline.execute(request())
            self.assertEqual(result['status'], 'COMMITTED')
            self.assertEqual(agent.calls, ['review', 'identity_api', 'authority_api'])
            phases = [event['phase'] for event in result['cee_events']]
            self.assertLess(phases.index('MINAH_REVIEWED'), phases.index('IDENTITY_VERIFIED'))
            self.assertLess(phases.index('IDENTITY_VERIFIED'), phases.index('AUTHORITY_VERIFIED'))
            identity_event = next(event for event in result['cee_events'] if event['phase'] == 'IDENTITY_VERIFIED')
            authority_event = next(event for event in result['cee_events'] if event['phase'] == 'AUTHORITY_VERIFIED')
            self.assertEqual(identity_event['detail']['identity_mode'], 'INSTITUTION_MINAH_IDENTITY_API')
            self.assertEqual(authority_event['detail']['authority_mode'], 'INSTITUTION_MINAH_AUTHORITY_API')
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
                self.assertEqual([e["phase"] for e in resumed.cee.events("OP-001")][-2:], ["COMMITTED", "FINALIZED"])
            finally:
                resumed.close()




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

    def test_recovery_refuses_changed_minah_profile(self):
        payload = request(operation_id="RECOVERY-PROFILE", event_id="RECOVERY-PROFILE-EVENT", process_id="RECOVERY-PROFILE-PROC")
        self.pipeline.execute(payload)
        changed_profile = dict(self.pipeline.minah_profile)
        changed_profile["deployment_id"] = "different-institution-profile"
        self.pipeline.minah_profile = changed_profile
        with self.assertRaisesRegex(ExecutionConflict, "RECOVERY_PROFILE_DIFFERS_FROM_PINNED_PROFILE"):
            self.pipeline.recover("RECOVERY-PROFILE")

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


if __name__ == "__main__":
    unittest.main()
