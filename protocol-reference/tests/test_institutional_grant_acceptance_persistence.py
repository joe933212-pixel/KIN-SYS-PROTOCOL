# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import json
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
        self.receiver_private_key = Ed25519PrivateKey.generate()
        self.receiver_public_bytes = self.receiver_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        self.receiver_key_record = {
            "tenant_id": "tenant-receiver",
            "receiver_institution_id": "BANK-UG-01",
            "key_id": "receiver-key-01",
            "algorithm": "Ed25519",
            "status": "ACTIVE",
            "public_key_ref": "public:receiver-key-01",
            "valid_from": 100,
            "valid_until": 300,
        }
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

    def receiver_signing_key_loader(self, tenant_id, institution_id, key_id):
        if (tenant_id, institution_id, key_id) == (
            "tenant-receiver", "BANK-UG-01", "receiver-key-01"
        ):
            return self.receiver_private_key
        return None

    def receiver_acceptance_key_loader(self, tenant_id, institution_id, key_id):
        if (tenant_id, institution_id, key_id) == (
            "tenant-receiver", "BANK-UG-01", "receiver-key-01"
        ):
            return dict(self.receiver_key_record)
        return None

    def receiver_acceptance_public_key_loader(self, public_key_ref):
        return (self.receiver_public_bytes
                if public_key_ref == "public:receiver-key-01" else None)

    def load_verified(self, **overrides):
        args = {
            "tenant_id": "tenant-receiver",
            "grant_id": "GRANT-ACCEPT-01",
            "require_federated_provenance": True,
            "public_key_loader": lambda ref: (
                self.public_bytes if ref == "public:key-01" else None),
            "receiver_acceptance_key_loader": self.receiver_acceptance_key_loader,
            "receiver_acceptance_public_key_loader": self.receiver_acceptance_public_key_loader,
            "clock": lambda: 150,
        }
        args.update(overrides)
        return self.registry.load_verified_grant(**args)

    def accept(self, **overrides):
        args = {
            "tenant_id": "tenant-receiver", "envelope": self.envelope,
            "receiver_institution_id": "BANK-UG-01",
            "receiver_approval": self.approval,
            "verify_receiver_approval": lambda grant, approval: approval == self.approval,
            "public_key_loader": lambda ref: self.public_bytes if ref == "public:key-01" else None,
            "event_id": "grant-accepted-01", "actor_ref": "actor:bank-approver",
            "evidence_ref": "approval:receiver-01",
            "receiver_signing_key_id": "receiver-key-01",
            "receiver_signing_key_loader": self.receiver_signing_key_loader,
            "clock": lambda: 150,
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
        self.assertEqual(
            provenance["acceptance"]["receiver_receipt"]["claims"]["key_id"],
            "receiver-key-01",
        )
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



    def test_verified_grant_store_revalidates_issuer_signature_digest_and_audit(self):
        self.accept()
        loaded = self.load_verified()
        self.assertEqual(loaded, dict(self.grant, status="ACTIVE"))

        self.registry.db.execute(
            """UPDATE accepted_grant_provenance SET grant_digest=?
               WHERE tenant_id=? AND grant_id=? AND version=?""",
            ("0" * 64, "tenant-receiver", "GRANT-ACCEPT-01", "1"),
        )
        with self.assertRaisesRegex(TrustRegistryError, "GRANT_PROVENANCE_DIGEST_MISMATCH"):
            self.load_verified()

    def test_federated_resolution_rejects_active_grant_without_signed_provenance(self):
        self.registry.create_grant_version(
            tenant_id="tenant-receiver",
            grant=dict(self.grant, grant_id="LOCAL-GRANT-01", status="ACTIVE"),
            event_id="local-grant-created", actor_ref="actor:local-approver",
            evidence_ref="evidence:local-approval",
        )
        with self.assertRaisesRegex(TrustRegistryError, "FEDERATED_GRANT_PROVENANCE_REQUIRED"):
            self.load_verified(grant_id="LOCAL-GRANT-01")

    def test_federated_grant_revision_requires_fresh_signed_provenance(self):
        self.accept()
        current = self.registry.load_grant("tenant-receiver", "GRANT-ACCEPT-01")
        revised = dict(current, version="2", roles=["reviewer", "approver"])
        self.registry.create_grant_revision(
            tenant_id="tenant-receiver", grant_id="GRANT-ACCEPT-01",
            expected_version="1", grant=revised,
            event_id="grant-revised-02", actor_ref="actor:bank-approver",
            evidence_ref="approval:grant-revision",
        )
        with self.assertRaisesRegex(TrustRegistryError, "FEDERATED_GRANT_PROVENANCE_REQUIRED"):
            self.load_verified()

    def test_issuer_trust_revocation_invalidates_previously_accepted_grant_provenance(self):
        self.accept()
        self.registry.revoke_trusted_issuer(
            tenant_id="tenant-receiver",
            issuer_institution_id="NATIONAL-REGISTRY",
            actor_ref="actor:trust-admin", evidence_ref="evidence:trust-revocation",
            event_id="issuer-trust-revoked",
        )
        with self.assertRaisesRegex(TrustRegistryError, "GRANT_PROVENANCE_VERIFICATION_FAILED"):
            self.load_verified()

    def test_acceptance_audit_context_tampering_is_detected(self):
        self.accept()
        row = self.registry.db.execute(
            """SELECT acceptance_json FROM accepted_grant_provenance
               WHERE tenant_id=? AND grant_id=? AND version=?""",
            ("tenant-receiver", "GRANT-ACCEPT-01", "1"),
        ).fetchone()
        acceptance = json.loads(row["acceptance_json"])
        acceptance["receiver_approval"] = {
            "actor_ref": "actor:attacker", "approval_ref": "approval:forged",
        }
        self.registry.db.execute(
            """UPDATE accepted_grant_provenance SET acceptance_json=?
               WHERE tenant_id=? AND grant_id=? AND version=?""",
            (json.dumps(acceptance, sort_keys=True, separators=(",", ":")),
             "tenant-receiver", "GRANT-ACCEPT-01", "1"),
        )
        event = self.registry.db.execute(
            """SELECT event_id,payload_json FROM authority_grant_events
               WHERE tenant_id=? AND grant_id=? AND version=?""",
            ("tenant-receiver", "GRANT-ACCEPT-01", "1"),
        ).fetchone()
        payload = json.loads(event["payload_json"])
        payload["acceptance"] = acceptance
        self.registry.db.execute(
            """UPDATE authority_grant_events SET actor_ref=?,evidence_ref=?,payload_json=?
               WHERE tenant_id=? AND event_id=?""",
            ("actor:attacker", "approval:forged",
             json.dumps(payload, sort_keys=True, separators=(",", ":")),
             "tenant-receiver", event["event_id"]),
        )
        with self.assertRaisesRegex(
            TrustRegistryError, "GRANT_ACCEPTANCE_RECEIPT_INVALID:RECEIVER_ACCEPTANCE_RECEIPT_SCOPE_MISMATCH"
        ):
            self.load_verified()



    def test_federated_acceptance_requires_receiver_signing_and_verification_keys(self):
        with self.assertRaisesRegex(TrustRegistryError, "RECEIVER_ACCEPTANCE_SIGNER_REQUIRED"):
            self.accept(receiver_signing_key_loader=None)
        self.accept()
        with self.assertRaisesRegex(
            TrustRegistryError, "RECEIVER_ACCEPTANCE_TRUST_CONFIGURATION_INCOMPLETE"
        ):
            self.registry.load_verified_grant(
                "tenant-receiver", "GRANT-ACCEPT-01",
                require_federated_provenance=True,
                public_key_loader=lambda ref: self.public_bytes,
                clock=lambda: 150,
            )

    def test_receiver_acceptance_key_revocation_invalidates_retained_receipt(self):
        self.accept()
        self.receiver_key_record["status"] = "REVOKED"
        with self.assertRaisesRegex(
            TrustRegistryError, "GRANT_ACCEPTANCE_RECEIPT_INVALID:RECEIVER_ACCEPTANCE_KEY_NOT_ACTIVE"
        ):
            self.load_verified()




    def test_receiver_signer_is_not_resolved_until_receiver_approval_succeeds(self):
        order = []

        def approve(grant, supplied):
            order.append("approval")
            return supplied == self.approval

        def signing_key(tenant_id, institution_id, key_id):
            order.append("signer")
            return self.receiver_private_key

        self.accept(
            verify_receiver_approval=approve,
            receiver_signing_key_loader=signing_key,
        )
        self.assertEqual(order, ["approval", "signer"])

        order.clear()
        with self.assertRaisesRegex(TrustRegistryError, "SIGNED_GRANT_ACCEPTANCE_REJECTED"):
            self.accept(
                verify_receiver_approval=lambda grant, supplied: (
                    order.append("approval") or False
                ),
                receiver_signing_key_loader=signing_key,
            )
        self.assertEqual(order, ["approval"])


if __name__ == "__main__":
    unittest.main()
