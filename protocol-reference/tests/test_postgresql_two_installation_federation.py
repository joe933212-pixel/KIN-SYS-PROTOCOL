# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""End-to-end federation across isolated PostgreSQL installation stores.

Each side owns a separate PostgreSQL schema and its own execution runtime. The
test uses the real signed-envelope, receiver execution, durable inbox, signed
acknowledgement and durable outbox components. CEE/IVM remain the reference
runtime's local stores; this is not a two-host or production deployment test.
"""
from __future__ import annotations

import os
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from governed_contracts import CONTRACT_VERSION, compute_payload_digest
from institutional_federation import sign_cross_installation_instruction
from institutional_federation_acknowledgement import verify_receiver_acknowledgement
from institutional_federated_receiver import InstitutionalFederatedInstructionReceiver
from postgresql_federation_inbox import PostgreSQLFederationInbox
from postgresql_federation_outbox import PostgreSQLFederationOutbox
from postgresql_migrations import apply_migrations

from test_cross_installation_runtime_integration import IsolatedInstallation


DSN = os.environ.get("KIN_SYS_POSTGRES_TEST_DSN")


def federated_instruction():
    value = {
        "contract_version": CONTRACT_VERSION,
        "request_id": "REQ-PG-FED-UG-RW-001",
        "operation_id": "OP-PG-FED-UG-RW-001",
        "idempotency_key": "IDEM-PG-FED-UG-RW-001",
        "principal_ref": "AUTHORITY-001",
        "authority_ref": "origin:authority-claim-is-not-inherited",
        "action": "ACTIVATE",
        "target_ref": "TX-PG-FED-UG-RW-001",
        "purpose": "Independent receiving-institution review",
        "jurisdiction": "RW",
        "submitted_at": "2026-11-16T08:00:00+00:00",
        "rule_pack_version": "KIN-SYS-DEMO-RULES:1.0",
        "evidence_refs": ["evidence:PG-FED-UG-RW-001"],
        "evidence": {
            "case_ref": "TX-PG-FED-UG-RW-001",
            "consignment_id": "TX-PG-FED-UG-RW-001",
            "commodity": "SYNTHETIC_MAIZE",
            "origin": "UG",
            "destination": "RW",
            "rule_refs": ["SYNTHETIC-CROSS-BORDER-RULE"],
        },
    }
    value["payload_digest"] = compute_payload_digest(value)
    return value


@unittest.skipUnless(DSN, "KIN_SYS_POSTGRES_TEST_DSN is not configured")
class PostgreSQLTwoInstallationFederationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        cls.psycopg, cls.sql = psycopg, sql

    def setUp(self):
        self.admin = self.psycopg.connect(DSN, autocommit=True)
        self.sender_schema = "kin_sys_sender_" + uuid.uuid4().hex
        self.receiver_schema = "kin_sys_receiver_" + uuid.uuid4().hex
        for schema in (self.sender_schema, self.receiver_schema):
            self.admin.execute(
                self.sql.SQL("CREATE SCHEMA {}").format(self.sql.Identifier(schema))
            )

        def factory(schema):
            def connect():
                connection = self.psycopg.connect(DSN)
                connection.execute(
                    self.sql.SQL("SET search_path TO {}").format(
                        self.sql.Identifier(schema)
                    )
                )
                connection.commit()
                return connection
            return connect

        self.sender_connect = factory(self.sender_schema)
        self.receiver_connect = factory(self.receiver_schema)
        with self.sender_connect() as connection:
            apply_migrations(connection)
        with self.receiver_connect() as connection:
            apply_migrations(connection)

        self.now = 1_800_000_030
        self.sender_private = Ed25519PrivateKey.generate()
        self.sender_public = self.sender_private.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        self.receiver_private = Ed25519PrivateKey.generate()
        self.receiver_public = self.receiver_private.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        self.sender_runtime = IsolatedInstallation(
            country="UG", tenant="tenant-ug", institution="BANK-UG-01",
            installation="INSTALL-UG-01", binding="BIND-UG-01",
            jurisdiction="UG", policy_version="KIN-SYS-DEMO-RULES:1.0",
            source_version="UG-SOURCES:1.0", minah_id="MINAH-UG-01",
            idp_id="IDP-UG-01", jurisdiction_package="UG-PACK-01",
            authority_version="UG-AUTHORITY:1.0",
        )
        self.receiver_runtime = IsolatedInstallation(
            country="RW", tenant="tenant-rw", institution="BANK-RW-01",
            installation="INSTALL-RW-01", binding="BIND-RW-01",
            jurisdiction="RW", policy_version="KIN-SYS-DEMO-RULES:1.0",
            source_version="RW-SOURCES:2.0", minah_id="MINAH-RW-01",
            idp_id="IDP-RW-01", jurisdiction_package="RW-PACK-01",
            authority_version="RW-AUTHORITY:2.0",
        )
        self.addCleanup(self.sender_runtime.close)
        self.addCleanup(self.receiver_runtime.close)

        # Run the receiving institution's actual MINAH -> CEE -> IVM path
        # against its own PostgreSQL schema, not process-local SQLite stores.
        # This makes the execution outcome survive replacement of the runtime.
        from postgresql_cee_journal import PostgreSQLCEEJournal
        from postgresql_protocol_store import PostgreSQLProtocolStore
        from protocol_pipeline import KinSysProtocolPipeline
        from institutional_instruction_execution import InstitutionalInstructionExecutionService

        self.receiver_runtime.pipeline.close()
        self.receiver_runtime.pipeline = KinSysProtocolPipeline(
            minah_profile=self.receiver_runtime.profile,
            cee_journal=PostgreSQLCEEJournal(self.receiver_connect),
            ivm_store=PostgreSQLProtocolStore(self.receiver_connect),
            identity_provider_id=self.receiver_runtime.idp_id,
            identity_provider_version="1",
            jurisdiction_package_id=self.receiver_runtime.jurisdiction_package,
            jurisdiction_package_version="1",
            authority_profile_version=self.receiver_runtime.authority_version,
        )
        self.receiver_runtime.service = InstitutionalInstructionExecutionService(
            runtime_binding_resolver=self.receiver_runtime,
            pipeline_factory=lambda _context: self.receiver_runtime.pipeline,
        )

        self.receiver_inbox = PostgreSQLFederationInbox(self.receiver_connect)
        self.sender_outbox = PostgreSQLFederationOutbox(
            self.sender_connect, self._verify_receiver_receipt
        )
        self.sender_trust_for_receiver = {
            "tenant_id": "tenant-ug",
            "installation_id": "INSTALL-RW-01",
            "institution_id": "BANK-RW-01",
            "key_id": "KEY-RW-01",
            "algorithm": "Ed25519",
            "status": "ACTIVE",
            "valid_from": 1_700_000_000,
            "valid_until": 1_900_000_000,
            "public_key_ref": "keyref:rw-receiver",
        }
        self.receiver_trust_for_sender = {
            "tenant_id": "tenant-rw",
            "key_id": "KEY-UG-01",
            "issuer_institution_id": "BANK-UG-01",
            "algorithm": "Ed25519",
            "status": "ACTIVE",
            "valid_from": 1_700_000_000,
            "valid_until": 1_900_000_000,
            "issuer_valid_from": 1_700_000_000,
            "issuer_valid_until": 1_900_000_000,
            "public_key_ref": "keyref:ug-sender",
        }
        self.receiver = InstitutionalFederatedInstructionReceiver(
            receiver_tenant_id="tenant-rw",
            receiver_installation_id="INSTALL-RW-01",
            receiver_institution_id="BANK-RW-01",
            receiver_binding_id="BIND-RW-01",
            execution_service=self.receiver_runtime.service,
            trust_key_loader=lambda tenant, key_id: (
                self.receiver_trust_for_sender
                if tenant == "tenant-rw" and key_id == "KEY-UG-01" else None
            ),
            public_key_loader=lambda ref: (
                self.sender_public if ref == "keyref:ug-sender" else b""
            ),
            clock=lambda: self.now,
            inbox=self.receiver_inbox,
            inbox_worker_id="rw-inbox-worker-1",
            inbox_lease_seconds=30,
            receiver_signing_key_id="KEY-RW-01",
            receiver_signing_key_loader=lambda tenant, institution, key_id: (
                self.receiver_private
                if (tenant, institution, key_id) ==
                   ("tenant-rw", "BANK-RW-01", "KEY-RW-01")
                else None
            ),
        )

        instruction = federated_instruction()
        self.envelope = sign_cross_installation_instruction(
            instruction=instruction,
            message_id="MSG-PG-FED-UG-RW-001",
            key_id="KEY-UG-01",
            issuer_installation_id="INSTALL-UG-01",
            issuer_institution_id="BANK-UG-01",
            receiver_installation_id="INSTALL-RW-01",
            receiver_institution_id="BANK-RW-01",
            nonce="NONCE-PG-FED-UG-RW-001",
            issued_at=self.now - 30,
            expires_at=self.now + 90,
            private_key=self.sender_private,
        )
        self.sender_outbox.enqueue(
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            envelope=self.envelope,
        )

    def tearDown(self):
        for schema in (self.sender_schema, self.receiver_schema):
            self.admin.execute(
                self.sql.SQL("DROP SCHEMA {} CASCADE").format(
                    self.sql.Identifier(schema)
                )
            )
        self.admin.close()

    def _verify_receiver_receipt(self, receipt, original):
        return verify_receiver_acknowledgement(
            receipt,
            original_envelope=original,
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            sender_institution_id="BANK-UG-01",
            receiver_trust_key_loader=lambda tenant, installation, key_id: (
                self.sender_trust_for_receiver
                if (tenant, installation, key_id) ==
                   ("tenant-ug", "INSTALL-RW-01", "KEY-RW-01")
                else None
            ),
            receiver_public_key_loader=lambda ref: (
                self.receiver_public if ref == "keyref:rw-receiver" else b""
            ),
            clock=lambda: self.now,
        )

    def test_two_isolated_installations_complete_signed_postgresql_federation(self):
        claimed = self.sender_outbox.claim(
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            message_id="MSG-PG-FED-UG-RW-001",
            worker_id="ug-outbox-worker-1",
        )
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["attempt_count"], 1)

        response = self.receiver.receive(claimed["envelope"])
        self.assertTrue(response["message_verified"])
        self.assertFalse(response["origin_authority_inherited"])
        self.assertFalse(response["receiver_execution_authorised_by_origin"])
        self.assertEqual(response["receiver_decision"]["protocol_decision"], "PASSED")
        self.assertEqual(response["receiver_runtime_pins"]["tenant_id"], "tenant-rw")
        self.assertEqual(response["receiver_runtime_pins"]["institution_id"], "BANK-RW-01")
        self.assertEqual(response["receiver_runtime_pins"]["minah_deployment_id"], "MINAH-RW-01")
        self.assertIn("receiver_acknowledgement", response)

        acknowledged = self.sender_outbox.acknowledge(
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            message_id="MSG-PG-FED-UG-RW-001",
            receipt=response["receiver_acknowledgement"],
        )
        self.assertTrue(acknowledged["acknowledged"])
        self.assertFalse(acknowledged["duplicate"])

        # Simulate process replacement: construct fresh store adapters over
        # the same independent schemas and redeliver the exact signed envelope.
        restarted_inbox = PostgreSQLFederationInbox(self.receiver_connect)

        # Replace the complete receiving execution runtime as well as its
        # inbox adapter. Both CEE lifecycle and IVM decision history must be
        # recovered from the receiver's durable PostgreSQL installation store.
        self.receiver_runtime.pipeline.close()
        self.receiver_runtime.pipeline = KinSysProtocolPipeline(
            minah_profile=self.receiver_runtime.profile,
            cee_journal=PostgreSQLCEEJournal(self.receiver_connect),
            ivm_store=PostgreSQLProtocolStore(self.receiver_connect),
            identity_provider_id=self.receiver_runtime.idp_id,
            identity_provider_version="1",
            jurisdiction_package_id=self.receiver_runtime.jurisdiction_package,
            jurisdiction_package_version="1",
            authority_profile_version=self.receiver_runtime.authority_version,
        )
        self.receiver_runtime.service = InstitutionalInstructionExecutionService(
            runtime_binding_resolver=self.receiver_runtime,
            pipeline_factory=lambda _context: self.receiver_runtime.pipeline,
        )

        restarted_receiver = InstitutionalFederatedInstructionReceiver(
            receiver_tenant_id="tenant-rw",
            receiver_installation_id="INSTALL-RW-01",
            receiver_institution_id="BANK-RW-01",
            receiver_binding_id="BIND-RW-01",
            execution_service=self.receiver_runtime.service,
            trust_key_loader=self.receiver.trust_key_loader,
            public_key_loader=self.receiver.public_key_loader,
            clock=lambda: self.now,
            inbox=restarted_inbox,
            inbox_worker_id="rw-inbox-worker-after-restart",
            inbox_lease_seconds=30,
            receiver_signing_key_id="KEY-RW-01",
            receiver_signing_key_loader=self.receiver.receiver_signing_key_loader,
        )
        replayed = restarted_receiver.receive(claimed["envelope"])
        self.assertTrue(replayed["duplicate_delivery"])
        self.assertEqual(
            replayed["receiver_acknowledgement"],
            response["receiver_acknowledgement"],
        )

        duplicate_ack = self.sender_outbox.acknowledge(
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            message_id="MSG-PG-FED-UG-RW-001",
            receipt=replayed["receiver_acknowledgement"],
        )
        self.assertTrue(duplicate_ack["duplicate"])
        sender_state = self.sender_outbox.get(
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            message_id="MSG-PG-FED-UG-RW-001",
        )
        self.assertEqual(sender_state["status"], "ACKNOWLEDGED")
        receiver_state = restarted_inbox.get(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id="MSG-PG-FED-UG-RW-001",
        )
        self.assertEqual(receiver_state["status"], "COMPLETED")

        receiver_operation = self.receiver_runtime.pipeline.cee.get(
            "OP-PG-FED-UG-RW-001"
        )
        self.assertIsNotNone(receiver_operation)
        self.assertEqual(receiver_operation["phase"], "FINALIZED")
        self.assertEqual(receiver_operation["outcome"], "COMMITTED")
        self.assertIsNone(
            self.sender_runtime.pipeline.cee.get("OP-PG-FED-UG-RW-001")
        )
        self.assertNotEqual(
            self.sender_runtime.pipeline.cee,
            self.receiver_runtime.pipeline.cee,
        )


if __name__ == "__main__":
    unittest.main()
