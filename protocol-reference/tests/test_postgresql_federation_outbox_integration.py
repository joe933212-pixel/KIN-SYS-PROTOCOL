# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""PostgreSQL integration tests for sender-side federation outbox recovery."""
from __future__ import annotations

import os
import sys
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DSN = os.environ.get("KIN_SYS_POSTGRES_TEST_DSN")
from postgresql_migrations import apply_migrations
from postgresql_federation_outbox import FederationOutboxError, PostgreSQLFederationOutbox

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from governed_contracts import CONTRACT_VERSION, compute_payload_digest
from institutional_federation import sign_cross_installation_instruction
from institutional_federation_acknowledgement import (
    sign_receiver_acknowledgement, verify_receiver_acknowledgement,
)


def instruction():
    value = {
        "contract_version": CONTRACT_VERSION, "request_id": "REQ-OUTBOX-001",
        "operation_id": "OP-OUTBOX-001", "idempotency_key": "IDEM-OUTBOX-001",
        "principal_ref": "principal:origin", "authority_ref": "authority:origin",
        "action": "REQUEST_DESTINATION_REVIEW", "target_ref": "transaction:OUTBOX-001",
        "purpose": "Durable sender outbox integration test", "jurisdiction": "RW",
        "submitted_at": "2026-11-16T08:00:00+00:00",
        "rule_pack_version": "RW-RULES:1", "evidence_refs": ["evidence:OUTBOX-001"],
        "evidence": {"document_ref": "evidence:OUTBOX-001"},
    }
    value["payload_digest"] = compute_payload_digest(value)
    return value


@unittest.skipUnless(DSN, "KIN_SYS_POSTGRES_TEST_DSN is not configured")
class PostgreSQLFederationOutboxIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        cls.psycopg, cls.sql = psycopg, sql

    def setUp(self):
        self.schema = "kin_sys_outbox_" + uuid.uuid4().hex
        self.admin = self.psycopg.connect(DSN, autocommit=True)
        self.admin.execute(
            self.sql.SQL("CREATE SCHEMA {}").format(self.sql.Identifier(self.schema))
        )

        def connect():
            connection = self.psycopg.connect(DSN)
            connection.execute(
                self.sql.SQL("SET search_path TO {}").format(
                    self.sql.Identifier(self.schema)
                )
            )
            connection.commit()
            return connection

        self.connect = connect
        with self.connect() as connection:
            apply_migrations(connection)

        self.now = 1_800_000_000
        self.sender_private = Ed25519PrivateKey.generate()
        self.receiver_private = Ed25519PrivateKey.generate()
        self.receiver_public = self.receiver_private.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        self.envelope = sign_cross_installation_instruction(
            instruction=instruction(), message_id="MSG-OUTBOX-001",
            key_id="SENDER-KEY-1", issuer_installation_id="INSTALL-UG-01",
            issuer_institution_id="BANK-UG-01",
            receiver_installation_id="INSTALL-RW-01",
            receiver_institution_id="BANK-RW-01", nonce="NONCE-OUTBOX-001",
            issued_at=self.now - 20, expires_at=self.now + 100,
            private_key=self.sender_private,
        )
        self.response = {
            "message_id": "MSG-OUTBOX-001", "operation_id": "OP-OUTBOX-001",
            "origin_installation_id": "INSTALL-UG-01",
            "origin_institution_id": "BANK-UG-01",
            "receiver_installation_id": "INSTALL-RW-01",
            "receiver_institution_id": "BANK-RW-01", "message_verified": True,
            "delivery_status": "COMPLETED", "duplicate_delivery": False,
            "receiver_decision": {
                "request_id": "REQ-OUTBOX-001", "operation_id": "OP-OUTBOX-001",
                "protocol_decision": "PASSED",
                "cee_execution_ref": "cee-operation:OP-OUTBOX-001",
            },
            "receiver_runtime_pins": {
                "tenant_id": "tenant-rw", "institution_id": "BANK-RW-01",
                "binding_id": "BIND-RW-01", "jurisdiction_ref": "RW",
                "minah_deployment_id": "MINAH-RW-01", "minah_runtime_version": "1",
                "identity_provider_id": "IDP-RW-01", "identity_provider_version": "1",
                "policy_pack_version": "RW-RULES:1", "source_pack_version": "RW-SOURCES:1",
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
        def verifier(receipt, original):
            return verify_receiver_acknowledgement(
                receipt, original_envelope=original,
                sender_tenant_id="tenant-ug",
                sender_installation_id="INSTALL-UG-01",
                sender_institution_id="BANK-UG-01",
                receiver_trust_key_loader=lambda tenant, installation, key: self.key_record,
                receiver_public_key_loader=lambda ref: self.receiver_public,
                clock=lambda: self.now,
            )
        self.outbox = PostgreSQLFederationOutbox(self.connect, verifier)
        self.outbox.enqueue(
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            envelope=self.envelope,
        )
        self.receipt = sign_receiver_acknowledgement(
            original_envelope=self.envelope, response=self.response,
            key_id="RECEIVER-KEY-1", accepted_at=self.now,
            private_key=self.receiver_private,
        )

    def tearDown(self):
        self.admin.execute(
            self.sql.SQL("DROP SCHEMA {} CASCADE").format(self.sql.Identifier(self.schema))
        )
        self.admin.close()

    def test_enqueue_is_idempotent_and_rejects_content_rebinding(self):
        duplicate = self.outbox.enqueue(
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            envelope=self.envelope,
        )
        self.assertFalse(duplicate["inserted"])
        altered = {
            "claims": dict(self.envelope["claims"], nonce="NONCE-OTHER"),
            "signature": self.envelope["signature"],
        }
        with self.assertRaisesRegex(FederationOutboxError, "IDEMPOTENCY_CONFLICT"):
            self.outbox.enqueue(
                sender_tenant_id="tenant-ug",
                sender_installation_id="INSTALL-UG-01",
                envelope=altered,
            )

    def test_only_one_sender_worker_claims_delivery(self):
        def claim(worker):
            return self.outbox.claim(
                sender_tenant_id="tenant-ug",
                sender_installation_id="INSTALL-UG-01",
                message_id="MSG-OUTBOX-001", worker_id=worker,
            )
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ["sender-worker-a", "sender-worker-b"]))
        self.assertEqual(sum(item is not None for item in results), 1)

    def test_transport_failure_releases_delivery_for_retry(self):
        claimed = self.outbox.claim(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            message_id="MSG-OUTBOX-001", worker_id="sender-worker",
        )
        self.assertEqual(claimed["attempt_count"], 1)
        self.assertTrue(self.outbox.mark_retryable(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            message_id="MSG-OUTBOX-001", worker_id="sender-worker",
            error_code="TRANSPORT_TIMEOUT",
        ))
        retry = self.outbox.claim(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            message_id="MSG-OUTBOX-001", worker_id="sender-worker-retry",
        )
        self.assertEqual(retry["attempt_count"], 2)

    def test_late_acknowledgement_completes_after_sender_lease_expires(self):
        self.outbox.claim(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            message_id="MSG-OUTBOX-001", worker_id="sender-crashed",
        )
        with self.connect() as connection:
            connection.execute(
                """UPDATE federation_outbox SET lease_expires_at = CURRENT_TIMESTAMP
                   - INTERVAL '1 second'
                   WHERE sender_tenant_id = 'tenant-ug'
                     AND sender_installation_id = 'INSTALL-UG-01'
                     AND message_id = 'MSG-OUTBOX-001'"""
            )
        result = self.outbox.acknowledge(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            message_id="MSG-OUTBOX-001", receipt=self.receipt,
        )
        self.assertTrue(result["acknowledged"])
        self.assertFalse(result["duplicate"])
        stored = self.outbox.get(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            message_id="MSG-OUTBOX-001",
        )
        self.assertEqual(stored["status"], "ACKNOWLEDGED")
        self.assertEqual(stored["acknowledgement"], self.receipt)

    def test_duplicate_acknowledgement_is_idempotent(self):
        first = self.outbox.acknowledge(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            message_id="MSG-OUTBOX-001", receipt=self.receipt,
        )
        second = self.outbox.acknowledge(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            message_id="MSG-OUTBOX-001", receipt=self.receipt,
        )
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["response"], second["response"])

    def test_invalid_receiver_signature_is_rejected(self):
        altered = dict(self.receipt)
        altered["response"] = dict(self.receipt["response"])
        altered["response"]["receiver_result"] = {"status": "ALTERED"}
        with self.assertRaisesRegex(
            FederationOutboxError, "ACKNOWLEDGEMENT_VERIFICATION_FAILED"
        ):
            self.outbox.acknowledge(
                sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
                message_id="MSG-OUTBOX-001", receipt=altered,
            )

    def test_conflicting_valid_acknowledgement_is_rejected(self):
        changed_response = dict(self.response, receiver_result={"status": "DIFFERENT"})
        changed_receipt = sign_receiver_acknowledgement(
            original_envelope=self.envelope, response=changed_response,
            key_id="RECEIVER-KEY-1", accepted_at=self.now,
            private_key=self.receiver_private,
        )
        self.outbox.acknowledge(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            message_id="MSG-OUTBOX-001", receipt=self.receipt,
        )
        with self.assertRaisesRegex(FederationOutboxError, "ACKNOWLEDGEMENT_CONFLICT"):
            self.outbox.acknowledge(
                sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
                message_id="MSG-OUTBOX-001", receipt=changed_receipt,
            )

    def test_acknowledgement_cannot_be_rebound_to_another_sender_row(self):
        with self.assertRaisesRegex(FederationOutboxError, "MESSAGE_NOT_FOUND"):
            self.outbox.acknowledge(
                sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
                message_id="MSG-OTHER", receipt=self.receipt,
            )



    def test_batch_claim_leases_rows_before_returning_to_dispatcher(self):
        claims = self.outbox.claim_batch(
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            worker_id="sender-batch-worker",
            limit=5,
            lease_seconds=45,
        )
        self.assertEqual(len(claims), 1)
        claim = claims[0]
        self.assertEqual(claim["message_id"], "MSG-OUTBOX-001")
        self.assertEqual(claim["receiver_installation_id"], "INSTALL-RW-01")
        self.assertEqual(claim["lease_owner"], "sender-batch-worker")
        self.assertEqual(claim["attempt_count"], 1)
        self.assertEqual(self.outbox.claim_batch(
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            worker_id="sender-other-worker",
            limit=5,
            lease_seconds=45,
        ), [])


    def test_retry_schedule_prevents_early_reclaim_and_allows_due_retry(self):
        claimed = self.outbox.claim(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            message_id="MSG-OUTBOX-001", worker_id="sender-worker",
        )
        self.assertEqual(claimed["attempt_count"], 1)
        self.assertTrue(self.outbox.mark_retryable(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            message_id="MSG-OUTBOX-001", worker_id="sender-worker",
            error_code="TRANSPORT_TIMEOUT",
        ))
        stored = self.outbox.get(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            message_id="MSG-OUTBOX-001",
        )
        self.assertEqual(stored["status"], "RETRYABLE_FAILURE")
        self.assertIsNotNone(stored["next_attempt_at"])
        self.assertIsNone(self.outbox.claim(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            message_id="MSG-OUTBOX-001", worker_id="too-early",
        ))
        with self.connect() as connection:
            connection.execute(
                """UPDATE federation_outbox
                   SET next_attempt_at = CURRENT_TIMESTAMP - INTERVAL '1 second'
                   WHERE sender_tenant_id = 'tenant-ug'
                     AND sender_installation_id = 'INSTALL-UG-01'
                     AND message_id = 'MSG-OUTBOX-001'"""
            )
        retry = self.outbox.claim(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            message_id="MSG-OUTBOX-001", worker_id="sender-worker-retry",
        )
        self.assertEqual(retry["attempt_count"], 2)

    def test_retry_budget_dead_letters_failed_delivery(self):
        with self.connect() as connection:
            connection.execute(
                """UPDATE federation_outbox SET max_attempts = 1
                   WHERE sender_tenant_id = 'tenant-ug'
                     AND sender_installation_id = 'INSTALL-UG-01'
                     AND message_id = 'MSG-OUTBOX-001'"""
            )
        claimed = self.outbox.claim(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            message_id="MSG-OUTBOX-001", worker_id="last-attempt",
        )
        self.assertEqual(claimed["attempt_count"], 1)
        self.assertTrue(self.outbox.mark_retryable(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            message_id="MSG-OUTBOX-001", worker_id="last-attempt",
            error_code="TRANSPORT_TIMEOUT",
        ))
        stored = self.outbox.get(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            message_id="MSG-OUTBOX-001",
        )
        self.assertEqual(stored["status"], "DEAD_LETTER")
        self.assertIsNotNone(stored["dead_lettered_at"])
        self.assertIsNone(stored["next_attempt_at"])
        self.assertEqual(self.outbox.claim_batch(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            worker_id="no-reclaim", limit=5,
        ), [])

    def test_expired_final_delivery_lease_is_dead_lettered_not_reclaimed(self):
        with self.connect() as connection:
            connection.execute(
                """UPDATE federation_outbox SET max_attempts = 1
                   WHERE sender_tenant_id = 'tenant-ug'
                     AND sender_installation_id = 'INSTALL-UG-01'
                     AND message_id = 'MSG-OUTBOX-001'"""
            )
        self.outbox.claim(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            message_id="MSG-OUTBOX-001", worker_id="crashed-final-attempt",
        )
        with self.connect() as connection:
            connection.execute(
                """UPDATE federation_outbox SET lease_expires_at = CURRENT_TIMESTAMP
                   - INTERVAL '1 second'
                   WHERE sender_tenant_id = 'tenant-ug'
                     AND sender_installation_id = 'INSTALL-UG-01'
                     AND message_id = 'MSG-OUTBOX-001'"""
            )
        self.assertEqual(self.outbox.claim_batch(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            worker_id="after-crash", limit=5,
        ), [])
        stored = self.outbox.get(
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            message_id="MSG-OUTBOX-001",
        )
        self.assertEqual(stored["status"], "DEAD_LETTER")
        self.assertIsNotNone(stored["dead_lettered_at"])

if __name__ == "__main__":
    unittest.main()
