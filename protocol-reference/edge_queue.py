# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Synthetic offline operation queue; replay never grants institutional authority."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any


class EdgeOperationQueue:
    """Durable local queue with idempotent enqueue and explicit reconnect checks."""

    def __init__(self, database: str = ":memory:"):
        self.db = sqlite3.connect(database, timeout=10.0)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS queued_operations (
                operation_id TEXT PRIMARY KEY,
                payload_digest TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                evidence_version TEXT NOT NULL,
                rule_version TEXT NOT NULL,
                status TEXT NOT NULL,
                replay_reason TEXT,
                replay_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS queue_events (
                event_id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL REFERENCES queued_operations(operation_id),
                event_type TEXT NOT NULL,
                detail_json TEXT NOT NULL
            );
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def enqueue(self, operation_id: str, payload: dict[str, Any], *,
                evidence_version: str, rule_version: str) -> dict[str, Any]:
        if not operation_id or not evidence_version or not rule_version:
            raise ValueError("EDGE_OPERATION_FIELDS_REQUIRED")
        if not isinstance(payload, dict):
            raise ValueError("EDGE_PAYLOAD_MUST_BE_OBJECT")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        try:
            # Serialize read/compare/insert so concurrent retries converge.
            self.db.execute("BEGIN IMMEDIATE")
            existing = self.db.execute(
                "SELECT * FROM queued_operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if existing:
                same = (existing["payload_digest"], existing["evidence_version"], existing["rule_version"]) == (
                    digest, evidence_version, rule_version
                )
                if not same:
                    raise ValueError("EDGE_OPERATION_ID_CONFLICT")
                self.db.commit()
                return self._as_dict(existing)
            self.db.execute(
                """INSERT INTO queued_operations
                   (operation_id, payload_digest, payload_json, evidence_version, rule_version, status)
                   VALUES (?, ?, ?, ?, ?, 'QUEUED_OFFLINE')""",
                (operation_id, digest, canonical, evidence_version, rule_version),
            )
            self._event(operation_id + ":QUEUED", operation_id, "QUEUED_OFFLINE", {
                "evidence_version": evidence_version, "rule_version": rule_version,
            })
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.get(operation_id)

    def replay(self, operation_id: str, *, current_evidence_version: str,
               current_rule_version: str, authority_active: bool) -> dict[str, Any]:
        """Revalidate queued context; success only means eligible for central review."""
        row = self.db.execute(
            "SELECT * FROM queued_operations WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if row is None:
            raise KeyError("EDGE_OPERATION_NOT_FOUND")
        actual_digest = hashlib.sha256(row["payload_json"].encode("utf-8")).hexdigest()
        if actual_digest != row["payload_digest"]:
            self.db.execute(
                """UPDATE queued_operations
                   SET status='QUARANTINED_PAYLOAD_INTEGRITY_FAILURE',
                       replay_reason='PAYLOAD_DIGEST_MISMATCH'
                   WHERE operation_id=?""",
                (operation_id,),
            )
            self.db.commit()
            return self.get(operation_id)
        if row["status"] == "READY_FOR_CENTRAL_REVIEW" and (
            row["evidence_version"] == current_evidence_version
            and row["rule_version"] == current_rule_version and authority_active
        ):
            return self._as_dict(row)
        if row["status"] not in {"QUEUED_OFFLINE", "READY_FOR_CENTRAL_REVIEW"}:
            # A deferred/rejected operation is immutable. Re-issue a fresh
            # operation ID with newly reviewed evidence/context instead.
            return self._as_dict(row)
        if not authority_active:
            status, reason = "REJECTED_AUTHORITY_REVOKED", "AUTHORITY_INACTIVE_AT_RECONNECT"
        elif row["rule_version"] != current_rule_version:
            status, reason = "DEFERRED_RULE_VERSION_MISMATCH", "RULE_VERSION_CHANGED_WHILE_OFFLINE"
        elif row["evidence_version"] != current_evidence_version:
            status, reason = "DEFERRED_STALE_EVIDENCE", "EVIDENCE_VERSION_CHANGED_WHILE_OFFLINE"
        else:
            status, reason = "READY_FOR_CENTRAL_REVIEW", None
        self.db.execute(
            """UPDATE queued_operations
               SET status=?, replay_reason=?, replay_count=replay_count+1
               WHERE operation_id=?""",
            (status, reason, operation_id),
        )
        self._event(
            f"{operation_id}:REPLAY:{self._next_event_number(operation_id)}",
            operation_id, "REPLAY_CHECK",
            {"status": status, "reason": reason, "authority_active": authority_active,
             "current_evidence_version": current_evidence_version,
             "current_rule_version": current_rule_version},
        )
        self.db.commit()
        return self.get(operation_id)

    def get(self, operation_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM queued_operations WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if row is None:
            raise KeyError("EDGE_OPERATION_NOT_FOUND")
        return self._as_dict(row)

    def history(self, operation_id: str) -> list[dict[str, Any]]:
        return [
            {"event_id": row["event_id"], "operation_id": row["operation_id"],
             "event_type": row["event_type"], "detail": json.loads(row["detail_json"])}
            for row in self.db.execute(
                "SELECT * FROM queue_events WHERE operation_id=? ORDER BY rowid", (operation_id,)
            )
        ]

    def _next_event_number(self, operation_id: str) -> int:
        count = self.db.execute(
            "SELECT COUNT(*) FROM queue_events WHERE operation_id=? AND event_type='REPLAY_CHECK'",
            (operation_id,),
        ).fetchone()[0]
        return count + 1

    def _event(self, event_id: str, operation_id: str, event_type: str,
               detail: dict[str, Any]) -> None:
        encoded = json.dumps(detail, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.db.execute(
            "INSERT INTO queue_events VALUES (?, ?, ?, ?)",
            (event_id, operation_id, event_type, encoded),
        )

    @staticmethod
    def _as_dict(row: sqlite3.Row) -> dict[str, Any]:
        raw_payload = row["payload_json"]
        actual_digest = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
        try:
            payload = json.loads(raw_payload)
        except (TypeError, json.JSONDecodeError):
            payload = None
        return {
            "operation_id": row["operation_id"],
            "payload": payload,
            "payload_integrity_ok": actual_digest == row["payload_digest"],
            "evidence_version": row["evidence_version"],
            "rule_version": row["rule_version"],
            "status": row["status"],
            "replay_reason": row["replay_reason"],
            "replay_count": row["replay_count"],
        }
