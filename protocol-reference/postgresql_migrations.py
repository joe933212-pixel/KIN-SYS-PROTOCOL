# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Transactional PostgreSQL schema migration runner for KIN-SYS.

Applies versioned SQL under a database advisory lock, records SHA-256
checksums, and rejects edits to migrations that have already been applied.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Any

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations" / "postgresql"
MIGRATION_NAME = re.compile(r"^(?P<version>[0-9]{3})_[a-z0-9_]+\.sql$")
ADVISORY_LOCK_ID = 0x4B494E535953


class MigrationError(RuntimeError):
    """A migration is unsafe, inconsistent, or cannot be applied."""


def migration_checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[tuple[str, Path]]:
    """Return validated, ordered migration files; reject ambiguous versions."""
    if not directory.is_dir():
        raise MigrationError("POSTGRES_MIGRATIONS_DIRECTORY_MISSING")
    found: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise MigrationError("POSTGRES_MIGRATION_FILENAME_INVALID:" + path.name)
        version = match.group("version")
        if version in seen:
            raise MigrationError("POSTGRES_MIGRATION_VERSION_DUPLICATE:" + version)
        seen.add(version)
        found.append((version, path))
    return found


def _assert_transaction_neutral(sql: str, filename: str) -> None:
    """Keep transaction ownership in this runner, not inside migration files."""
    if re.search(r"(?im)^\s*(BEGIN|COMMIT|ROLLBACK)\s*;\s*(?:--.*)?$", sql):
        raise MigrationError("POSTGRES_MIGRATION_MUST_NOT_CONTROL_TRANSACTION:" + filename)
    if not sql.strip():
        raise MigrationError("POSTGRES_MIGRATION_EMPTY:" + filename)


def apply_migrations(connection: Any, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations atomically while holding a transaction advisory lock.

    The connection must support psycopg 3's transaction context and cursor API.
    The caller owns connection lifetime.
    """
    migrations = discover_migrations(directory)
    applied_now: list[str] = []
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_ID,))
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS kin_sys_schema_migrations (
                    version TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    checksum_sha256 TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                "SELECT version, filename, checksum_sha256 "
                "FROM kin_sys_schema_migrations ORDER BY version"
            )
            existing = {
                row[0]: {"filename": row[1], "checksum": row[2]}
                for row in cursor.fetchall()
            }
            for version, path in migrations:
                sql = path.read_text(encoding="utf-8")
                _assert_transaction_neutral(sql, path.name)
                checksum = migration_checksum(sql)
                prior = existing.get(version)
                if prior is not None:
                    if prior["filename"] != path.name or prior["checksum"] != checksum:
                        raise MigrationError(
                            "POSTGRES_MIGRATION_CHECKSUM_MISMATCH:" + version
                        )
                    continue
                cursor.execute(sql, prepare=False)
                cursor.execute(
                    "INSERT INTO kin_sys_schema_migrations "
                    "(version, filename, checksum_sha256) VALUES (%s, %s, %s)",
                    (version, path.name, checksum),
                )
                applied_now.append(version)
    return applied_now


def main() -> int:
    dsn = os.environ.get("KIN_SYS_POSTGRES_DSN")
    if not dsn:
        print("KIN_SYS_POSTGRES_DSN is required", file=sys.stderr)
        return 2
    try:
        import psycopg
    except ImportError:
        print("Install optional driver psycopg[binary]>=3.2,<4", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(dsn) as connection:
            applied = apply_migrations(connection)
        print("Applied migrations: " + (", ".join(applied) if applied else "none"))
        return 0
    except Exception as exc:
        print("PostgreSQL migration failed: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
