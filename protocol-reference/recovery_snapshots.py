# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Best-effort, read-only SQLite snapshot pairs for recovery review.

The CEE and IVM stores are separate databases. SQLite's backup API gives each
store a consistent individual snapshot, but cannot make the pair one atomic
cross-database snapshot. The manifest states that limitation explicitly.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SNAPSHOT_SCHEMA_VERSION = "kin-sys-recovery-snapshot-set-0.1"
_STORE_TABLES = {
    "cee": {"cee_operations", "cee_events", "cee_observations"},
    "ivm": {"processes", "events", "transition_history"},
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_descriptor(connection: sqlite3.Connection) -> dict[str, str]:
    rows = connection.execute("PRAGMA database_list").fetchall()
    main = next((row for row in rows if row[1] == "main"), None)
    filename = main[2] if main else ""
    return {"kind": "FILE" if filename else "MEMORY", "database_role": "main"}


def _snapshot_one(
    source: sqlite3.Connection,
    target: Path,
    expected_tables: set[str],
) -> dict[str, Any]:
    started = datetime.now(timezone.utc).isoformat()
    destination = sqlite3.connect(str(target))
    try:
        source.backup(destination)
        destination.commit()
        integrity_rows = destination.execute("PRAGMA integrity_check").fetchall()
        integrity_ok = len(integrity_rows) == 1 and integrity_rows[0][0] == "ok"
        if not integrity_ok:
            raise RuntimeError("SNAPSHOT_INTEGRITY_CHECK_FAILED")
        schema_rows = destination.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
        table_names = {row[1] for row in schema_rows if row[0] == "table"}
        missing = sorted(expected_tables - table_names)
        if missing:
            raise RuntimeError("SNAPSHOT_SCHEMA_INCOMPLETE")
        row_counts = {}
        for table in sorted(expected_tables):
            # Table names are fixed allow-listed constants, not caller input.
            row_counts[table] = destination.execute(
                'SELECT COUNT(*) FROM "' + table + '"'
            ).fetchone()[0]
        schema_digest = hashlib.sha256(_canonical([
            {"type": row[0], "name": row[1], "sql": row[2]} for row in schema_rows
        ])).hexdigest()
        user_version = destination.execute("PRAGMA user_version").fetchone()[0]
        application_id = destination.execute("PRAGMA application_id").fetchone()[0]
    finally:
        destination.close()
    finished = datetime.now(timezone.utc).isoformat()
    file_digest = _file_digest(target)
    size_bytes = target.stat().st_size
    os.chmod(target, 0o444)
    return {
        "capture_started_at": started,
        "capture_finished_at": finished,
        "integrity": "OK",
        "schema_digest": schema_digest,
        "user_version": user_version,
        "application_id": application_id,
        "row_counts": row_counts,
        "file_size_bytes": size_bytes,
        "sha256": file_digest,
        "filename": target.name,
    }


def create_recovery_snapshot_set(
    pipeline: Any,
    destination: str | os.PathLike[str],
    operation_id: str,
) -> dict[str, Any]:
    """Create two individually consistent read-only SQLite snapshots and a manifest.

    The destination must not already exist. A staging directory is atomically
    renamed into place only after both backups and the manifest are complete.
    """
    destination_path = Path(destination)
    parent = destination_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if destination_path.exists():
        raise FileExistsError("SNAPSHOT_DESTINATION_ALREADY_EXISTS")
    stage = Path(tempfile.mkdtemp(prefix=".kin-sys-snapshot-", dir=str(parent)))
    capture_started = datetime.now(timezone.utc).isoformat()
    try:
        cee_source = pipeline.cee.db
        ivm_source = pipeline.ivm.db
        stores = {
            "cee": {
                "source": _source_descriptor(cee_source),
                "snapshot": _snapshot_one(
                    cee_source, stage / "cee.sqlite3", _STORE_TABLES["cee"]
                ),
            },
            "ivm": {
                "source": _source_descriptor(ivm_source),
                "snapshot": _snapshot_one(
                    ivm_source, stage / "ivm.sqlite3", _STORE_TABLES["ivm"]
                ),
            },
        }
        manifest: dict[str, Any] = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "operation_id": operation_id,
            "capture_started_at": capture_started,
            "capture_finished_at": datetime.now(timezone.utc).isoformat(),
            "capture_mode": "SEQUENTIAL_INDIVIDUAL_SQLITE_BACKUPS",
            "pair_atomicity": "NOT_ATOMIC_ACROSS_CEE_AND_IVM",
            "pair_consistency": "NOT_EVALUATED_BY_SNAPSHOT_CREATION",
            "sqlite_library_version": sqlite3.sqlite_version,
            "stores": stores,
            "limitations": [
                "Each database is an individually consistent SQLite backup; the pair is not a single atomic cross-store snapshot.",
                "The manifest records capture windows but does not prove both stores represent the same instant.",
                "File hashes detect later byte changes relative to this manifest; they do not prove source authenticity or prevent an attacker replacing both files and manifest.",
                "No trusted timestamp, digital signature, external immutable storage, backup provenance, or independent custodian is provided.",
                "SQLite integrity_check is structural validation, not malicious-tamper detection.",
            ],
        }
        manifest["manifest_digest"] = hashlib.sha256(_canonical(manifest)).hexdigest()
        manifest_path = stage / "manifest.json"
        manifest_path.write_bytes(_canonical(manifest) + b"\n")
        os.chmod(manifest_path, 0o444)
        os.replace(stage, destination_path)
        return {
            "status": "SNAPSHOT_SET_CREATED",
            "destination": str(destination_path),
            "manifest": manifest,
        }
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def verify_recovery_snapshot_set(directory: str | os.PathLike[str]) -> dict[str, Any]:
    """Verify manifest digest, snapshot file hashes, schema integrity and table counts."""
    root = Path(directory)
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        claimed = manifest.get("manifest_digest")
        payload = {key: value for key, value in manifest.items() if key != "manifest_digest"}
        actual = hashlib.sha256(_canonical(payload)).hexdigest()
        if not isinstance(claimed, str) or claimed != actual:
            return {"status": "MANIFEST_DIGEST_MISMATCH", "stores": {}}
        results: dict[str, Any] = {}
        for role, expected_tables in _STORE_TABLES.items():
            record = manifest["stores"][role]["snapshot"]
            path = root / record["filename"]
            if not path.is_file() or _file_digest(path) != record["sha256"]:
                results[role] = {"status": "FILE_DIGEST_MISMATCH"}
                continue
            connection = sqlite3.connect("file:" + str(path.resolve()) + "?mode=ro", uri=True)
            try:
                integrity = connection.execute("PRAGMA integrity_check").fetchall()
                if len(integrity) != 1 or integrity[0][0] != "ok":
                    results[role] = {"status": "INTEGRITY_FAILED"}
                    continue
                counts = {
                    table: connection.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0]
                    for table in sorted(expected_tables)
                }
                results[role] = {
                    "status": "VERIFIED" if counts == record["row_counts"] else "ROW_COUNT_MISMATCH",
                    "row_counts": counts,
                }
            finally:
                connection.close()
        overall = "VERIFIED" if all(item["status"] == "VERIFIED" for item in results.values()) else "FAILED"
        return {"status": overall, "manifest_digest": claimed, "stores": results}
    except Exception:
        return {"status": "UNAVAILABLE", "stores": {}}
