# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from institutional_grant_provenance import (
    GrantProvenanceError, accept_authority_grant, sign_authority_grant,
    verify_authority_grant,
)


class IssuerSignedGrantTests(unittest.TestCase):
    def setUp(self):
        self.private_key = Ed25519PrivateKey.generate()
        self.public_bytes = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        self.grant = {
            "grant_id": "GRANT-FED-01", "tenant_id": "tenant-receiver",
            "status": "PENDING", "actor_ref": "actor:registrar",
            "institution_id": "BANK-UG-01", "authority_ref": "authority:registry",
            "roles": ["reviewer"], "resource_types": ["institution"],
            "resource_ids": ["BANK-UG-01"], "valid_from": 100,
            "valid_until": 300, "version": "1",
            "issuer_institution_id": "NATIONAL-REGISTRY",
            "approval_ref": "approval:issuer-01",
        }
        self.key_record = {
            "tenant_id": "tenant-receiver", "key_id": "issuer-key-01",
            "issuer_institution_id": "NATIONAL-REGISTRY", "algorithm": "Ed25519",
            "status": "ACTIVE", "public_key_ref": "public:key-01",
            "valid_from": 100, "valid_until": 300,
            "issuer_valid_from": 100, "issuer_valid_until": 300,
        }
        self.envelope = sign_authority_grant(
            self.grant, key_id="issuer-key-01", private_key=self.private_key)

    def trust_key(self, tenant_id, key_id):
        if tenant_id == "tenant-receiver" and key_id == "issuer-key-01":
            return self.key_record
        return None

    def public_key(self, ref):
        return self.public_bytes if ref == "public:key-01" else None

    def verify(self, envelope=None, tenant="tenant-receiver", key_loader=None):
        return verify_authority_grant(
            envelope or self.envelope, tenant_id=tenant,
            trust_key_loader=key_loader or self.trust_key,
            public_key_loader=self.public_key, clock=lambda: 150)

    def test_issuer_signature_verifies_against_receiver_trust_root(self):
        self.assertEqual(self.verify(), self.grant)

    def test_receiver_acceptance_is_separate_and_activates_exact_grant(self):
        accepted = accept_authority_grant(
            self.envelope, tenant_id="tenant-receiver",
            receiver_institution_id="BANK-UG-01",
            trust_key_loader=self.trust_key, public_key_loader=self.public_key,
            verify_receiver_approval=lambda grant, approval: (
                approval == {"actor_ref": "actor:bank-approver", "approval_ref": "bank:approval-9"}
                and grant["grant_id"] == "GRANT-FED-01"),
            receiver_approval={"actor_ref": "actor:bank-approver", "approval_ref": "bank:approval-9"},
            clock=lambda: 150)
        self.assertEqual(accepted["grant"]["status"], "ACTIVE")
        self.assertEqual(accepted["acceptance"]["grant_version"], "1")
        self.assertEqual(accepted["acceptance"]["receiver_institution_id"], "BANK-UG-01")
        self.assertEqual(accepted["issuer_envelope"], self.envelope)

    def test_issuer_signature_alone_does_not_accept_grant(self):
        with self.assertRaisesRegex(GrantProvenanceError, "RECEIVER_APPROVAL_REQUIRED"):
            accept_authority_grant(
                self.envelope, tenant_id="tenant-receiver",
                receiver_institution_id="BANK-UG-01",
                trust_key_loader=self.trust_key, public_key_loader=self.public_key,
                verify_receiver_approval=lambda grant, approval: False,
                receiver_approval={}, clock=lambda: 150)

    def test_wrong_receiver_is_rejected(self):
        with self.assertRaisesRegex(GrantProvenanceError, "GRANT_RECEIVER_INSTITUTION_MISMATCH"):
            accept_authority_grant(
                self.envelope, tenant_id="tenant-receiver",
                receiver_institution_id="BANK-KE-01",
                trust_key_loader=self.trust_key, public_key_loader=self.public_key,
                verify_receiver_approval=lambda grant, approval: True,
                receiver_approval={"approval_ref": "approval:01"}, clock=lambda: 150)

    def test_tampered_grant_claim_fails_signature_verification(self):
        tampered = {"claims": dict(self.envelope["claims"]),
                    "signature": self.envelope["signature"]}
        tampered["claims"]["grant"] = dict(self.grant, roles=["administrator"])
        with self.assertRaisesRegex(GrantProvenanceError, "ASSERTION_SIGNATURE_INVALID"):
            self.verify(tampered)

    def test_unknown_or_wrong_issuer_key_fails_closed(self):
        with self.assertRaisesRegex(GrantProvenanceError, "UNTRUSTED_GRANT_ISSUER_KEY"):
            self.verify(key_loader=lambda tenant, key: None)
        wrong = dict(self.key_record, issuer_institution_id="OTHER-ISSUER")
        with self.assertRaisesRegex(GrantProvenanceError, "GRANT_TRUST_KEY_ISSUER_MISMATCH"):
            self.verify(key_loader=lambda tenant, key: wrong)

    def test_expired_key_and_revoked_trust_fail_closed(self):
        expired = dict(self.key_record, valid_until=149)
        with self.assertRaisesRegex(GrantProvenanceError, "GRANT_TRUST_KEY_OUTSIDE_VALIDITY"):
            self.verify(key_loader=lambda tenant, key: expired)
        revoked = dict(self.key_record, status="REVOKED")
        with self.assertRaisesRegex(GrantProvenanceError, "GRANT_TRUST_KEY_NOT_ACTIVE"):
            self.verify(key_loader=lambda tenant, key: revoked)

    def test_cross_tenant_and_expired_grant_fail_closed(self):
        with self.assertRaisesRegex(GrantProvenanceError, "GRANT_TENANT_MISMATCH"):
            self.verify(tenant="tenant-other")
        with self.assertRaisesRegex(GrantProvenanceError, "GRANT_OUTSIDE_VALIDITY"):
            self.verify()

    def test_only_pending_grants_may_be_issued(self):
        with self.assertRaisesRegex(GrantProvenanceError, "ISSUED_GRANT_MUST_BE_PENDING"):
            sign_authority_grant(dict(self.grant, status="ACTIVE"),
                                 key_id="issuer-key-01", private_key=self.private_key)


if __name__ == "__main__":
    unittest.main()
