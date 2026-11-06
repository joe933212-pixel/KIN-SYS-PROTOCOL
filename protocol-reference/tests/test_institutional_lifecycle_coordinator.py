# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import os
import tempfile
import unittest

from institutional_lifecycle_coordinator import (
    InstitutionalLifecycleCoordinator, LifecycleCoordinatorError,
)
from institutional_registry import InstitutionalRegistry


class InstitutionalLifecycleCoordinatorTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        self.registry = InstitutionalRegistry(self.path)
        self.addCleanup(self.registry.close)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))
        self.coordinator = InstitutionalLifecycleCoordinator(self.registry)
        self.institution = {
            "institution_id": "BANK-UG-01",
            "legal_name": "Example Bank",
            "jurisdiction_ref": "UG",
            "status": "PENDING_VERIFICATION",
            "verification_ref": None,
        }
        self.registry.create_record(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01", version="1.0",
            status="PENDING_VERIFICATION", payload=self.institution,
        )
        self.actor = {
            "actor_ref": "actor:registrar",
            "institution_id": "REGULATOR-UG",
            "authenticated": True,
            "verified": True,
            "authority_ref": "authority:institution-register",
            "roles": ["institution_verifier"],
        }

    def call_transition(self, **overrides):
        args = dict(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01", expected_version="1.0",
            new_version="2.0", event_id="evt-verify-1",
            target_status="VERIFIED", actor=self.actor,
            evidence_ref="evidence:registry-check",
            approval_ref="approval:verify-1",
        )
        args.update(overrides)
        return self.coordinator.transition(**args)

    def test_authorised_transition_persists_snapshot_and_event(self):
        result = self.call_transition()
        self.assertTrue(result["created"])
        snapshot = self.registry.get_record(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01", version="2.0",
        )
        self.assertEqual(snapshot["status"], "VERIFIED")
        self.assertEqual(snapshot["payload"]["verification_ref"], "approval:verify-1")
        events = self.registry.list_events(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "INSTITUTION_VERIFIED")

    def test_wrong_authority_fails_without_writing(self):
        actor = dict(self.actor, roles=["institution_submitter"])
        with self.assertRaisesRegex(ValueError, "REQUIRED_AUTHORITY_ROLE_MISSING"):
            self.call_transition(actor=actor)
        self.assertIsNone(self.registry.get_record(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01", version="2.0",
        ))
        self.assertEqual(self.registry.list_events(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01",
        ), [])

    def test_tenant_context_does_not_allow_cross_tenant_read_or_write(self):
        with self.assertRaisesRegex(LifecycleCoordinatorError, "CURRENT_RECORD_VERSION_NOT_FOUND"):
            self.call_transition(tenant_id="tenant-b")
        self.assertEqual(self.registry.list_events(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01",
        ), [])

    def test_stale_expected_version_fails_without_partial_write(self):
        self.call_transition()
        with self.assertRaisesRegex(ValueError, "CURRENT_RECORD_VERSION_NOT_FOUND"):
            self.call_transition(new_version="3.0", event_id="evt-stale")
        self.assertIsNone(self.registry.get_record(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01", version="3.0",
        ))

    def test_binding_activation_requires_complete_activation_context(self):
        binding = {
            "binding_id": "BIND-1",
            "owner_institution_id": "BANK-UG-01",
            "jurisdiction_package_id": "UG-PACK",
            "jurisdiction_package_version": "1.0",
            "minah_deployment_id": "MINAH-1",
            "policy_pack_version": "1.0",
            "source_pack_version": "1.0",
            "authority_profile_version": "1.0",
            "status": "APPROVED",
            "approval_ref": "approval:binding",
        }
        self.registry.create_record(
            tenant_id="tenant-a", resource_type="binding",
            resource_id="BIND-1", version="1.0", status="APPROVED",
            payload=binding,
        )
        actor = {
            "actor_ref": "actor:activation",
            "institution_id": "REGULATOR-UG",
            "authenticated": True, "verified": True,
            "authority_ref": "authority:binding-activation",
            "roles": ["binding_activation_authority"],
        }
        with self.assertRaisesRegex(LifecycleCoordinatorError, "ACTIVATION_CONTEXT_REQUIRED"):
            self.coordinator.transition(
                tenant_id="tenant-a", resource_type="binding",
                resource_id="BIND-1", expected_version="1.0",
                new_version="2.0", event_id="evt-activate",
                target_status="ACTIVE", actor=actor,
                evidence_ref="evidence:activation",
                approval_ref="approval:activation",
            )
        self.assertIsNone(self.registry.get_record(
            tenant_id="tenant-a", resource_type="binding",
            resource_id="BIND-1", version="2.0",
        ))


if __name__ == "__main__":
    unittest.main()
