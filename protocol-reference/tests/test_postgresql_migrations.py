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
    apply_migrations,
)


class _FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None, **kwargs):
        normalized = " ".join(sql.split()).upper()
        if normalized.startswith("SELECT VERSION, FILENAME, CHECKSUM_SHA256"):
            self.rows = [
                (version, row["filename"], row["checksum"])
                for version, row in sorted(self.connection.applied.items())
            ]
        elif normalized.startswith("INSERT INTO KIN_SYS_SCHEMA_MIGRATIONS"):
            version, filename, checksum = params
            if version in self.connection.applied:
                raise AssertionError("duplicate migration insert")
            self.connection.applied[version] = {
                "filename": filename, "checksum": checksum,
            }
        elif normalized.startswith("SELECT PG_ADVISORY_XACT_LOCK"):
            self.rows = []
        else:
            # DDL and migration SQL are accepted by this behavioral test double.
            self.rows = []

    def fetchall(self):
        return list(self.rows)


class _FakeTransaction:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        self.snapshot = dict(self.connection.applied)
        self.connection.transaction_count += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.connection.applied = self.snapshot
        return False


class _FakeConnection:
    def __init__(self):
        self.applied = {}
        self.transaction_count = 0

    def transaction(self):
        return _FakeTransaction(self)

    def cursor(self):
        return _FakeCursor(self)


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

    def test_apply_migrations_is_idempotent_and_records_checksum(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "001_first.sql").write_text("CREATE TABLE first_item (id INT);", encoding="utf-8")
            connection = _FakeConnection()
            self.assertEqual(apply_migrations(connection, root), ["001"])
            self.assertEqual(apply_migrations(connection, root), [])
            self.assertEqual(connection.transaction_count, 2)
            self.assertEqual(
                connection.applied["001"]["checksum"],
                migration_checksum("CREATE TABLE first_item (id INT);"),
            )

    def test_apply_migrations_rejects_changed_applied_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            migration = root / "001_first.sql"
            migration.write_text("CREATE TABLE first_item (id INT);", encoding="utf-8")
            connection = _FakeConnection()
            apply_migrations(connection, root)
            migration.write_text("CREATE TABLE first_item (id BIGINT);", encoding="utf-8")
            with self.assertRaisesRegex(MigrationError, "CHECKSUM_MISMATCH"):
                apply_migrations(connection, root)
            self.assertEqual(connection.applied["001"]["checksum"],
                             migration_checksum("CREATE TABLE first_item (id INT);"))

    def test_apply_migrations_rolls_back_earlier_migration_records_on_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "001_first.sql").write_text("CREATE TABLE first_item (id INT);", encoding="utf-8")
            (root / "002_second.sql").write_text("COMMIT;", encoding="utf-8")
            connection = _FakeConnection()
            with self.assertRaisesRegex(MigrationError, "MUST_NOT_CONTROL_TRANSACTION"):
                apply_migrations(connection, root)
            self.assertEqual(connection.applied, {})

    def test_repository_migration_is_transaction_neutral(self):
        migrations = discover_migrations()
        self.assertTrue(migrations)
        for _, path in migrations:
            _assert_transaction_neutral(path.read_text(encoding="utf-8"), path.name)


if __name__ == "__main__":
    unittest.main()
