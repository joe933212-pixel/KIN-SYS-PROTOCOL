# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Conventional durable baseline for matched local transition experiments.

This implementation intentionally uses ordinary SQLite transactions and a
separate schema. It is a comparison target, not part of the KIN-SYS runtime.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any


class ConventionalSQLiteBaseline:
    def __init__(self, database: str):
        self.db = sqlite3.connect(database, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS baseline_objects (
                object_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 0,
                authority TEXT NOT NULL,
                authority_active INTEGER NOT NULL CHECK (authority_active IN (0, 1)),
                rule_set TEXT NOT NULL,
                rule_version TEXT NOT NULL,
                eligible INTEGER NOT NULL CHECK (eligible IN (0, 1))
            );
            CREATE TABLE IF NOT EXISTS baseline_operations (
                operation_id TEXT PRIMARY KEY,
                object_id TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                FOREIGN KEY(object_id) REFERENCES baseline_objects(object_id)
            );
            CREATE TABLE IF NOT EXISTS baseline_audit (
                operation_id TEXT PRIMARY KEY,
                object_id TEXT NOT NULL,
                old_state TEXT NOT NULL,
                new_state TEXT NOT NULL,
                authority TEXT NOT NULL,
                rule_set TEXT NOT NULL,
                rule_version TEXT NOT NULL,
                FOREIGN KEY(operation_id) REFERENCES baseline_operations(operation_id),
                FOREIGN KEY(object_id) REFERENCES baseline_objects(object_id)
            );
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def create_object(
        self, object_id: str, *, authority: str = "AUTHORITY-001",
        authority_active: bool = True, rule_set: str = "LICENCE-RULES",
        rule_version: str = "1.0", eligible: bool = True,
    ) -> None:
        self.db.execute(
            """INSERT INTO baseline_objects
               (object_id, state, version, authority, authority_active,
                rule_set, rule_version, eligible)
               VALUES (?, 'PENDING', 0, ?, ?, ?, ?, ?)""",
            (object_id, authority, int(authority_active), rule_set,
             rule_version, int(eligible)),
        )
        self.db.commit()

    def set_authority_active(self, object_id: str, active: bool) -> None:
        self.db.execute(
            "UPDATE baseline_objects SET authority_active = ? WHERE object_id = ?",
            (int(active), object_id),
        )
        self.db.commit()

    def set_rule(self, object_id: str, *, version: str, eligible: bool) -> None:
        self.db.execute(
            "UPDATE baseline_objects SET rule_version = ?, eligible = ? WHERE object_id = ?",
            (version, int(eligible), object_id),
        )
        self.db.commit()

    def object(self, object_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM baseline_objects WHERE object_id = ?", (object_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"OBJECT_NOT_FOUND:{object_id}")
        return dict(row)

    def operation(self, operation_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM baseline_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        return dict(row) if row else None

    def history(self, object_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM baseline_audit WHERE object_id = ? ORDER BY rowid",
            (object_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _digest(request: dict[str, Any]) -> str:
        encoded = json.dumps(
            request, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def execute(self, request: dict[str, Any], *, fail_before_commit: bool = False) -> dict[str, Any]:
        required = {
            "event_id", "process_id", "actor_id", "event_type",
            "rule_set", "rule_version",
        }
        if not isinstance(request, dict) or required - set(request):
            raise ValueError("INVALID_REQUEST_FIELDS")
        if any(not isinstance(request[key], str) or not request[key] for key in required):
            raise ValueError("INVALID_REQUEST_FIELD_VALUE")

        operation_id = request["event_id"]
        object_id = request["process_id"]
        digest = self._digest(request)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            prior = self.db.execute(
                "SELECT * FROM baseline_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if prior:
                if prior["request_hash"] != digest:
                    raise ValueError("OPERATION_ID_CONFLICT")
                audit = self.db.execute(
                    "SELECT * FROM baseline_audit WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                self.db.commit()
                return {
                    "status": prior["status"], "reason": prior["reason"],
                    "duplicate": True, "event_id": operation_id,
                    "from_state": audit["old_state"] if audit else None,
                    "to_state": audit["new_state"] if audit else None,
                }

            obj = self.db.execute(
                "SELECT * FROM baseline_objects WHERE object_id = ?", (object_id,)
            ).fetchone()
            if obj is None:
                raise KeyError(f"OBJECT_NOT_FOUND:{object_id}")

            reason = None
            if request["actor_id"] != obj["authority"]:
                reason = "AUTHORITY_MISMATCH"
            elif not obj["authority_active"]:
                reason = "AUTHORITY_REVOKED"
            elif request["rule_set"] != obj["rule_set"] or request["rule_version"] != obj["rule_version"]:
                reason = "RULE_VERSION_MISMATCH"
            elif not obj["eligible"]:
                reason = "RULE_REJECTED"
            elif request["event_type"] != "ACTIVATE" or obj["state"] != "PENDING":
                reason = "UNDEFINED_TRANSITION"

            if reason:
                self.db.execute(
                    """INSERT INTO baseline_operations
                       (operation_id, object_id, request_hash, status, reason)
                       VALUES (?, ?, ?, 'REJECTED', ?)""",
                    (operation_id, object_id, digest, reason),
                )
                self.db.commit()
                return {
                    "status": "REJECTED", "reason": reason,
                    "duplicate": False, "event_id": operation_id,
                    "from_state": None, "to_state": None,
                }

            old_state = obj["state"]
            new_state = "ACTIVE"
            self.db.execute(
                """INSERT INTO baseline_operations
                   (operation_id, object_id, request_hash, status, reason)
                   VALUES (?, ?, ?, 'ACCEPTED', NULL)""",
                (operation_id, object_id, digest),
            )
            changed = self.db.execute(
                """UPDATE baseline_objects SET state = ?, version = version + 1
                   WHERE object_id = ? AND state = ? AND version = ?""",
                (new_state, object_id, old_state, obj["version"]),
            ).rowcount
            if changed != 1:
                raise RuntimeError("CONCURRENT_STATE_CHANGE")
            self.db.execute(
                """INSERT INTO baseline_audit
                   (operation_id, object_id, old_state, new_state, authority,
                    rule_set, rule_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (operation_id, object_id, old_state, new_state, obj["authority"],
                 obj["rule_set"], obj["rule_version"]),
            )
            if fail_before_commit:
                raise RuntimeError("INJECTED_FAILURE_BEFORE_COMMIT")
            self.db.commit()
            return {
                "status": "ACCEPTED", "reason": None, "duplicate": False,
                "event_id": operation_id, "from_state": old_state,
                "to_state": new_state,
            }
        except Exception:
            self.db.rollback()
            raise
