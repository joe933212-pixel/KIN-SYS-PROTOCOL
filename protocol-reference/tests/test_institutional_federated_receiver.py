# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from governed_contracts import CONTRACT_VERSION, compute_payload_digest
from institutional_federation import sign_cross_installation_instruction
from institutional_federated_receiver import (
    FederatedInstructionReceiverError,
    InstitutionalFederatedInstructionReceiver,
)


def instruction():
    value = {
        "contract_version": CONTRACT_VERSION,
        "request_id": "REQ-RECEIVE-001",
        "operation_id": "OP-RECEIVE-001",
        "idempotency_key": "IDEM-RECEIVE-001",
        "principal_ref": "principal:origin-user",
        "authority_ref": "authority:origin-bank",
        "action": "REQUEST_DESTINATION_REVIEW",
        "target_ref": "transaction:RECEIVE-001",
        "purpose": "Independent receiving institution review",
        "jurisdiction": "RW",
        "submitted_at": "2026-11-09T10:00:00+00:00",
        "rule_pack_version": "RW-RULES:3.2",
        "evidence_refs": ["evidence:RECEIVE-001"],
        "evidence": {"document_ref": "evidence:RECEIVE-001"},
    }
    value["payload_digest"] = compute_payload_digest(value)
    return value


class LocalExecution:
    def __init__(self):
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        item = kwargs["instruction"]
        return {
            "decision": {
                "request_id": item["request_id"],
                "operation_id": item["operation_id"],
                "protocol_decision": "DEFERRED",
            },
            "result": {"status": "DEFERRED"},
            "runtime_pins": {"institution_id": kwargs["institution_id"]},
        }


class FederatedReceiverTests(unittest.TestCase):
    def setUp(self):
        self.private = Ed25519PrivateKey.generate()
        self.public = self.private.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw)
        self.now = 1_800_000_030
        self.replay = set()
        self.key_record = {
            "tenant_id": "tenant-rw",
            "key_id": "KEY-UG-01",
            "issuer_institution_id": "BANK-UG-01",
            "algorithm": "Ed25519",
            "status": "ACTIVE",
            "public_key_ref": "keyref:ug",
            "valid_from": 1_700_000_000,
            "valid_until": 1_900_000_000,
            "issuer_valid_from": 1_700_000_000,
            "issuer_valid_until": 1_900_000_000,
        }
        self.envelope = sign_cross_installation_instruction(
            instruction=instruction(), message_id="MSG-RECEIVE-001",
            key_id="KEY-UG-01", issuer_installation_id="INSTALL-UG-01",
            issuer_institution_id="BANK-UG-01",
            receiver_installation_id="INSTALL-RW-01",
            receiver_institution_id="BANK-RW-01", nonce="NONCE-RECEIVE-001",
            issued_at=self.now - 30, expires_at=self.now + 90,
            private_key=self.private,
        )
        self.executor = LocalExecution()
        self.receiver = InstitutionalFederatedInstructionReceiver(
            receiver_tenant_id="tenant-rw",
            receiver_installation_id="INSTALL-RW-01",
            receiver_institution_id="BANK-RW-01",
            receiver_binding_id="BIND-RW-01",
            execution_service=self.executor,
            trust_key_loader=lambda tenant, key: self.key_record,
            public_key_loader=lambda ref: self.public,
            consume_nonce=self.consume,
            clock=lambda: self.now,
        )

    def consume(self, tenant, issuer, nonce, expires):
        key = (tenant, issuer, nonce)
        if key in self.replay:
            return False
        self.replay.add(key)
        return True

    def test_verified_message_enters_receiver_selected_local_execution(self):
        result = self.receiver.receive(self.envelope)
        self.assertTrue(result["message_verified"])
        self.assertFalse(result["origin_authority_inherited"])
        self.assertFalse(result["receiver_execution_authorised_by_origin"])
        self.assertEqual(len(self.executor.calls), 1)
        call = self.executor.calls[0]
        self.assertEqual(call["tenant_id"], "tenant-rw")
        self.assertEqual(call["institution_id"], "BANK-RW-01")
        self.assertEqual(call["binding_id"], "BIND-RW-01")
        self.assertEqual(call["instruction"]["principal_ref"], "principal:origin-user")


    def test_durable_duplicate_is_not_executed_twice(self):
        calls = []

        def accept_verified_message(**kwargs):
            calls.append(kwargs)
            return {
                "inserted": len(calls) == 1,
                "status": "RECEIVED",
            }

        receiver = InstitutionalFederatedInstructionReceiver(
            receiver_tenant_id="tenant-rw",
            receiver_installation_id="INSTALL-RW-01",
            receiver_institution_id="BANK-RW-01",
            receiver_binding_id="BIND-RW-01",
            execution_service=self.executor,
            trust_key_loader=lambda tenant, key: self.key_record,
            public_key_loader=lambda ref: self.public,
            accept_verified_message=accept_verified_message,
            clock=lambda: self.now,
        )
        receiver.receive(self.envelope)
        with self.assertRaisesRegex(
            FederatedInstructionReceiverError,
            "FEDERATED_MESSAGE_ALREADY_DURABLY_ACCEPTED",
        ):
            receiver.receive(self.envelope)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self.executor.calls), 1)

    def test_invalid_signature_never_reaches_local_execution(self):
        bad = {"claims": dict(self.envelope["claims"]),
               "signature": self.envelope["signature"]}
        bad["claims"]["issuer_installation_id"] = "INSTALL-ATTACKER"
        with self.assertRaises(FederatedInstructionReceiverError):
            self.receiver.receive(bad)
        self.assertEqual(self.executor.calls, [])

    def test_destination_mismatch_never_reaches_local_execution(self):
        receiver = InstitutionalFederatedInstructionReceiver(
            receiver_tenant_id="tenant-rw",
            receiver_installation_id="INSTALL-OTHER",
            receiver_institution_id="BANK-RW-01",
            receiver_binding_id="BIND-RW-01",
            execution_service=self.executor,
            trust_key_loader=lambda tenant, key: self.key_record,
            public_key_loader=lambda ref: self.public,
            consume_nonce=self.consume,
            clock=lambda: self.now,
        )
        with self.assertRaises(FederatedInstructionReceiverError):
            receiver.receive(self.envelope)
        self.assertEqual(self.executor.calls, [])

    def test_local_rejection_is_not_relabelled_as_federation_acceptance(self):
        class Rejecting:
            def execute(self, **kwargs):
                raise ValueError("INSTRUCTION_JURISDICTION_DOES_NOT_MATCH_ACTIVE_BINDING")
        receiver = InstitutionalFederatedInstructionReceiver(
            receiver_tenant_id="tenant-rw",
            receiver_installation_id="INSTALL-RW-01",
            receiver_institution_id="BANK-RW-01",
            receiver_binding_id="BIND-RW-01",
            execution_service=Rejecting(),
            trust_key_loader=lambda tenant, key: self.key_record,
            public_key_loader=lambda ref: self.public,
            consume_nonce=self.consume,
            clock=lambda: self.now,
        )
        with self.assertRaisesRegex(
            FederatedInstructionReceiverError, "RECEIVER_LOCAL_EXECUTION"
        ):
            receiver.receive(self.envelope)

    def test_receiver_configuration_is_mandatory(self):
        with self.assertRaises(FederatedInstructionReceiverError):
            InstitutionalFederatedInstructionReceiver(
                receiver_tenant_id="", receiver_installation_id="INSTALL-RW-01",
                receiver_institution_id="BANK-RW-01", receiver_binding_id="BIND-RW-01",
                execution_service=self.executor,
                trust_key_loader=lambda tenant, key: self.key_record,
                public_key_loader=lambda ref: self.public,
                consume_nonce=self.consume, clock=lambda: self.now,
            )


if __name__ == "__main__":
    unittest.main()
