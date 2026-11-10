# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""PostgreSQL integration tests for durable federation inbox transitions."""
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
from postgresql_federation_inbox import (
    FederationInboxError,
    PostgreSQLFederationInbox,
)


@unittest.skipUnless(DSN, "KIN_SYS_POSTGRES_TEST_DSN is not configured")
class PostgreSQLFederationInboxIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        cls.psycopg, cls.sql = psycopg, sql

    def setUp(self):
        self.schema = "kin_sys_inbox_" + uuid.uuid4().hex
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
        self.inbox_a = PostgreSQLFederationInbox(self.connect)
        self.inbox_b = PostgreSQLFederationInbox(self.connect)
        self.message = {
            "claims": {
                "message_id": "MSG-UG-RW-001",
                "operation_id": "OP-UG-RW-001",
                "issuer_installation_id": "INSTALL-UG-01",
            },
            "signature": "synthetic-test-envelope",
        }
        self.accept()

    def tearDown(self):
        self.admin.execute(
            self.sql.SQL("DROP SCHEMA {} CASCADE").format(self.sql.Identifier(self.schema))
        )
        self.admin.close()

    def accept(self, envelope=None):
        return self.inbox_a.accept_verified(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id="MSG-UG-RW-001",
            operation_id="OP-UG-RW-001",
            idempotency_key="IDEM-UG-RW-001",
            envelope=self.message if envelope is None else envelope,
        )

    def claim(self, worker):
        return self.inbox_a.claim(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id="MSG-UG-RW-001",
            worker_id=worker,
            lease_seconds=30,
        )

    def test_accept_is_idempotent_for_identical_verified_delivery(self):
        first = self.accept()
        second = self.accept()
        self.assertTrue(first["inserted"])
        self.assertFalse(second["inserted"])
        self.assertEqual(second["status"], "RECEIVED")

    def test_message_or_idempotency_reuse_with_different_content_is_rejected(self):
        altered = {**self.message, "claims": {**self.message["claims"], "operation_id": "OP-OTHER"}}
        with self.assertRaisesRegex(FederationInboxError, "IDEMPOTENCY_CONFLICT"):
            self.accept(altered)

    def test_only_one_concurrent_worker_can_claim_a_delivery(self):
        with ThreadPoolExecutor(max_workers=10) as pool:
            claims = list(pool.map(self.claim, [f"worker-{i}" for i in range(10)]))
        self.assertEqual(sum(item is not None for item in claims), 1)

    def test_only_current_lease_owner_can_complete(self):
        claim = self.claim("worker-a")
        self.assertIsNotNone(claim)
        wrong_owner = self.inbox_b.complete(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id="MSG-UG-RW-001",
            worker_id="worker-b",
            decision_ref="DECISION-RW-001",
            response={"protocol_decision": "PASSED"},
        )
        self.assertFalse(wrong_owner)
        completed = self.inbox_a.complete(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id="MSG-UG-RW-001",
            worker_id="worker-a",
            decision_ref="DECISION-RW-001",
            response={"protocol_decision": "PASSED"},
        )
        self.assertTrue(completed)
        self.assertEqual(self.inbox_b.get(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id="MSG-UG-RW-001",
        )["status"], "COMPLETED")
        self.assertIsNone(self.claim("worker-c"))

    def test_retryable_failure_can_be_reclaimed(self):
        self.assertIsNotNone(self.claim("worker-a"))
        self.assertTrue(self.inbox_a.fail(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id="MSG-UG-RW-001",
            worker_id="worker-a",
            error_code="RECEIVER_TEMPORARILY_UNAVAILABLE",
            retryable=True,
        ))
        retry = self.claim("worker-b")
        self.assertIsNotNone(retry)
        self.assertEqual(retry["attempt_count"], 2)

    def test_expired_lease_can_be_reclaimed_by_another_worker(self):
        first = self.inbox_a.claim(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id="MSG-UG-RW-001",
            worker_id="worker-a",
            lease_seconds=1,
        )
        self.assertIsNotNone(first)
        import time
        time.sleep(1.1)
        second = self.claim("worker-b")
        self.assertIsNotNone(second)
        self.assertEqual(second["attempt_count"], 2)


if __name__ == "__main__":
    unittest.main()
