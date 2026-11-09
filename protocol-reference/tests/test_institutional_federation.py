# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from governed_contracts import CONTRACT_VERSION, compute_payload_digest
from institutional_federation import (
    FederationMessageError,
    sign_cross_installation_instruction,
    verify_cross_installation_instruction,
)


def make_instruction():
    value = {
        "contract_version": CONTRACT_VERSION,
        "request_id": "REQ-XB-001",
        "operation_id": "OP-XB-001",
        "idempotency_key": "IDEM-XB-001",
        "principal_ref": "actor:origin",
        "authority_ref": "authority:origin-bank",
        "action": "SUBMIT_CROSS_BORDER_TRANSACTION",
        "target_ref": "transaction:XB-001",
        "purpose": "Request independent destination-side review",
        "jurisdiction": "UG",
        "submitted_at": "2026-11-09T10:00:00+00:00",
        "rule_pack_version": "ORIGIN-RULES:1.0",
        "evidence_refs": ["evidence:XB-001"],
        "evidence": {"document_ref": "evidence:XB-001"},
    }
    value["payload_digest"] = compute_payload_digest(value)
    return value


class ReplayGuard:
    def __init__(self):
        self.seen = set()

    def consume(self, tenant_id, issuer_installation_id, nonce, expires_at):
        key = (tenant_id, issuer_installation_id, nonce)
        if key in self.seen:
            return False
        self.seen.add(key)
        return True


class CrossInstallationFederationTests(unittest.TestCase):
    def setUp(self):
        self.private = Ed25519PrivateKey.generate()
        self.public = self.private.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw)
        self.replay = ReplayGuard()
        self.record = {
            "tenant_id": "tenant-rw",
            "key_id": "KEY-UG-01",
            "issuer_institution_id": "BANK-UG-01",
            "algorithm": "Ed25519",
            "status": "ACTIVE",
            "public_key_ref": "keyref:bank-ug-01",
            "valid_from": 1_700_000_000,
            "valid_until": 1_900_000_000,
            "issuer_valid_from": 1_700_000_000,
            "issuer_valid_until": 1_900_000_000,
        }
        self.envelope = sign_cross_installation_instruction(
            instruction=make_instruction(),
            message_id="MSG-XB-001",
            key_id="KEY-UG-01",
            issuer_installation_id="INSTALL-UG-01",
            issuer_institution_id="BANK-UG-01",
            receiver_installation_id="INSTALL-RW-01",
            receiver_institution_id="BANK-RW-01",
            nonce="NONCE-XB-001",
            issued_at=1_800_000_000,
            expires_at=1_800_000_120,
            private_key=self.private,
        )

    def verify(self, envelope=None, **overrides):
        args = {
            "receiver_tenant_id": "tenant-rw",
            "receiver_installation_id": "INSTALL-RW-01",
            "receiver_institution_id": "BANK-RW-01",
            "trust_key_loader": lambda tenant, key: self.record,
            "public_key_loader": lambda ref: self.public,
            "consume_nonce": self.replay.consume,
            "clock": lambda: 1_800_000_030,
        }
        args.update(overrides)
        return verify_cross_installation_instruction(
            self.envelope if envelope is None else envelope, **args)

    def test_valid_handoff_is_verified_but_not_authorised(self):
        result = self.verify()
        self.assertTrue(result["verified"])
        self.assertFalse(result["authority_inherited"])
        self.assertFalse(result["execution_authorised"])
        self.assertEqual(result["instruction"]["operation_id"], "OP-XB-001")

    def test_same_message_is_rejected_on_replay(self):
        self.verify()
        with self.assertRaisesRegex(FederationMessageError, "REPLAYED"):
            self.verify()

    def test_wrong_destination_installation_is_rejected(self):
        with self.assertRaisesRegex(FederationMessageError, "RECIPIENT_MISMATCH"):
            self.verify(receiver_installation_id="INSTALL-OTHER")

    def test_wrong_destination_institution_is_rejected(self):
        with self.assertRaisesRegex(FederationMessageError, "RECIPIENT_MISMATCH"):
            self.verify(receiver_institution_id="BANK-OTHER")

    def test_unknown_or_revoked_issuer_key_is_rejected(self):
        self.record["status"] = "REVOKED"
        with self.assertRaisesRegex(FederationMessageError, "MISMATCH_OR_INACTIVE"):
            self.verify()

    def test_key_must_belong_to_the_claimed_issuer(self):
        self.record["issuer_institution_id"] = "BANK-OTHER"
        with self.assertRaisesRegex(FederationMessageError, "MISMATCH_OR_INACTIVE"):
            self.verify()

    def test_signature_tampering_is_rejected(self):
        changed = {"claims": dict(self.envelope["claims"]),
                   "signature": self.envelope["signature"]}
        changed["claims"]["issuer_installation_id"] = "INSTALL-ATTACKER"
        with self.assertRaisesRegex(FederationMessageError, "SIGNATURE_VERIFICATION_FAILED"):
            self.verify(changed)

    def test_instruction_tampering_is_rejected_before_signature_check(self):
        changed = {"claims": dict(self.envelope["claims"]),
                   "signature": self.envelope["signature"]}
        changed["claims"]["instruction"] = dict(changed["claims"]["instruction"])
        changed["claims"]["instruction"]["purpose"] = "altered"
        with self.assertRaisesRegex(FederationMessageError, "DIGEST_INVALID"):
            self.verify(changed)

    def test_expired_message_is_rejected(self):
        with self.assertRaisesRegex(FederationMessageError, "EXPIRED_OR_OUTSIDE_WINDOW"):
            self.verify(clock=lambda: 1_800_000_121)

    def test_overlong_message_lifetime_is_rejected(self):
        changed = sign_cross_installation_instruction(
            instruction=make_instruction(), message_id="MSG-LONG",
            key_id="KEY-UG-01", issuer_installation_id="INSTALL-UG-01",
            issuer_institution_id="BANK-UG-01",
            receiver_installation_id="INSTALL-RW-01",
            receiver_institution_id="BANK-RW-01", nonce="NONCE-LONG",
            issued_at=1_800_000_000, expires_at=1_800_001_000,
            private_key=self.private)
        with self.assertRaisesRegex(FederationMessageError, "EXPIRED_OR_OUTSIDE_WINDOW"):
            self.verify(changed)

    def test_untrusted_issuer_is_rejected(self):
        with self.assertRaisesRegex(FederationMessageError, "ISSUER_NOT_TRUSTED"):
            self.verify(trust_key_loader=lambda tenant, key: None)


    def test_replay_guard_unavailability_fails_closed(self):
        def unavailable(*args):
            raise RuntimeError("store unavailable")
        with self.assertRaisesRegex(FederationMessageError, "REPLAY_GUARD_UNAVAILABLE"):
            self.verify(consume_nonce=unavailable)

    def test_sqlite_nonce_adapter_scopes_nonce_by_issuer_installation(self):
        from institutional_federation import sqlite_nonce_consumer

        class Guard:
            def __init__(self):
                self.seen = set()

            def consume(self, tenant, nonce, expiry):
                if (tenant, nonce) in self.seen:
                    return False
                self.seen.add((tenant, nonce))
                return True

        guard = Guard()
        consumer = sqlite_nonce_consumer(guard)
        self.assertTrue(consumer("tenant-rw", "INSTALL-UG-01", "same-nonce", 100))
        self.assertFalse(consumer("tenant-rw", "INSTALL-UG-01", "same-nonce", 100))
        self.assertTrue(consumer("tenant-rw", "INSTALL-KE-01", "same-nonce", 100))

    def test_invalid_instruction_digest_cannot_be_signed(self):
        invalid = make_instruction()
        invalid["purpose"] = "tampered after digest"
        with self.assertRaisesRegex(FederationMessageError, "DIGEST_INVALID"):
            sign_cross_installation_instruction(
                instruction=invalid, message_id="MSG-BAD", key_id="KEY-UG-01",
                issuer_installation_id="INSTALL-UG-01",
                issuer_institution_id="BANK-UG-01",
                receiver_installation_id="INSTALL-RW-01",
                receiver_institution_id="BANK-RW-01", nonce="NONCE-BAD",
                issued_at=1_800_000_000, expires_at=1_800_000_120,
                private_key=self.private)


if __name__ == "__main__":
    unittest.main()
