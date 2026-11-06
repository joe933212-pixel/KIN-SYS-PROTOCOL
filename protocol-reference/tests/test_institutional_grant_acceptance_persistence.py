# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import os
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from institutional_grant_provenance import sign_authority_grant
from institutional_trust_registry import (
    InstitutionalTrustRegistry, TrustRegistryConflict, TrustRegistryError,
)


class PersistentGrantAcceptanceTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        self.registry = InstitutionalTrustRegistry(self.path)
        self.addCleanup(self.registry.close)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))
        self.private_key = Ed25519PrivateKey.generate()
        self.public_bytes = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        self.grant = {
            "grant_id": "GRANT-ACCEPT-01", "tenant_id": "tenant-receiver",
            "status": "PENDING", "actor_ref": "actor:registrar",
            "institution_id": "BANK-UG-01", "authority_ref": "authority:registry",
            "roles": ["reviewer"], "resource_types": ["institution"],
            "resource_ids": ["BANK-UG-01"], "valid_from": 100,
            "valid_until": 300, "version": "1",
            "issuer_institution_id": "NATIONAL-REGISTRY",
            "approval_ref": "approval:issuer-01",
        }
        self.registry.register_trusted_issuer(
            tenant_id="tenant-receiver",
            issuer_institution_id="NATIONAL-REGISTRY",
            trust_basis_ref="trust-agreement:01", valid_from=100, valid_until=300,
            actor_ref="actor:trust-admin", evidence_ref="evidence:trust-01")
        self.registry.register_trust_key(
            tenant_id="tenant-receiver",
            issuer_institution_id="NATIONAL-REGISTRY", key_id="issuer-key-01",
            algorithm="Ed25519", public_key_ref="public:key-01",
            valid_from=100, valid_until=300,
            actor_ref="actor:trust-admin", evidence_ref="evidence:key-01")
        self.envelope = sign_authority_grant(
            self.grant, key_id="issuer-key-01", private_key=self.private_key)
        self.approval = {
            "actor_ref": "actor:bank-approver",
            "approval_ref": "approval:receiver-01",
        }

    def accept(self, **overrides):
        args = {
            "tenant_id": "tenant-receiver", "envelope": self.envelope,
            "receiver_institution_id": "BANK-UG-01",
            "receiver_approval": self.approval,
            "verify_receiver_approval": lambda grant, approval: approval == self.approval,
            "public_key_loader": lambda ref: self.public_bytes if ref == "public:key-01" else None,
            "event_id": "grant-accepted-01", "actor_ref": "actor:bank-approver",
            "evidence_ref": "approval:receiver-01", "clock": lambda: 150,
        }
        args.update(overrides)
        return self.registry.accept_signed_grant(**args)

    def test_acceptance_atomically_persists_active_grant_and_both_provenance_records(self):
        result = self.accept()
        self.assertTrue(result["accepted"])
        self.assertEqual(self.registry.load_grant("tenant-receiver", "GRANT-ACCEPT-01"),
                         dict(self.grant, status="ACTIVE"))
        provenance = self.registry.load_grant_provenance(
            tenant_id="tenant-receiver", grant_id="GRANT-ACCEPT-01", version="1")
        self.assertEqual(provenance["issuer_envelope"], self.envelope)
        self.assertEqual(provenance["acceptance"]["receiver_approval"], self.approval)
        events = self.registry.list_grant_events(
            tenant_id="tenant-receiver", grant_id="GRANT-ACCEPT-01")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "FEDERATED_GRANT_ACCEPTED")

    def test_receiver_refusal_leaves_no_grant_or_provenance(self):
        with self.assertRaisesRegex(TrustRegistryError, "SIGNED_GRANT_ACCEPTANCE_REJECTED"):
            self.accept(verify_receiver_approval=lambda grant, approval: False)
        self.assertIsNone(self.registry.load_grant("tenant-receiver", "GRANT-ACCEPT-01"))
        self.assertIsNone(self.registry.load_grant_provenance(
            tenant_id="tenant-receiver", grant_id="GRANT-ACCEPT-01", version="1"))

    def test_tenant_and_receiver_audit_context_are_enforced(self):
        with self.assertRaisesRegex(TrustRegistryError, "SIGNED_GRANT_ACCEPTANCE_REJECTED"):
            self.accept(tenant_id="tenant-other")
        with self.assertRaisesRegex(TrustRegistryError, "RECEIVER_APPROVAL_AUDIT_CONTEXT_MISMATCH"):
            self.accept(actor_ref="actor:spoofed")
        self.assertIsNone(self.registry.load_grant("tenant-receiver", "GRANT-ACCEPT-01"))

    def test_duplicate_event_rolls_back_grant_and_acceptance_bundle(self):
        self.registry.create_grant_version(
            tenant_id="tenant-receiver",
            grant=dict(self.grant, grant_id="EXISTING-GRANT", status="ACTIVE"),
            event_id="grant-accepted-01", actor_ref="actor:bank-approver",
            evidence_ref="approval:receiver-01")
        with self.assertRaises(Exception):
            self.accept()
        self.assertIsNone(self.registry.load_grant("tenant-receiver", "GRANT-ACCEPT-01"))
        self.assertIsNone(self.registry.load_grant_provenance(
            tenant_id="tenant-receiver", grant_id="GRANT-ACCEPT-01", version="1"))

    def test_acceptance_is_not_repeatable_as_a_second_initial_creation(self):
        self.accept()
        with self.assertRaisesRegex(TrustRegistryConflict, "AUTHORITY_GRANT_ALREADY_EXISTS"):
            self.accept(event_id="grant-accepted-02")


if __name__ == "__main__":
    unittest.main()
