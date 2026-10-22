# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Independent conventional SQLite model for deferred edge submissions."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any


class ConventionalEdgeQueue:
    """Alternative schema and code path for offline submission/reconnect checks."""

    def __init__(self, database: str = ":memory:"):
        self.db = sqlite3.connect(database, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS edge_messages (
                message_key TEXT PRIMARY KEY,
                body_hash TEXT NOT NULL,
                body TEXT NOT NULL,
                evidence_revision TEXT NOT NULL,
                ruleset_revision TEXT NOT NULL,
                queue_state TEXT NOT NULL,
                reason_code TEXT,
                attempts INTEGER NOT NULL DEFAULT 0
            );
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def enqueue(self, operation_id: str, payload: dict[str, Any], *,
                evidence_version: str, rule_version: str) -> dict[str, Any]:
        if not operation_id or not evidence_version or not rule_version:
            raise ValueError("EDGE_OPERATION_FIELDS_REQUIRED")
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(body.encode()).hexdigest()
        row = self.db.execute(
            "SELECT * FROM edge_messages WHERE message_key=?", (operation_id,)
        ).fetchone()
        if row:
            if (row["body_hash"], row["evidence_revision"], row["ruleset_revision"]) != (
                digest, evidence_version, rule_version
            ):
                raise ValueError("EDGE_OPERATION_ID_CONFLICT")
            return self._view(row)
        self.db.execute(
            "INSERT INTO edge_messages VALUES (?, ?, ?, ?, ?, 'QUEUED_OFFLINE', NULL, 0)",
            (operation_id, digest, body, evidence_version, rule_version),
        )
        self.db.commit()
        return self.get(operation_id)

    def replay(self, operation_id: str, *, current_evidence_version: str,
               current_rule_version: str, authority_active: bool) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM edge_messages WHERE message_key=?", (operation_id,)
        ).fetchone()
        if row is None:
            raise KeyError("EDGE_OPERATION_NOT_FOUND")
        if row["queue_state"] == "READY_FOR_CENTRAL_REVIEW" and (
            row["evidence_revision"] == current_evidence_version
            and row["ruleset_revision"] == current_rule_version and authority_active
        ):
            return self._view(row)
        if row["queue_state"] not in {"QUEUED_OFFLINE", "READY_FOR_CENTRAL_REVIEW"}:
            return self._view(row)
        if not authority_active:
            state, reason = "REJECTED_AUTHORITY_REVOKED", "AUTHORITY_INACTIVE_AT_RECONNECT"
        elif row["ruleset_revision"] != current_rule_version:
            state, reason = "DEFERRED_RULE_VERSION_MISMATCH", "RULE_VERSION_CHANGED_WHILE_OFFLINE"
        elif row["evidence_revision"] != current_evidence_version:
            state, reason = "DEFERRED_STALE_EVIDENCE", "EVIDENCE_VERSION_CHANGED_WHILE_OFFLINE"
        else:
            state, reason = "READY_FOR_CENTRAL_REVIEW", None
        self.db.execute(
            "UPDATE edge_messages SET queue_state=?, reason_code=?, attempts=attempts+1 WHERE message_key=?",
            (state, reason, operation_id),
        )
        self.db.commit()
        return self.get(operation_id)

    def get(self, operation_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM edge_messages WHERE message_key=?", (operation_id,)
        ).fetchone()
        if row is None:
            raise KeyError("EDGE_OPERATION_NOT_FOUND")
        return self._view(row)

    @staticmethod
    def _view(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "operation_id": row["message_key"],
            "payload": json.loads(row["body"]),
            "evidence_version": row["evidence_revision"],
            "rule_version": row["ruleset_revision"],
            "status": row["queue_state"],
            "replay_reason": row["reason_code"],
            "replay_count": row["attempts"],
        }
