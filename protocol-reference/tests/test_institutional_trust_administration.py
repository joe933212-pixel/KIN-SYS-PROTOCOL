# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Tests for authority-gated institutional trust administration."""
from __future__ import annotations

import os
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from institutional_grant_provenance import sign_authority_grant
from institutional_trust_administration import (
    InstitutionalTrustAdministration,
    InstitutionalTrustAdministrationError,
)
from institutional_trust_registry import InstitutionalTrustRegistry


class StubAuthorityResolver:
    def __init__(self):
        self.principal = {
            "authenticated": True,
            "verified": True,
            "actor_ref": "actor:local-admin",
            "institution_id": "BANK-UG-01",
            "authority_ref": "authority:local-trust",
            "grant_ref": "LOCAL-ADMIN@1",
            "roles": [],
            "assertion_algorithm": "HMAC-SHA256",
            "assertion_issuer_institution_id": None,
        }
        self.calls = []
        self.error = None

    def resolve(self, assertion, *, tenant_id, resource_type, resource_id):
        self.calls.append((assertion, tenant_id, resource_type, resource_id))
        if self.error is not None:
            raise self.error
        return dict(self.principal)


class StubEvidenceVerifier:
    def __init__(self):
        self.calls = []
        self.reject = False

    def verify(self, evidence_ref, *, context):
        self.calls.append((evidence_ref, dict(context)))
        if self.reject:
            return {"verified": False, "evidence_ref": evidence_ref}
        suffix = str(len(self.calls))
        return {
            "verified": True,
            "evidence_ref": evidence_ref,
            "verification_ref": "verification:" + evidence_ref + ":" + suffix,
            "evidence_digest": "sha256:" + ("a" * 64),
        }


class InstitutionalTrustAdministrationTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        self.registry = InstitutionalTrustRegistry(self.path)
        self.addCleanup(self.registry.close)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))

        self.authority = StubAuthorityResolver()
        self.evidence = StubEvidenceVerifier()
        self.issuer_private = Ed25519PrivateKey.generate()
        self.issuer_public = self.issuer_private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        self.receiver_private = Ed25519PrivateKey.generate()
        self.receiver_public = self.receiver_private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        self.receiver_key_record = {
            "tenant_id": "tenant-a",
            "receiver_institution_id": "BANK-UG-01",
            "key_id": "receiver-acceptance-key",
            "algorithm": "Ed25519",
            "status": "ACTIVE",
            "public_key_ref": "vault://receiver/public",
            "valid_from": 100,
            "valid_until": 2000,
        }
        self.public_keys = {"vault://issuer/public": self.issuer_public}
        self.signer_calls = []
        self.admin = self.make_admin()

    def make_admin(self, registry=None):
        def issuer_public_key_loader(ref):
            return self.public_keys.get(ref)

        def receiver_key_id_loader(tenant_id, institution_id):
            return "receiver-acceptance-key"

        def receiver_signing_key_loader(tenant_id, institution_id, key_id):
            self.signer_calls.append((tenant_id, institution_id, key_id))
            if (tenant_id, institution_id, key_id) != (
                "tenant-a", "BANK-UG-01", "receiver-acceptance-key"
            ):
                return None
            return self.receiver_private

        return InstitutionalTrustAdministration(
            registry=registry or self.registry,
            authority_resolver=self.authority,
            evidence_verifier=self.evidence,
            issuer_public_key_loader=issuer_public_key_loader,
            receiver_signing_key_id_loader=receiver_key_id_loader,
            receiver_signing_key_loader=receiver_signing_key_loader,
            clock=lambda: 150,
        )

    def local_grant(self, *, grant_id="LOCAL-01", version="1",
                    actor_ref="actor:beneficiary"):
        return {
            "grant_id": grant_id,
            "tenant_id": "tenant-a",
            "status": "ACTIVE",
            "actor_ref": actor_ref,
            "institution_id": "BANK-UG-01",
            "authority_ref": "authority:local-operations",
            "roles": ["payment_reviewer"],
            "resource_types": ["payment_instruction"],
            "resource_ids": ["PAY-001"],
            "valid_from": 100,
            "valid_until": 300,
            "version": version,
            "issuer_institution_id": "BANK-UG-01",
            "approval_ref": "approval:local-" + version,
        }

    def federated_pending_grant(self, *, grant_id="FED-01", version="1"):
        return {
            "grant_id": grant_id,
            "tenant_id": "tenant-a",
            "status": "PENDING",
            "actor_ref": "actor:federated-beneficiary",
            "institution_id": "BANK-UG-01",
            "authority_ref": "authority:cross-border",
            "roles": ["payment_initiator"],
            "resource_types": ["payment_instruction"],
            "resource_ids": ["PAY-UG-KE-001"],
            "valid_from": 100,
            "valid_until": 300,
            "version": version,
            "issuer_institution_id": "NATIONAL-REGISTRY",
            "approval_ref": "approval:issuer-" + version,
        }

    def create_local_grant(self):
        self.authority.principal["roles"] = ["authority_grant_issuer"]
        return self.admin.create_grant_version(
            tenant_id="tenant-a", grant=self.local_grant(),
            actor_assertion={"signed": "local"}, event_id="local-create-1",
            evidence_ref="evidence:local-create",
        )

    def establish_issuer_and_key(self):
        self.authority.principal["roles"] = ["institution_trust_establisher"]
        self.admin.establish_issuer_trust(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            trust_basis_ref="trust-agreement:national-registry",
            valid_from=100, valid_until=2000,
            actor_assertion={"signed": "local"},
            evidence_ref="evidence:issuer-trust",
        )
        self.authority.principal["roles"] = ["institution_trust_key_registrar"]
        self.admin.register_trust_key(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            key_id="issuer-key-1", algorithm="Ed25519",
            public_key_ref="vault://issuer/public",
            valid_from=100, valid_until=2000,
            actor_assertion={"signed": "local"},
            evidence_ref="evidence:issuer-key",
        )

    def test_local_grant_creation_uses_scoped_local_authority_and_verified_evidence(self):
        self.authority.principal["roles"] = ["authority_grant_issuer"]
        result = self.admin.create_grant_version(
            tenant_id="tenant-a", grant=self.local_grant(),
            actor_assertion={"signed": "local"}, event_id="local-create-1",
            evidence_ref="evidence:grant-approval",
        )
        self.assertTrue(result["created"])
        self.assertEqual(self.authority.calls[-1][1:], (
            "tenant-a", "authority_grant", "LOCAL-01",
        ))
        event = self.registry.list_grant_events(
            tenant_id="tenant-a", grant_id="LOCAL-01",
        )[0]
        self.assertEqual(event["actor_ref"], "actor:local-admin")
        self.assertEqual(
            event["evidence_ref"], result["evidence_verification_ref"],
        )
        self.assertNotEqual(event["actor_ref"], "caller-supplied-actor")

    def test_missing_role_and_untrusted_assertion_do_not_mutate_registry(self):
        with self.assertRaisesRegex(
            InstitutionalTrustAdministrationError, "TRUST_ADMIN_ROLE_INSUFFICIENT",
        ):
            self.admin.create_grant_version(
                tenant_id="tenant-a", grant=self.local_grant(),
                actor_assertion={"signed": "local"}, event_id="local-create-1",
                evidence_ref="evidence:grant-approval",
            )
        self.authority.error = ValueError("bad local assertion")
        self.authority.principal["roles"] = ["authority_grant_issuer"]
        with self.assertRaisesRegex(
            InstitutionalTrustAdministrationError, "TRUST_ADMIN_AUTHORITY_REJECTED",
        ):
            self.admin.create_grant_version(
                tenant_id="tenant-a", grant=self.local_grant(),
                actor_assertion={"signed": "bad"}, event_id="local-create-1",
                evidence_ref="evidence:grant-approval",
            )
        self.assertIsNone(self.registry.load_grant("tenant-a", "LOCAL-01"))

    def test_federated_assertion_cannot_administer_receiver_local_registry(self):
        self.authority.principal["roles"] = ["authority_grant_issuer"]
        self.authority.principal["assertion_algorithm"] = "Ed25519"
        self.authority.principal["assertion_issuer_institution_id"] = "OTHER-BANK"
        with self.assertRaisesRegex(
            InstitutionalTrustAdministrationError,
            "TRUST_ADMIN_REQUIRES_RECEIVER_LOCAL_ASSERTION",
        ):
            self.admin.create_grant_version(
                tenant_id="tenant-a", grant=self.local_grant(),
                actor_assertion={"signed": "federated"}, event_id="local-create-1",
                evidence_ref="evidence:grant-approval",
            )
        self.assertIsNone(self.registry.load_grant("tenant-a", "LOCAL-01"))

    def test_cross_institution_and_self_delegation_are_rejected(self):
        self.authority.principal["roles"] = ["authority_grant_issuer"]
        foreign = dict(
            self.local_grant(), institution_id="BANK-KE-01",
            issuer_institution_id="BANK-KE-01",
        )
        with self.assertRaisesRegex(
            InstitutionalTrustAdministrationError,
            "LOCAL_GRANT_INSTITUTION_OR_ISSUER_MISMATCH",
        ):
            self.admin.create_grant_version(
                tenant_id="tenant-a", grant=foreign,
                actor_assertion={"signed": "local"}, event_id="foreign-create",
                evidence_ref="evidence:foreign",
            )
        self_grant = self.local_grant(actor_ref="actor:local-admin")
        with self.assertRaisesRegex(
            InstitutionalTrustAdministrationError,
            "SELF_DELEGATION_REQUIRES_SEPARATE_APPROVAL",
        ):
            self.admin.create_grant_version(
                tenant_id="tenant-a", grant=self_grant,
                actor_assertion={"signed": "local"}, event_id="self-create",
                evidence_ref="evidence:self",
            )
        self.assertIsNone(self.registry.load_grant("tenant-a", "LOCAL-01"))

    def test_grant_revision_pins_head_and_preserves_beneficiary_and_issuer(self):
        self.create_local_grant()
        self.authority.principal["roles"] = ["authority_grant_amender"]
        revised = dict(
            self.local_grant(version="2"),
            roles=["payment_reviewer", "payment_approver"],
            approval_ref="approval:local-revision-2",
        )
        result = self.admin.create_grant_revision(
            tenant_id="tenant-a", grant_id="LOCAL-01", expected_version="1",
            grant=revised, actor_assertion={"signed": "local"},
            event_id="local-revision-2", evidence_ref="evidence:revision-2",
        )
        self.assertEqual(result["version"], "2")
        self.assertEqual(
            self.registry.load_grant("tenant-a", "LOCAL-01")["roles"],
            ["payment_reviewer", "payment_approver"],
        )
        changed_beneficiary = dict(revised, version="3", actor_ref="actor:other")
        with self.assertRaisesRegex(
            InstitutionalTrustAdministrationError,
            "GRANT_REVISION_IDENTITY_OR_VERSION_MISMATCH",
        ):
            self.admin.create_grant_revision(
                tenant_id="tenant-a", grant_id="LOCAL-01", expected_version="2",
                grant=changed_beneficiary, actor_assertion={"signed": "local"},
                event_id="local-revision-3", evidence_ref="evidence:revision-3",
            )
        with self.assertRaisesRegex(
            InstitutionalTrustAdministrationError, "STALE_GRANT_VERSION",
        ):
            self.admin.create_grant_revision(
                tenant_id="tenant-a", grant_id="LOCAL-01", expected_version="1",
                grant=dict(revised, version="3"),
                actor_assertion={"signed": "local"},
                event_id="stale-revision", evidence_ref="evidence:stale",
            )

    def test_grant_revocation_requires_distinct_role_and_advances_head(self):
        self.create_local_grant()
        with self.assertRaisesRegex(
            InstitutionalTrustAdministrationError, "TRUST_ADMIN_ROLE_INSUFFICIENT",
        ):
            self.admin.revoke_grant(
                tenant_id="tenant-a", grant_id="LOCAL-01", expected_version="1",
                new_version="2", actor_assertion={"signed": "local"},
                event_id="local-revoke-2", evidence_ref="evidence:revoke",
            )
        self.authority.principal["roles"] = ["authority_grant_revoker"]
        result = self.admin.revoke_grant(
            tenant_id="tenant-a", grant_id="LOCAL-01", expected_version="1",
            new_version="2", actor_assertion={"signed": "local"},
            event_id="local-revoke-2", evidence_ref="evidence:revoke",
        )
        current = self.registry.load_grant("tenant-a", "LOCAL-01")
        self.assertTrue(result["revoked"])
        self.assertEqual(current["status"], "REVOKED")
        self.assertEqual(current["version"], "2")
        self.assertEqual(
            self.registry.list_grant_events(
                tenant_id="tenant-a", grant_id="LOCAL-01",
            )[-1]["actor_ref"],
            "actor:local-admin",
        )

    def test_issuer_and_key_lifecycle_have_separate_roles_and_audited_evidence(self):
        self.authority.principal["roles"] = ["institution_trust_establisher"]
        self.admin.establish_issuer_trust(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            trust_basis_ref="trust-agreement:national-registry",
            valid_from=100, valid_until=2000,
            actor_assertion={"signed": "local"},
            evidence_ref="evidence:issuer-establishment",
        )
        self.authority.principal["roles"] = ["institution_trust_key_registrar"]
        self.admin.register_trust_key(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            key_id="issuer-key-1", algorithm="Ed25519",
            public_key_ref="vault://issuer/public",
            valid_from=100, valid_until=1000,
            actor_assertion={"signed": "local"},
            evidence_ref="evidence:key-one",
        )
        self.admin.register_trust_key(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            key_id="issuer-key-2", algorithm="Ed25519",
            public_key_ref="vault://issuer/public-2",
            valid_from=900, valid_until=1800,
            actor_assertion={"signed": "local"},
            evidence_ref="evidence:key-two",
        )
        self.assertEqual(
            self.registry.load_trust_key("tenant-a", "issuer-key-1")["status"],
            "ACTIVE",
        )
        self.assertEqual(
            self.registry.load_trust_key("tenant-a", "issuer-key-2")["status"],
            "ACTIVE",
        )
        self.authority.principal["roles"] = ["institution_trust_key_revoker"]
        self.admin.revoke_trust_key(
            tenant_id="tenant-a", key_id="issuer-key-1",
            actor_assertion={"signed": "local"}, event_id="key-one-revoked",
            evidence_ref="evidence:key-one-revocation",
        )
        self.assertIsNone(self.registry.load_trust_key("tenant-a", "issuer-key-1"))
        self.assertIsNotNone(self.registry.load_trust_key("tenant-a", "issuer-key-2"))
        self.authority.principal["roles"] = ["institution_trust_revoker"]
        self.admin.revoke_issuer_trust(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            actor_assertion={"signed": "local"}, event_id="issuer-revoked",
            evidence_ref="evidence:issuer-revocation",
        )
        self.assertIsNone(self.registry.load_trust_key("tenant-a", "issuer-key-2"))
        events = self.registry.list_trusted_issuer_events(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
        )
        self.assertEqual(events[-1]["event_type"], "ISSUER_TRUST_REVOKED")

    def test_evidence_refusal_prevents_registry_mutation(self):
        self.authority.principal["roles"] = ["authority_grant_issuer"]
        self.evidence.reject = True
        with self.assertRaisesRegex(
            InstitutionalTrustAdministrationError, "TRUST_ADMIN_EVIDENCE_NOT_VERIFIED",
        ):
            self.admin.create_grant_version(
                tenant_id="tenant-a", grant=self.local_grant(),
                actor_assertion={"signed": "local"}, event_id="evidence-rejected",
                evidence_ref="evidence:unverified",
            )
        self.assertIsNone(self.registry.load_grant("tenant-a", "LOCAL-01"))

    def test_federated_grant_acceptance_uses_local_authority_and_receiver_receipt(self):
        self.establish_issuer_and_key()
        self.authority.principal["roles"] = ["federated_grant_acceptor"]
        pending = self.federated_pending_grant()
        envelope = sign_authority_grant(
            pending, key_id="issuer-key-1", private_key=self.issuer_private,
        )
        result = self.admin.accept_federated_grant(
            tenant_id="tenant-a", envelope=envelope,
            receiver_institution_id="BANK-UG-01",
            actor_assertion={"signed": "local"},
            event_id="federated-accept-1",
            evidence_ref="evidence:receiver-acceptance",
        )
        self.assertTrue(result["accepted"])
        self.assertEqual(result["grant"]["status"], "ACTIVE")
        self.assertEqual(result["actor_ref"], "actor:local-admin")
        self.assertEqual(len(self.signer_calls), 1)
        provenance = self.registry.load_grant_provenance(
            tenant_id="tenant-a", grant_id="FED-01", version="1",
        )
        self.assertEqual(
            provenance["acceptance"]["receiver_approval"]["evidence_verification"]
            ["verification_ref"],
            result["evidence_verification_ref"],
        )
        verified = self.registry.load_verified_grant(
            "tenant-a", "FED-01", require_federated_provenance=True,
            public_key_loader=lambda ref: self.public_keys.get(ref),
            receiver_acceptance_key_loader=lambda tenant, institution, key: (
                dict(self.receiver_key_record)
                if (tenant, institution, key) == (
                    "tenant-a", "BANK-UG-01", "receiver-acceptance-key",
                ) else None
            ),
            receiver_acceptance_public_key_loader=lambda ref: (
                self.receiver_public if ref == "vault://receiver/public" else None
            ),
            clock=lambda: 150,
        )
        self.assertEqual(verified["version"], "1")
        self.assertEqual(verified["status"], "ACTIVE")

    def test_federated_acceptance_rejects_foreign_receiver_administrator(self):
        self.establish_issuer_and_key()
        self.authority.principal["roles"] = ["federated_grant_acceptor"]
        self.authority.principal["institution_id"] = "BANK-KE-01"
        envelope = sign_authority_grant(
            self.federated_pending_grant(),
            key_id="issuer-key-1", private_key=self.issuer_private,
        )
        with self.assertRaisesRegex(
            InstitutionalTrustAdministrationError,
            "FEDERATED_GRANT_RECEIVER_ADMINISTRATOR_MISMATCH",
        ):
            self.admin.accept_federated_grant(
                tenant_id="tenant-a", envelope=envelope,
                receiver_institution_id="BANK-UG-01",
                actor_assertion={"signed": "foreign-local"},
                event_id="foreign-accept", evidence_ref="evidence:foreign-accept",
            )
        self.assertIsNone(self.registry.load_grant("tenant-a", "FED-01"))
        self.assertEqual(self.signer_calls, [])

    def test_federated_revision_wrapper_pins_current_head_and_binds_approval(self):
        self.establish_issuer_and_key()
        self.authority.principal["roles"] = ["federated_grant_acceptor"]
        first = self.federated_pending_grant()
        first_envelope = sign_authority_grant(
            first, key_id="issuer-key-1", private_key=self.issuer_private,
        )
        self.admin.accept_federated_grant(
            tenant_id="tenant-a", envelope=first_envelope,
            receiver_institution_id="BANK-UG-01",
            actor_assertion={"signed": "local"},
            event_id="federated-accept-1",
            evidence_ref="evidence:receiver-acceptance-1",
        )
        revised = self.federated_pending_grant(version="2")
        revised["approval_ref"] = "approval:issuer-2"
        revised_envelope = sign_authority_grant(
            revised, key_id="issuer-key-1", private_key=self.issuer_private,
        )
        captured = {}

        def accept_revision(**kwargs):
            captured.update(kwargs)
            return {"accepted": True, "revised": True, "version": "2"}

        self.registry.accept_signed_grant_revision = accept_revision
        result = self.admin.accept_federated_grant_revision(
            tenant_id="tenant-a", grant_id="FED-01", expected_version="1",
            envelope=revised_envelope, receiver_institution_id="BANK-UG-01",
            actor_assertion={"signed": "local"},
            event_id="federated-revision-2",
            evidence_ref="evidence:receiver-acceptance-2",
        )
        self.assertTrue(result["revised"])
        self.assertEqual(captured["actor_ref"], "actor:local-admin")
        self.assertEqual(captured["expected_version"], "1")
        self.assertTrue(captured["verify_receiver_approval"](
            revised, captured["receiver_approval"],
        ))
        self.assertFalse(captured["verify_receiver_approval"](
            dict(revised, roles=["different_role"]),
            captured["receiver_approval"],
        ))



    def test_invalid_issuer_signature_is_rejected_before_evidence_or_signer(self):
        self.establish_issuer_and_key()
        self.authority.principal["roles"] = ["federated_grant_acceptor"]
        envelope = sign_authority_grant(
            self.federated_pending_grant(),
            key_id="issuer-key-1", private_key=self.issuer_private,
        )
        envelope["signature"] = "A" * 86
        evidence_calls = len(self.evidence.calls)
        signer_calls = len(self.signer_calls)
        with self.assertRaisesRegex(
            InstitutionalTrustAdministrationError,
            "FEDERATED_GRANT_ISSUER_PROVENANCE_INVALID",
        ):
            self.admin.accept_federated_grant(
                tenant_id="tenant-a", envelope=envelope,
                receiver_institution_id="BANK-UG-01",
                actor_assertion={"signed": "local"},
                event_id="bad-signature-accept",
                evidence_ref="evidence:bad-signature",
            )
        self.assertEqual(len(self.evidence.calls), evidence_calls)
        self.assertEqual(len(self.signer_calls), signer_calls)
        self.assertIsNone(self.registry.load_grant("tenant-a", "FED-01"))

    def test_sqlite_backend_reports_unsupported_federated_revision(self):
        self.assertFalse(callable(getattr(self.registry, "accept_signed_grant_revision", None)))
        with self.assertRaisesRegex(
            InstitutionalTrustAdministrationError,
            "FEDERATED_GRANT_REVISION_NOT_SUPPORTED_BY_BACKEND",
        ):
            self.admin.accept_federated_grant_revision(
                tenant_id="tenant-a", grant_id="FED-01", expected_version="1",
                envelope={}, receiver_institution_id="BANK-UG-01",
                actor_assertion={"signed": "local"},
                event_id="unsupported-revision", evidence_ref="evidence:unused",
            )


if __name__ == "__main__":
    unittest.main()
