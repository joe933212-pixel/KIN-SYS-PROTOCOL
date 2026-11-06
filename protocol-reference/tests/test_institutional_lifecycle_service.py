# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import unittest

from institutional_lifecycle_service import LifecycleViolation, apply_transition


class InstitutionalLifecycleServiceTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            "institution_id": "BANK-UG-01",
            "legal_name": "Example Bank",
            "jurisdiction_ref": "UG",
            "status": "PENDING_VERIFICATION",
            "verification_ref": None,
        }
        self.external_verifier = {
            "actor_ref": "actor:registrar-7",
            "institution_id": "REGULATOR-UG",
            "authenticated": True,
            "verified": True,
            "authority_ref": "authority:institution-register",
            "roles": ["institution_verifier"],
        }

    def transition(self, actor=None, **kwargs):
        return apply_transition(
            "institution", self.record, "VERIFIED",
            actor or self.external_verifier,
            evidence_ref="evidence:registration-check",
            approval_ref="approval:verification-42",
            expected_version="1.0",
            **kwargs,
        )

    def test_authorised_external_actor_can_verify_and_event_is_bound(self):
        result = self.transition()
        self.assertEqual(result["record"]["status"], "VERIFIED")
        self.assertEqual(result["record"]["verification_ref"], "approval:verification-42")
        self.assertEqual(result["event"]["resource_id"], "BANK-UG-01")
        self.assertEqual(result["event"]["expected_version"], "1.0")
        self.assertEqual(self.record["status"], "PENDING_VERIFICATION")

    def test_institution_cannot_verify_itself(self):
        actor = dict(self.external_verifier, institution_id="BANK-UG-01")
        with self.assertRaisesRegex(LifecycleViolation, "SELF_APPROVAL_PROHIBITED"):
            self.transition(actor)

    def test_wrong_role_cannot_verify(self):
        actor = dict(self.external_verifier, roles=["institution_submitter"])
        with self.assertRaisesRegex(LifecycleViolation, "REQUIRED_AUTHORITY_ROLE_MISSING"):
            self.transition(actor)

    def test_unverified_or_unauthenticated_actor_is_rejected(self):
        actor = dict(self.external_verifier, verified=False)
        with self.assertRaisesRegex(LifecycleViolation, "VERIFIED_AUTHENTICATED_ACTOR_REQUIRED"):
            self.transition(actor)

    def test_missing_authority_evidence_is_rejected(self):
        with self.assertRaisesRegex(LifecycleViolation, "TRANSITION_EVIDENCE_REQUIRED"):
            apply_transition("institution", self.record, "VERIFIED",
                self.external_verifier, evidence_ref=None,
                approval_ref="approval:1")

    def test_missing_approval_reference_is_rejected(self):
        with self.assertRaisesRegex(LifecycleViolation, "APPROVAL_REFERENCE_REQUIRED"):
            apply_transition("institution", self.record, "VERIFIED",
                self.external_verifier, evidence_ref="evidence:1")

    def test_illegal_transition_cannot_skip_verification(self):
        with self.assertRaisesRegex(LifecycleViolation, "TRANSITION_NOT_PERMITTED"):
            apply_transition("institution", self.record, "ACTIVE",
                self.external_verifier, evidence_ref="evidence:1",
                approval_ref="approval:1")

    def test_suspension_requires_dedicated_authority_and_evidence(self):
        actor = dict(self.external_verifier, roles=["institution_suspension_authority"])
        result = apply_transition("institution", self.record, "SUSPENDED",
            actor, evidence_ref="evidence:incident")
        self.assertEqual(result["record"]["status"], "SUSPENDED")
        actor["roles"] = ["institution_verifier"]
        with self.assertRaisesRegex(LifecycleViolation, "REQUIRED_AUTHORITY_ROLE_MISSING"):
            apply_transition("institution", self.record, "SUSPENDED",
                actor, evidence_ref="evidence:incident")

    def test_revocation_is_terminal(self):
        actor = dict(self.external_verifier, roles=["institution_revocation_authority"])
        result = apply_transition("institution", self.record, "REVOKED",
            actor, evidence_ref="evidence:revocation")
        with self.assertRaisesRegex(LifecycleViolation, "TRANSITION_NOT_PERMITTED"):
            apply_transition("institution", result["record"], "VERIFIED",
                self.external_verifier, evidence_ref="evidence:1",
                approval_ref="approval:1")

    def test_jurisdiction_approval_requires_external_approver(self):
        package = {
            "package_id": "UG-PACK", "owner_institution_id": "BANK-UG-01",
            "status": "VALIDATED",
        }
        actor = {
            "actor_ref": "actor:regulator", "institution_id": "REGULATOR-UG",
            "authenticated": True, "verified": True,
            "authority_ref": "authority:jurisdiction",
            "roles": ["jurisdiction_approver"],
        }
        result = apply_transition("jurisdiction", package, "APPROVED", actor,
            evidence_ref="evidence:review", approval_ref="approval:package",
            expected_version="2.1")
        self.assertEqual(result["record"]["status"], "APPROVED")
        actor["institution_id"] = "BANK-UG-01"
        with self.assertRaisesRegex(LifecycleViolation, "SELF_APPROVAL_PROHIBITED"):
            apply_transition("jurisdiction", package, "APPROVED", actor,
                evidence_ref="evidence:review", approval_ref="approval:package")

    def test_conformance_requires_assessor_role(self):
        deployment = {
            "deployment_id": "MINAH-1", "owner_institution_id": "BANK-UG-01",
            "status": "CONNECTIVITY_VERIFIED",
        }
        actor = {
            "actor_ref": "actor:tester", "institution_id": "TEST-LAB",
            "authenticated": True, "verified": True,
            "authority_ref": "authority:test-lab",
            "roles": ["minah_conformance_assessor"],
        }
        result = apply_transition("minah", deployment, "CONFORMANCE_PASSED",
            actor, evidence_ref="evidence:test-suite-v3")
        self.assertEqual(result["record"]["status"], "CONFORMANCE_PASSED")

    def test_binding_activation_requires_activation_authority(self):
        binding = {
            "binding_id": "BIND-1", "owner_institution_id": "BANK-UG-01",
            "status": "APPROVED",
        }
        actor = {
            "actor_ref": "actor:activation-officer", "institution_id": "REGULATOR-UG",
            "authenticated": True, "verified": True,
            "authority_ref": "authority:deployment-activation",
            "roles": ["binding_activation_authority"],
        }
        result = apply_transition("binding", binding, "ACTIVE", actor,
            evidence_ref="evidence:activation-check",
            approval_ref="approval:activation")
        self.assertEqual(result["record"]["status"], "ACTIVE")


if __name__ == "__main__":
    unittest.main()
