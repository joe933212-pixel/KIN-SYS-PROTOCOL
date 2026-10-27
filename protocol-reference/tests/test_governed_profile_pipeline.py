# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""End-to-end tests for MINAH profile approval at the execution boundary."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from minah_deployment_profile import PROFILE_VERSION
from minah_profile_governance import (
    APPROVAL_VERSION,
    MinahApprovalViolation,
    profile_digest,
)
from protocol_pipeline import KinSysProtocolPipeline


def make_profile():
    return {
        "profile_version": PROFILE_VERSION,
        "deployment_id": "governed-minah-test",
        "owner_scope": "INSTITUTION",
        "owner_ref": "TEST-INSTITUTION",
        "use_case": "PROTOCOL_REFERENCE_TEST",
        "jurisdictions": ["UG-DEMO"],
        "policy_pack_version": "TEST-POLICY-1",
        "source_pack_version": "TEST-SOURCES-1",
        "model_ref": "test-minah-agent-v1",
        "deployment_mode": "SELF_HOSTED",
        "may_authorise": False,
    }


def make_approval(profile):
    return {
        "approval_version": APPROVAL_VERSION,
        "approval_id": "test-approval-001",
        "signer_ref": "test-owner-key",
        "algorithm": "TEST-ONLY",
        "profile_digest": profile_digest(profile),
        "approved_at": "2026-10-26T10:00:00Z",
        "expires_at": "2027-10-26T10:00:00Z",
        "signature": "test-signature",
    }


def request(operation_id="GOVERNED-PROFILE-001"):
    return {
        "operation_id": operation_id,
        "event_id": operation_id + "-EVENT",
        "process_id": operation_id + "-PROCESS",
        "actor_id": "AUTHORITY-001",
        "event_type": "ACTIVATE",
        "jurisdiction": "UG-DEMO",
        "rule_set": "KIN-SYS-DEMO-RULES",
        "rule_version": "1.0",
        "evidence": {
            "consignment_id": operation_id,
            "commodity": "SYNTHETIC_MAIZE",
            "origin": "UGANDA-DEMO",
            "destination": "BORDER-DEMO",
            "rule_refs": ["SYNTHETIC-RULE-1"],
        },
    }


class MustNotRunMinah:
    def __init__(self):
        self.called = False

    def review(self, evidence):
        self.called = True
        raise AssertionError("MINAH must not run when profile approval is not trusted")


class GovernedProfilePipelineTests(unittest.TestCase):
    def verifier(self, signer_ref, algorithm, payload, signature):
        return signer_ref == "test-owner-key" and algorithm == "TEST-ONLY" and signature == "test-signature" and bool(payload)

    def test_unapproved_profile_fails_before_minah(self):
        agent = MustNotRunMinah()
        pipeline = KinSysProtocolPipeline(
            minah_adapter=agent,
            minah_profile=make_profile(),
            require_profile_approval=True,
            profile_signature_verifier=self.verifier,
            profile_revocation_checker=lambda approval_id: False,
        )
        try:
            with self.assertRaisesRegex(MinahApprovalViolation, "APPROVAL_MUST_BE_OBJECT"):
                pipeline.execute(request())
            self.assertFalse(agent.called)
            self.assertIsNone(pipeline.inspect("GOVERNED-PROFILE-001"))
        finally:
            pipeline.close()

    def test_profile_tampering_fails_before_minah(self):
        profile = make_profile()
        approval = make_approval(profile)
        profile["policy_pack_version"] = "CHANGED-WITHOUT-APPROVAL"
        agent = MustNotRunMinah()
        pipeline = KinSysProtocolPipeline(
            minah_adapter=agent,
            minah_profile=profile,
            minah_profile_approval=approval,
            require_profile_approval=True,
            profile_signature_verifier=self.verifier,
            profile_revocation_checker=lambda approval_id: False,
        )
        try:
            with self.assertRaisesRegex(MinahApprovalViolation, "PROFILE_DIGEST_MISMATCH"):
                pipeline.execute(request())
            self.assertFalse(agent.called)
        finally:
            pipeline.close()

    def test_revocation_registry_outage_fails_before_minah(self):
        profile = make_profile()
        agent = MustNotRunMinah()
        pipeline = KinSysProtocolPipeline(
            minah_adapter=agent,
            minah_profile=profile,
            minah_profile_approval=make_approval(profile),
            require_profile_approval=True,
            profile_signature_verifier=self.verifier,
            profile_revocation_checker=lambda approval_id: (_ for _ in ()).throw(TimeoutError("offline")),
        )
        try:
            with self.assertRaisesRegex(MinahApprovalViolation, "REVOCATION_CHECK_UNAVAILABLE"):
                pipeline.execute(request())
            self.assertFalse(agent.called)
        finally:
            pipeline.close()

    def test_approved_profile_metadata_is_pinned_into_operation_fingerprint(self):
        profile = make_profile()
        approval = make_approval(profile)
        pipeline = KinSysProtocolPipeline(
            minah_profile=profile,
            minah_profile_approval=approval,
            require_profile_approval=True,
            profile_signature_verifier=self.verifier,
            profile_revocation_checker=lambda approval_id: False,
        )
        try:
            result = pipeline.execute(request())
            operation = pipeline.cee.get("GOVERNED-PROFILE-001")
            self.assertEqual(result["minah_disposition"]["deployment_profile"]["deployment_id"], "governed-minah-test")
            self.assertEqual(operation["request_json"]["_minah_profile_approval"]["approval_id"], "test-approval-001")
            verification = operation["request_json"]["_minah_profile_approval_verification"]
            self.assertEqual(verification["status"], "APPROVED")
            self.assertEqual(verification["profile_digest"], profile_digest(profile))
        finally:
            pipeline.close()


if __name__ == "__main__":
    unittest.main()
