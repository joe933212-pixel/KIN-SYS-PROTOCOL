# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Real PostgreSQL integration tests for shared nonce consumption."""
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
from postgresql_nonce_guard import PostgreSQLNonceGuard, PostgreSQLNonceGuardError


@unittest.skipUnless(DSN, "KIN_SYS_POSTGRES_TEST_DSN is not configured")
class PostgreSQLNonceGuardIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        cls.psycopg, cls.sql = psycopg, sql

    def setUp(self):
        self.schema = "kin_sys_nonce_" + uuid.uuid4().hex
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
        self.now = [1_800_000_000]
        self.guard_a = PostgreSQLNonceGuard(self.connect, clock=lambda: self.now[0])
        self.guard_b = PostgreSQLNonceGuard(self.connect, clock=lambda: self.now[0])

    def tearDown(self):
        self.admin.execute(
            self.sql.SQL("DROP SCHEMA {} CASCADE").format(self.sql.Identifier(self.schema))
        )
        self.admin.close()

    def test_nonce_is_single_use_across_independent_guard_instances(self):
        self.assertTrue(self.guard_a.consume("tenant-rw", "nonce-001", self.now[0] + 120))
        self.assertFalse(self.guard_b.consume("tenant-rw", "nonce-001", self.now[0] + 120))

    def test_nonce_scope_is_tenant_specific(self):
        self.assertTrue(self.guard_a.consume("tenant-rw", "nonce-002", self.now[0] + 120))
        self.assertTrue(self.guard_b.consume("tenant-ug", "nonce-002", self.now[0] + 120))

    def test_concurrent_workers_cannot_consume_the_same_nonce_twice(self):
        def consume(_):
            return self.guard_a.consume("tenant-rw", "nonce-race", self.now[0] + 120)
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(consume, range(12)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 11)

    def test_expired_rows_are_pruned_and_nonce_can_be_reused(self):
        self.assertTrue(self.guard_a.consume("tenant-rw", "nonce-expire", self.now[0] + 10))
        self.now[0] += 10
        self.assertTrue(self.guard_b.consume("tenant-rw", "nonce-trigger", self.now[0] + 60))
        self.assertTrue(self.guard_a.consume("tenant-rw", "nonce-expire", self.now[0] + 60))

    def test_expired_and_overlong_expiry_are_rejected(self):
        self.assertFalse(self.guard_a.consume("tenant-rw", "nonce-old", self.now[0]))
        self.assertTrue(self.guard_a.consume("tenant-rw", "nonce-skew-window", self.now[0] + 330))
        self.assertFalse(self.guard_a.consume("tenant-rw", "nonce-long", self.now[0] + 331))

    def test_database_failure_fails_closed(self):
        broken = PostgreSQLNonceGuard(lambda: (_ for _ in ()).throw(RuntimeError("offline")),
                                      clock=lambda: self.now[0])
        with self.assertRaisesRegex(PostgreSQLNonceGuardError, "STORE_UNAVAILABLE"):
            broken.consume("tenant-rw", "nonce-unavailable", self.now[0] + 60)


if __name__ == "__main__":
    unittest.main()
