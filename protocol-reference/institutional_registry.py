# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Tenant-scoped SQLite reference registry for institutional deployment records.

The caller must derive tenant_id from an authenticated, authorised principal.
This store enforces tenant predicates; it does not authenticate the caller or
claim production-grade isolation.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

RESOURCE_TYPES = {"institution", "jurisdiction", "minah", "binding"}


class RegistryError(ValueError):
    """Invalid registry operation or missing tenant-scoped resource."""


class RegistryConflict(RegistryError):
    """An immutable record version or event identifier was reused."""


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RegistryError("VALUE_NOT_CANONICALISABLE") from exc


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class InstitutionalRegistry:
    """Append-only, tenant-scoped record and lifecycle-event store."""

    def __init__(self, database: str):
        self.db = sqlite3.connect(database, timeout=10.0, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS institutional_records (
          tenant_id TEXT NOT NULL, resource_type TEXT NOT NULL,
          resource_id TEXT NOT NULL, version TEXT NOT NULL, status TEXT NOT NULL,
          payload_json TEXT NOT NULL, payload_digest TEXT NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY (tenant_id, resource_type, resource_id, version)
        );
        CREATE INDEX IF NOT EXISTS institutional_records_lookup
          ON institutional_records(tenant_id, resource_type, resource_id);
        CREATE TABLE IF NOT EXISTS institutional_events (
          tenant_id TEXT NOT NULL, event_id TEXT NOT NULL,
          resource_type TEXT NOT NULL, resource_id TEXT NOT NULL, version TEXT NOT NULL,
          event_type TEXT NOT NULL, actor_ref TEXT NOT NULL, evidence_ref TEXT,
          payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY (tenant_id, event_id),
          FOREIGN KEY (tenant_id, resource_type, resource_id, version)
            REFERENCES institutional_records(tenant_id, resource_type, resource_id, version)
        );
        CREATE INDEX IF NOT EXISTS institutional_events_lookup
          ON institutional_events(tenant_id, resource_type, resource_id);
        """)

    def close(self) -> None:
        self.db.close()

    @staticmethod
    def _check(tenant_id: str, resource_type: str, *identifiers: str) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise RegistryError("TENANT_CONTEXT_REQUIRED")
        if resource_type not in RESOURCE_TYPES:
            raise RegistryError("UNSUPPORTED_RESOURCE_TYPE")
        if any(not isinstance(x, str) or not x.strip() for x in identifiers):
            raise RegistryError("RESOURCE_IDENTIFIERS_REQUIRED")

    def create_record(self, *, tenant_id: str, resource_type: str,
                      resource_id: str, version: str, status: str,
                      payload: dict[str, Any]) -> dict[str, Any]:
        self._check(tenant_id, resource_type, resource_id, version, status)
        if not isinstance(payload, dict):
            raise RegistryError("RECORD_MUST_BE_OBJECT")
        body, digest = _json(payload), _digest(payload)
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute(
                """SELECT status,payload_digest FROM institutional_records
                   WHERE tenant_id=? AND resource_type=? AND resource_id=? AND version=?""",
                (tenant_id, resource_type, resource_id, version)).fetchone()
            if old:
                if old["status"] != status or old["payload_digest"] != digest:
                    raise RegistryConflict("IMMUTABLE_VERSION_CONFLICT")
                self.db.execute("COMMIT")
                return {"created": False, "payload_digest": digest}
            self.db.execute(
                """INSERT INTO institutional_records VALUES(?,?,?,?,?,?,?,?)""",
                (tenant_id, resource_type, resource_id, version, status, body, digest, now))
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return {"created": True, "payload_digest": digest}

    def transition_record(self, *, tenant_id: str, resource_type: str,
                          resource_id: str, expected_version: str,
                          new_version: str, event_id: str,
                          transition: dict[str, Any]) -> dict[str, Any]:
        """Atomically persist a lifecycle snapshot and its audit event.

        The caller must first obtain the transition from the lifecycle policy
        using a trusted actor assertion. This method enforces tenant scope,
        expected-current-version concurrency and snapshot/event atomicity; it
        does not authenticate actors or authorise the transition itself.
        """
        self._check(tenant_id, resource_type, resource_id, expected_version,
                    new_version, event_id)
        if not isinstance(transition, dict) or not isinstance(transition.get("record"), dict) or not isinstance(transition.get("event"), dict):
            raise RegistryError("INVALID_TRANSITION_RESULT")
        snapshot = transition["record"]
        event = transition["event"]
        if event.get("resource_type") != resource_type or event.get("resource_id") != resource_id:
            raise RegistryError("TRANSITION_RESOURCE_MISMATCH")
        if event.get("from_status") is None or event.get("to_status") != snapshot.get("status"):
            raise RegistryError("TRANSITION_STATUS_MISMATCH")
        if event.get("expected_version") not in (None, expected_version):
            raise RegistryError("TRANSITION_EXPECTED_VERSION_MISMATCH")
        event_type, actor_ref = event.get("event_type"), event.get("actor_ref")
        evidence_ref = event.get("evidence_ref")
        if not all(isinstance(x, str) and x.strip() for x in (event_type, actor_ref, evidence_ref)):
            raise RegistryError("TRANSITION_AUDIT_FIELDS_REQUIRED")
        body, digest = _json(snapshot), _digest(snapshot)
        event_body = _json(event)
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing = self.db.execute(
                """SELECT resource_type,resource_id,version,event_type,actor_ref,
                          evidence_ref,payload_json FROM institutional_events
                   WHERE tenant_id=? AND event_id=?""", (tenant_id, event_id)).fetchone()
            if existing:
                prior_record = self.db.execute(
                    """SELECT status,payload_digest FROM institutional_records
                       WHERE tenant_id=? AND resource_type=? AND resource_id=? AND version=?""",
                    (tenant_id, resource_type, resource_id, new_version)).fetchone()
                expected_event = (resource_type, resource_id, new_version, event_type,
                                  actor_ref, evidence_ref, event_body)
                actual_event = tuple(existing[k] for k in ("resource_type","resource_id",
                                "version","event_type","actor_ref","evidence_ref","payload_json"))
                if actual_event == expected_event and prior_record and prior_record["status"] == snapshot["status"] and prior_record["payload_digest"] == digest:
                    self.db.execute("COMMIT")
                    return {"created": False, "event_id": event_id, "version": new_version}
                raise RegistryConflict("EVENT_ID_REUSE_CONFLICT")
            current = self.db.execute(
                """SELECT version,status FROM institutional_records
                   WHERE tenant_id=? AND resource_type=? AND resource_id=?
                   ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (tenant_id, resource_type, resource_id)).fetchone()
            if current is None:
                raise RegistryError("TRANSITION_PARENT_NOT_FOUND")
            if current["version"] != expected_version:
                raise RegistryConflict("STALE_EXPECTED_VERSION")
            if current["status"] != event["from_status"]:
                raise RegistryConflict("STALE_EXPECTED_STATUS")
            if self.db.execute(
                """SELECT 1 FROM institutional_records WHERE tenant_id=? AND resource_type=?
                   AND resource_id=? AND version=?""",
                (tenant_id, resource_type, resource_id, new_version)).fetchone():
                raise RegistryConflict("IMMUTABLE_VERSION_CONFLICT")
            self.db.execute(
                """INSERT INTO institutional_records VALUES(?,?,?,?,?,?,?,?)""",
                (tenant_id, resource_type, resource_id, new_version,
                 snapshot["status"], body, digest, now))
            self.db.execute(
                """INSERT INTO institutional_events VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (tenant_id, event_id, resource_type, resource_id, new_version,
                 event_type, actor_ref, evidence_ref, event_body, now))
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return {"created": True, "event_id": event_id, "version": new_version}

    def get_record(self, *, tenant_id: str, resource_type: str,
                   resource_id: str, version: str) -> dict[str, Any] | None:
        self._check(tenant_id, resource_type, resource_id, version)
        row = self.db.execute(
            """SELECT status,payload_json,payload_digest,created_at
               FROM institutional_records
               WHERE tenant_id=? AND resource_type=? AND resource_id=? AND version=?""",
            (tenant_id, resource_type, resource_id, version)).fetchone()
        if row is None:
            return None
        return {"tenant_id": tenant_id, "resource_type": resource_type,
                "resource_id": resource_id, "version": version, "status": row["status"],
                "payload": json.loads(row["payload_json"]),
                "payload_digest": row["payload_digest"], "created_at": row["created_at"]}

    def get_current_record(self, *, tenant_id: str, resource_type: str,
                          resource_id: str) -> dict[str, Any] | None:
        """Load the latest tenant-scoped immutable snapshot for one resource."""
        self._check(tenant_id, resource_type, resource_id)
        row = self.db.execute(
            """SELECT version,status,payload_json,payload_digest,created_at
               FROM institutional_records
               WHERE tenant_id=? AND resource_type=? AND resource_id=?
               ORDER BY created_at DESC, rowid DESC LIMIT 1""",
            (tenant_id, resource_type, resource_id)).fetchone()
        if row is None:
            return None
        return {"tenant_id": tenant_id, "resource_type": resource_type,
                "resource_id": resource_id, "version": row["version"],
                "status": row["status"], "payload": json.loads(row["payload_json"]),
                "payload_digest": row["payload_digest"], "created_at": row["created_at"]}

    def list_records(self, *, tenant_id: str, resource_type: str,
                     resource_id: str | None = None) -> list[dict[str, Any]]:
        self._check(tenant_id, resource_type)
        sql = """SELECT resource_id,version,status,payload_digest,created_at
                 FROM institutional_records WHERE tenant_id=? AND resource_type=?"""
        args: tuple[Any, ...] = (tenant_id, resource_type)
        if resource_id is not None:
            if not isinstance(resource_id, str) or not resource_id.strip():
                raise RegistryError("INVALID_RESOURCE_ID")
            sql += " AND resource_id=?"
            args += (resource_id,)
        sql += " ORDER BY resource_id,created_at,version"
        return [dict(row) for row in self.db.execute(sql, args).fetchall()]

    def append_event(self, *, tenant_id: str, event_id: str,
                     resource_type: str, resource_id: str, version: str,
                     event_type: str, actor_ref: str, payload: dict[str, Any],
                     evidence_ref: str | None = None) -> dict[str, Any]:
        self._check(tenant_id, resource_type, event_id, resource_id,
                    version, event_type, actor_ref)
        if not isinstance(payload, dict):
            raise RegistryError("EVENT_PAYLOAD_MUST_BE_OBJECT")
        body = _json(payload)
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            parent = self.db.execute(
                """SELECT 1 FROM institutional_records
                   WHERE tenant_id=? AND resource_type=? AND resource_id=? AND version=?""",
                (tenant_id, resource_type, resource_id, version)).fetchone()
            if parent is None:
                raise RegistryError("TENANT_SCOPED_PARENT_NOT_FOUND")
            old = self.db.execute(
                """SELECT resource_type,resource_id,version,event_type,actor_ref,
                          evidence_ref,payload_json FROM institutional_events
                   WHERE tenant_id=? AND event_id=?""", (tenant_id, event_id)).fetchone()
            if old:
                expected = (resource_type, resource_id, version, event_type,
                            actor_ref, evidence_ref, body)
                actual = tuple(old[k] for k in ("resource_type","resource_id","version",
                              "event_type","actor_ref","evidence_ref","payload_json"))
                if actual != expected:
                    raise RegistryConflict("EVENT_ID_REUSE_CONFLICT")
                self.db.execute("COMMIT")
                return {"created": False, "event_id": event_id}
            self.db.execute(
                """INSERT INTO institutional_events VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (tenant_id,event_id,resource_type,resource_id,version,event_type,
                 actor_ref,evidence_ref,body,now))
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return {"created": True, "event_id": event_id}

    def list_events(self, *, tenant_id: str, resource_type: str,
                    resource_id: str) -> list[dict[str, Any]]:
        self._check(tenant_id, resource_type, resource_id)
        rows = self.db.execute(
            """SELECT event_id,version,event_type,actor_ref,evidence_ref,
                      payload_json,created_at FROM institutional_events
               WHERE tenant_id=? AND resource_type=? AND resource_id=?
               ORDER BY created_at,event_id""",
            (tenant_id, resource_type, resource_id)).fetchall()
        return [{"event_id":r["event_id"],"version":r["version"],
                 "event_type":r["event_type"],"actor_ref":r["actor_ref"],
                 "evidence_ref":r["evidence_ref"],"payload":json.loads(r["payload_json"]),
                 "created_at":r["created_at"]} for r in rows]
