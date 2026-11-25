# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from institutional_grant_provenance import (
    GrantProvenanceError, accept_authority_grant, sign_authority_grant,
    verify_authority_grant, sign_receiver_acceptance_receipt,
    verify_receiver_acceptance_receipt,
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

    def verify(self, envelope=None, tenant="tenant-receiver", key_loader=None, clock=None):
        return verify_authority_grant(
            envelope or self.envelope, tenant_id=tenant,
            trust_key_loader=key_loader or self.trust_key,
            public_key_loader=self.public_key, clock=clock or (lambda: 150))

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
            self.verify(clock=lambda: 350)

    def test_only_pending_grants_may_be_issued(self):
        with self.assertRaisesRegex(GrantProvenanceError, "ISSUED_GRANT_MUST_BE_PENDING"):
            sign_authority_grant(dict(self.grant, status="ACTIVE"),
                                 key_id="issuer-key-01", private_key=self.private_key)




    def test_receiver_receipt_binds_receiver_grant_and_complete_approval_bundle(self):
        approval = {"actor_ref": "actor:bank-approver", "approval_ref": "bank:approval-9",
                    "decision": "ACCEPT"}
        accepted = accept_authority_grant(
            self.envelope, tenant_id="tenant-receiver",
            receiver_institution_id="BANK-UG-01",
            trust_key_loader=self.trust_key, public_key_loader=self.public_key,
            verify_receiver_approval=lambda grant, supplied: supplied == approval,
            receiver_approval=approval, clock=lambda: 150)
        receiver_private = Ed25519PrivateKey.generate()
        receiver_public = receiver_private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        receipt = sign_receiver_acceptance_receipt(
            tenant_id="tenant-receiver", receiver_institution_id="BANK-UG-01",
            grant_id="GRANT-FED-01", grant_version="1",
            grant_digest=accepted["acceptance"]["grant_digest"],
            receiver_approval=approval, accepted_at=150,
            key_id="receiver-key-01", private_key=receiver_private)
        record = {
            "tenant_id": "tenant-receiver", "receiver_institution_id": "BANK-UG-01",
            "key_id": "receiver-key-01", "algorithm": "Ed25519",
            "status": "ACTIVE", "public_key_ref": "public:receiver-key-01",
            "valid_from": 100, "valid_until": 300,
        }
        claims = verify_receiver_acceptance_receipt(
            receipt, tenant_id="tenant-receiver",
            receiver_institution_id="BANK-UG-01", grant=self.grant,
            acceptance=accepted["acceptance"],
            receiver_acceptance_key_loader=lambda tenant, institution, key: record,
            receiver_acceptance_public_key_loader=lambda ref: receiver_public,
            clock=lambda: 160)
        self.assertEqual(claims["accepted_at"], 150)
        self.assertEqual(claims["receiver_institution_id"], "BANK-UG-01")

        tampered = dict(accepted["acceptance"],
                        receiver_approval=dict(approval, decision="REJECT"))
        with self.assertRaisesRegex(GrantProvenanceError,
                                    "RECEIVER_ACCEPTANCE_RECEIPT_SCOPE_MISMATCH"):
            verify_receiver_acceptance_receipt(
                receipt, tenant_id="tenant-receiver",
                receiver_institution_id="BANK-UG-01", grant=self.grant,
                acceptance=tampered,
                receiver_acceptance_key_loader=lambda tenant, institution, key: record,
                receiver_acceptance_public_key_loader=lambda ref: receiver_public,
                clock=lambda: 160)

    def test_receiver_receipt_rejects_wrong_receiver_tenant_key_and_revocation(self):
        approval = {"actor_ref": "actor:bank-approver", "approval_ref": "bank:approval-9"}
        accepted = accept_authority_grant(
            self.envelope, tenant_id="tenant-receiver",
            receiver_institution_id="BANK-UG-01",
            trust_key_loader=self.trust_key, public_key_loader=self.public_key,
            verify_receiver_approval=lambda grant, supplied: supplied == approval,
            receiver_approval=approval, clock=lambda: 150)
        receiver_private = Ed25519PrivateKey.generate()
        receiver_public = receiver_private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        receipt = sign_receiver_acceptance_receipt(
            tenant_id="tenant-receiver", receiver_institution_id="BANK-UG-01",
            grant_id="GRANT-FED-01", grant_version="1",
            grant_digest=accepted["acceptance"]["grant_digest"],
            receiver_approval=approval, accepted_at=150,
            key_id="receiver-key-01", private_key=receiver_private)
        record = {
            "tenant_id": "tenant-receiver", "receiver_institution_id": "BANK-UG-01",
            "key_id": "receiver-key-01", "algorithm": "Ed25519",
            "status": "ACTIVE", "public_key_ref": "public:receiver-key-01",
            "valid_from": 100, "valid_until": 300,
        }
        def verify(key_record, tenant="tenant-receiver", receiver="BANK-UG-01"):
            return verify_receiver_acceptance_receipt(
                receipt, tenant_id=tenant, receiver_institution_id=receiver,
                grant=self.grant, acceptance=accepted["acceptance"],
                receiver_acceptance_key_loader=lambda t, i, k: key_record,
                receiver_acceptance_public_key_loader=lambda ref: receiver_public,
                clock=lambda: 160)
        with self.assertRaisesRegex(GrantProvenanceError, "RECEIVER_ACCEPTANCE_RECEIPT_SCOPE_MISMATCH"):
            verify(record, tenant="tenant-other")
        with self.assertRaisesRegex(GrantProvenanceError, "RECEIVER_ACCEPTANCE_KEY_NOT_ACTIVE"):
            verify(dict(record, status="REVOKED"))
        with self.assertRaisesRegex(GrantProvenanceError, "RECEIVER_ACCEPTANCE_KEY_OUTSIDE_VALIDITY"):
            verify(dict(record, valid_from=151))


if __name__ == "__main__":
    unittest.main()
