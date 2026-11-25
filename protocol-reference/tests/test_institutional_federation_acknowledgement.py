# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from governed_contracts import CONTRACT_VERSION, compute_payload_digest
from institutional_federation import sign_cross_installation_instruction
from institutional_federation_acknowledgement import (
    FederationAcknowledgementError,
    sign_receiver_acknowledgement,
    verify_receiver_acknowledgement,
)


def instruction():
    value = {
        "contract_version": CONTRACT_VERSION,
        "request_id": "REQ-ACK-001",
        "operation_id": "OP-ACK-001",
        "idempotency_key": "IDEM-ACK-001",
        "principal_ref": "principal:origin",
        "authority_ref": "authority:origin",
        "action": "REQUEST_DESTINATION_REVIEW",
        "target_ref": "transaction:ACK-001",
        "purpose": "Receiver acknowledgement contract test",
        "jurisdiction": "RW",
        "submitted_at": "2026-11-16T08:00:00+00:00",
        "rule_pack_version": "RW-RULES:1",
        "evidence_refs": ["evidence:ACK-001"],
        "evidence": {"document_ref": "evidence:ACK-001"},
    }
    value["payload_digest"] = compute_payload_digest(value)
    return value


class FederationAcknowledgementTests(unittest.TestCase):
    def setUp(self):
        self.sender_private = Ed25519PrivateKey.generate()
        self.receiver_private = Ed25519PrivateKey.generate()
        self.receiver_public = self.receiver_private.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        self.now = 1_800_000_000
        self.original = sign_cross_installation_instruction(
            instruction=instruction(), message_id="MSG-ACK-001",
            key_id="SENDER-KEY-1", issuer_installation_id="INSTALL-UG-01",
            issuer_institution_id="BANK-UG-01",
            receiver_installation_id="INSTALL-RW-01",
            receiver_institution_id="BANK-RW-01", nonce="NONCE-ACK-001",
            issued_at=self.now - 20, expires_at=self.now + 100,
            private_key=self.sender_private,
        )
        self.response = {
            "message_id": "MSG-ACK-001",
            "operation_id": "OP-ACK-001",
            "origin_installation_id": "INSTALL-UG-01",
            "origin_institution_id": "BANK-UG-01",
            "receiver_installation_id": "INSTALL-RW-01",
            "receiver_institution_id": "BANK-RW-01",
            "message_verified": True,
            "delivery_status": "COMPLETED",
            "duplicate_delivery": False,
            "receiver_decision": {
                "request_id": "REQ-ACK-001", "operation_id": "OP-ACK-001",
                "protocol_decision": "PASSED",
                "cee_execution_ref": "cee-operation:OP-ACK-001",
            },
            "receiver_runtime_pins": {
                "tenant_id": "tenant-rw", "institution_id": "BANK-RW-01",
                "binding_id": "BIND-RW-01", "jurisdiction_ref": "RW",
                "minah_deployment_id": "MINAH-RW-01",
                "minah_runtime_version": "1", "identity_provider_id": "IDP-RW-01",
                "identity_provider_version": "1", "policy_pack_version": "RW-RULES:1",
                "source_pack_version": "RW-SOURCES:1",
                "authority_profile_version": "RW-AUTHORITY:1",
            },
            "receiver_result": {"status": "COMMITTED"},
        }
        self.key_record = {
            "tenant_id": "tenant-ug", "installation_id": "INSTALL-RW-01",
            "institution_id": "BANK-RW-01", "key_id": "RECEIVER-KEY-1",
            "algorithm": "Ed25519", "status": "ACTIVE",
            "valid_from": self.now - 1000, "valid_until": self.now + 1000,
            "public_key_ref": "receiver-key-ref",
        }
        self.receipt = sign_receiver_acknowledgement(
            original_envelope=self.original, response=self.response,
            key_id="RECEIVER-KEY-1", accepted_at=self.now,
            private_key=self.receiver_private,
        )

    def verify(self, receipt=None, original=None, key_record=None):
        return verify_receiver_acknowledgement(
            self.receipt if receipt is None else receipt,
            original_envelope=self.original if original is None else original,
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            sender_institution_id="BANK-UG-01",
            receiver_trust_key_loader=lambda tenant, installation, key: (
                self.key_record if key_record is None else key_record
            ),
            receiver_public_key_loader=lambda ref: self.receiver_public,
            clock=lambda: self.now,
        )

    def test_receiver_signature_verifies_against_sender_local_trust(self):
        result = self.verify()
        self.assertTrue(result["verified"])
        self.assertEqual(result["response"]["receiver_decision"], self.response["receiver_decision"])

    def test_duplicate_delivery_metadata_does_not_change_stable_acknowledgement(self):
        duplicate = dict(self.response, duplicate_delivery=True)
        receipt = sign_receiver_acknowledgement(
            original_envelope=self.original, response=duplicate,
            key_id="RECEIVER-KEY-1", accepted_at=self.now,
            private_key=self.receiver_private,
        )
        self.assertEqual(receipt["response"], self.receipt["response"])
        self.assertTrue(self.verify(receipt=receipt)["verified"])

    def test_acknowledgement_is_bound_to_exact_original_envelope(self):
        altered = dict(self.original)
        altered["claims"] = dict(self.original["claims"], nonce="NONCE-OTHER")
        with self.assertRaisesRegex(
            FederationAcknowledgementError, "ORIGINAL_MESSAGE_MISMATCH"
        ):
            self.verify(original=altered)

    def test_acknowledgement_rejects_tampered_response(self):
        altered = dict(self.receipt)
        altered["response"] = dict(self.receipt["response"])
        altered["response"]["receiver_decision"] = dict(
            altered["response"]["receiver_decision"], protocol_decision="REJECTED"
        )
        with self.assertRaisesRegex(
            FederationAcknowledgementError, "RESPONSE_DIGEST_MISMATCH"
        ):
            self.verify(receipt=altered)

    def test_acknowledgement_rejects_wrong_sender_or_receiver_scope(self):
        altered = dict(self.receipt)
        altered["claims"] = dict(
            self.receipt["claims"], receiver_institution_id="BANK-OTHER"
        )
        with self.assertRaisesRegex(FederationAcknowledgementError, "SCOPE_MISMATCH"):
            self.verify(receipt=altered)

    def test_acknowledgement_rejects_untrusted_or_inactive_receiver_key(self):
        with self.assertRaisesRegex(FederationAcknowledgementError, "NOT_ACTIVE"):
            self.verify(key_record=dict(self.key_record, status="REVOKED"))

    def test_acknowledgement_rejects_key_validity_mismatch(self):
        with self.assertRaisesRegex(FederationAcknowledgementError, "KEY_OUTSIDE_VALIDITY"):
            self.verify(key_record=dict(self.key_record, valid_until=self.now - 1))

    def test_pending_or_incomplete_response_cannot_be_signed(self):
        pending = dict(self.response, delivery_status="PROCESSING")
        with self.assertRaisesRegex(FederationAcknowledgementError, "NOT_COMPLETED"):
            sign_receiver_acknowledgement(
                original_envelope=self.original, response=pending,
                key_id="RECEIVER-KEY-1", accepted_at=self.now,
                private_key=self.receiver_private,
            )

    def test_acknowledgement_cannot_be_rebound_to_another_message(self):
        altered = dict(self.response, message_id="MSG-OTHER")
        with self.assertRaisesRegex(FederationAcknowledgementError, "SCOPE_MISMATCH"):
            sign_receiver_acknowledgement(
                original_envelope=self.original, response=altered,
                key_id="RECEIVER-KEY-1", accepted_at=self.now,
                private_key=self.receiver_private,
            )


if __name__ == "__main__":
    unittest.main()
