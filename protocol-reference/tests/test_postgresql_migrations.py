# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Unit tests for PostgreSQL migration discovery and safety checks."""
from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from postgresql_migrations import (
    MigrationError,
    _assert_transaction_neutral,
    discover_migrations,
    migration_checksum,
)


class PostgreSQLMigrationTests(unittest.TestCase):
    def test_discovers_migrations_in_version_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "002_second.sql").write_text("SELECT 2;", encoding="utf-8")
            (root / "001_first.sql").write_text("SELECT 1;", encoding="utf-8")
            self.assertEqual(
                [(version, path.name) for version, path in discover_migrations(root)],
                [("001", "001_first.sql"), ("002", "002_second.sql")],
            )

    def test_rejects_migration_filename_without_version_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "first.sql").write_text("SELECT 1;", encoding="utf-8")
            with self.assertRaisesRegex(MigrationError, "FILENAME_INVALID"):
                discover_migrations(root)

    def test_rejects_duplicate_version_even_with_different_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "001_first.sql").write_text("SELECT 1;", encoding="utf-8")
            (root / "001_second.sql").write_text("SELECT 2;", encoding="utf-8")
            with self.assertRaisesRegex(MigrationError, "VERSION_DUPLICATE"):
                discover_migrations(root)

    def test_transaction_control_must_belong_to_runner(self):
        for statement in ("BEGIN;", "COMMIT;", "ROLLBACK;"):
            with self.subTest(statement=statement):
                with self.assertRaisesRegex(MigrationError, "MUST_NOT_CONTROL_TRANSACTION"):
                    _assert_transaction_neutral(statement, "001_test.sql")

    def test_migration_checksum_is_stable_and_content_sensitive(self):
        first = migration_checksum("CREATE TABLE example (id INT);")
        self.assertEqual(first, migration_checksum("CREATE TABLE example (id INT);"))
        self.assertNotEqual(first, migration_checksum("CREATE TABLE example (id BIGINT);"))

    def test_repository_migration_is_transaction_neutral(self):
        migrations = discover_migrations()
        self.assertTrue(migrations)
        for _, path in migrations:
            _assert_transaction_neutral(path.read_text(encoding="utf-8"), path.name)


if __name__ == "__main__":
    unittest.main()
