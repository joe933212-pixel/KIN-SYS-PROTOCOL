# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import os
import tempfile
import unittest

from institutional_registry import InstitutionalRegistry, RegistryConflict, RegistryError


class InstitutionalRegistryTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        self.registry = InstitutionalRegistry(self.path)
        self.addCleanup(self.registry.close)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))
        self.payload = {"institution_id": "BANK-UG-01", "legal_name": "Example Bank"}

    def create(self, tenant="tenant-a", version="1.0"):
        return self.registry.create_record(
            tenant_id=tenant, resource_type="institution",
            resource_id="BANK-UG-01", version=version,
            status="PENDING_VERIFICATION", payload=self.payload)

    def test_record_is_retrievable_only_inside_owning_tenant(self):
        self.create()
        self.assertIsNotNone(self.registry.get_record(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01", version="1.0"))
        self.assertIsNone(self.registry.get_record(
            tenant_id="tenant-b", resource_type="institution",
            resource_id="BANK-UG-01", version="1.0"))
        self.assertEqual(self.registry.list_records(
            tenant_id="tenant-b", resource_type="institution"), [])

    def test_record_version_is_immutable_but_new_version_is_allowed(self):
        self.create()
        self.assertFalse(self.create()["created"])
        changed = {"institution_id": "BANK-UG-01", "legal_name": "Changed"}
        with self.assertRaisesRegex(RegistryConflict, "IMMUTABLE_VERSION_CONFLICT"):
            self.registry.create_record(
                tenant_id="tenant-a", resource_type="institution",
                resource_id="BANK-UG-01", version="1.0",
                status="PENDING_VERIFICATION", payload=changed)
        self.assertTrue(self.create(version="2.0")["created"])

    def test_lifecycle_events_are_tenant_scoped_and_idempotent(self):
        self.create()
        args = dict(tenant_id="tenant-a", event_id="evt-001",
                    resource_type="institution", resource_id="BANK-UG-01",
                    version="1.0", event_type="VERIFICATION_SUBMITTED",
                    actor_ref="authority:registrar", evidence_ref="evidence:001",
                    payload={"submission": "received"})
        self.assertTrue(self.registry.append_event(**args)["created"])
        self.assertFalse(self.registry.append_event(**args)["created"])
        self.assertEqual(len(self.registry.list_events(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01")), 1)
        self.assertEqual(self.registry.list_events(
            tenant_id="tenant-b", resource_type="institution",
            resource_id="BANK-UG-01"), [])

    def test_event_id_cannot_be_reused_for_different_content(self):
        self.create()
        args = dict(tenant_id="tenant-a", event_id="evt-001",
                    resource_type="institution", resource_id="BANK-UG-01",
                    version="1.0", event_type="VERIFICATION_SUBMITTED",
                    actor_ref="authority:registrar", payload={"submission": "received"})
        self.registry.append_event(**args)
        args["payload"] = {"submission": "altered"}
        with self.assertRaisesRegex(RegistryConflict, "EVENT_ID_REUSE_CONFLICT"):
            self.registry.append_event(**args)

    def test_event_cannot_reference_another_tenants_record(self):
        self.create()
        with self.assertRaisesRegex(RegistryError, "TENANT_SCOPED_PARENT_NOT_FOUND"):
            self.registry.append_event(
                tenant_id="tenant-b", event_id="evt-002",
                resource_type="institution", resource_id="BANK-UG-01",
                version="1.0", event_type="VERIFICATION_SUBMITTED",
                actor_ref="authority:registrar", payload={})

    def test_missing_tenant_context_is_rejected(self):
        with self.assertRaisesRegex(RegistryError, "TENANT_CONTEXT_REQUIRED"):
            self.registry.list_records(tenant_id="", resource_type="institution")

    def test_unsupported_resource_type_is_rejected(self):
        with self.assertRaisesRegex(RegistryError, "UNSUPPORTED_RESOURCE_TYPE"):
            self.registry.list_records(tenant_id="tenant-a", resource_type="secret")

    def test_noncanonical_values_are_rejected(self):
        with self.assertRaisesRegex(RegistryError, "VALUE_NOT_CANONICALISABLE"):
            self.registry.create_record(
                tenant_id="tenant-a", resource_type="institution",
                resource_id="BANK-UG-01", version="1.0",
                status="PENDING_VERIFICATION", payload={"not_json": object()})


if __name__ == "__main__":
    unittest.main()
