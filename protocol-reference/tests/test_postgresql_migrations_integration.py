# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Real PostgreSQL integration tests for the migration runner.

Set KIN_SYS_POSTGRES_TEST_DSN to enable these tests. CI provisions an isolated
PostgreSQL service; local environments without a DSN skip this test class.
"""
from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from postgresql_migrations import MigrationError, apply_migrations, discover_migrations


DSN = os.environ.get("KIN_SYS_POSTGRES_TEST_DSN")


@unittest.skipUnless(DSN, "KIN_SYS_POSTGRES_TEST_DSN is not configured")
class PostgreSQLMigrationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        cls.psycopg = psycopg
        cls.sql = sql

    def setUp(self):
        self.schema = "kin_sys_test_" + uuid.uuid4().hex
        self.admin = self.psycopg.connect(DSN, autocommit=True)
        self.admin.execute(
            self.sql.SQL("CREATE SCHEMA {}").format(self.sql.Identifier(self.schema))
        )
        self.connection = self.psycopg.connect(DSN, autocommit=True)
        self.connection.execute(
            self.sql.SQL("SET search_path TO {}").format(self.sql.Identifier(self.schema))
        )

    def tearDown(self):
        self.connection.close()
        self.admin.execute(
            self.sql.SQL("DROP SCHEMA {} CASCADE").format(self.sql.Identifier(self.schema))
        )
        self.admin.close()

    def test_repository_migration_applies_and_is_idempotent_on_postgresql(self):
        self.assertEqual(apply_migrations(self.connection), ["001", "002"])
        self.assertEqual(apply_migrations(self.connection), [])
        row = self.connection.execute(
            "SELECT version, filename, checksum_sha256 "
            "FROM kin_sys_schema_migrations"
        ).fetchone()
        self.assertEqual(row[0], "001")
        self.assertEqual(row[1], "001_institutional_minah_coordination.sql")
        versions = self.connection.execute(
            "SELECT version, filename FROM kin_sys_schema_migrations ORDER BY version"
        ).fetchall()
        self.assertEqual(
            versions,
            [
                ("001", "001_institutional_minah_coordination.sql"),
                ("002", "002_federation_nonce_guard.sql"),
            ],
        )
        tables = {
            item[0] for item in self.connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = %s",
                (self.schema,),
            ).fetchall()
        }
        self.assertIn("minah_execution_leases", tables)
        self.assertIn("minah_deployment_heads", tables)
        self.assertIn("consumed_federation_nonces", tables)

    def test_postgresql_rejects_changed_applied_migration_checksum(self):
        self.assertEqual(apply_migrations(self.connection), ["001", "002"])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = discover_migrations()[0][1].read_text(encoding="utf-8")
            (root / "001_institutional_minah_coordination.sql").write_text(
                original + "\n-- changed after application\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(MigrationError, "CHECKSUM_MISMATCH"):
                apply_migrations(self.connection, root)

    def test_postgresql_rolls_back_schema_and_history_after_later_migration_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "001_first.sql").write_text(
                "CREATE TABLE migration_probe (id BIGINT PRIMARY KEY);", encoding="utf-8"
            )
            (root / "002_broken.sql").write_text(
                "CREATE TABL definitely_invalid (id BIGINT);", encoding="utf-8"
            )
            with self.assertRaises(self.psycopg.Error):
                apply_migrations(self.connection, root)
        tables = {
            item[0] for item in self.connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = %s",
                (self.schema,),
            ).fetchall()
        }
        self.assertNotIn("migration_probe", tables)
        self.assertNotIn("kin_sys_schema_migrations", tables)


if __name__ == "__main__":
    unittest.main()
