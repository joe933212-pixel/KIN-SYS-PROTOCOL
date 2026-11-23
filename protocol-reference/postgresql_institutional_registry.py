# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""PostgreSQL-backed tenant-scoped institutional domain record registry.

This adapter implements the InstitutionalRegistry storage contract. It stores
immutable version snapshots and append-only lifecycle events. It does not
authenticate callers or authorise lifecycle transitions; those remain the
responsibility of the onboarding and lifecycle administration boundaries.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable

from institutional_registry import (
    RESOURCE_TYPES, RegistryConflict, RegistryError, _digest, _json,
)


class PostgreSQLInstitutionalRegistry:
    """PostgreSQL implementation of the institutional record registry contract.

    The connection factory must return a fresh, idle psycopg 3 connection.
    Each method owns and closes its connection. All writes use one PostgreSQL
    transaction, including the record snapshot and its associated audit event.
    """

    @contextmanager
    def _tenant_connection(self, tenant_id: str):
        """Run one operation with transaction-local PostgreSQL tenant context.

        RLS denies access when the setting is absent. Transaction-local
        set_config prevents tenant context leaking across pooled connections.
        The tenant ID must already have been derived from an authenticated
        institutional principal by the calling application boundary.
        """
        connection = self._connect()
        try:
            with connection.transaction():
                connection.execute(
                    "SELECT set_config('kin_sys.tenant_id', %s, true)",
                    (tenant_id,),
                )
                yield connection
        finally:
            connection.close()

    def __init__(self, connection_factory: Callable[[], Any]):
        if not callable(connection_factory):
            raise RegistryError("POSTGRES_CONNECTION_FACTORY_REQUIRED")
        self._connect = connection_factory

    @staticmethod
    def _check(tenant_id: str, resource_type: str, *identifiers: str) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise RegistryError("TENANT_CONTEXT_REQUIRED")
        if resource_type not in RESOURCE_TYPES:
            raise RegistryError("UNSUPPORTED_RESOURCE_TYPE")
        if any(not isinstance(value, str) or not value.strip() for value in identifiers):
            raise RegistryError("RESOURCE_IDENTIFIERS_REQUIRED")

    @staticmethod
    def _payload(value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    @staticmethod
    def _lock_scopes(cursor: Any, *scopes: str) -> None:
        # Acquire advisory locks in stable order to serialize resource-head
        # changes and event-id idempotency across independent connections.
        for scope in sorted(set(scopes)):
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (scope,),
            )

    @staticmethod
    def _resource_scope(tenant_id: str, resource_type: str, resource_id: str) -> str:
        return "institutional-resource:" + ":".join((tenant_id, resource_type, resource_id))

    @staticmethod
    def _event_scope(tenant_id: str, event_id: str) -> str:
        return "institutional-event:" + tenant_id + ":" + event_id

    @staticmethod
    def _event_by_id(cursor: Any, tenant_id: str, event_id: str) -> Any:
        cursor.execute(
            """SELECT resource_type,resource_id,version,event_type,actor_ref,
                      evidence_ref,payload_json
                 FROM institutional_events WHERE tenant_id=%s AND event_id=%s""",
            (tenant_id, event_id),
        )
        return cursor.fetchone()

    def close(self) -> None:
        """Compatibility no-op: connections are owned per operation."""

    def create_record_with_event(
        self, *, tenant_id: str, resource_type: str, resource_id: str,
        version: str, status: str, payload: dict[str, Any], event_id: str,
        event_type: str, actor_ref: str, evidence_ref: str,
        event_payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._check(tenant_id, resource_type, resource_id, version, status,
                    event_id, event_type, actor_ref, evidence_ref)
        if not isinstance(payload, dict) or not isinstance(event_payload, dict):
            raise RegistryError("RECORD_AND_EVENT_PAYLOADS_MUST_BE_OBJECTS")
        body, digest = _json(payload), _digest(payload)
        event_body = _json(event_payload)
        now = datetime.now(timezone.utc)
        with self._tenant_connection(tenant_id) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    self._lock_scopes(
                        cursor,
                        self._resource_scope(tenant_id, resource_type, resource_id),
                        self._event_scope(tenant_id, event_id),
                    )
                    existing = self._event_by_id(cursor, tenant_id, event_id)
                    if existing:
                        cursor.execute(
                            """SELECT status,payload_digest FROM institutional_records
                               WHERE tenant_id=%s AND resource_type=%s
                                 AND resource_id=%s AND version=%s""",
                            (tenant_id, resource_type, resource_id, version),
                        )
                        prior = cursor.fetchone()
                        expected = (resource_type, resource_id, version, event_type,
                                    actor_ref, evidence_ref, event_payload)
                        actual = tuple(existing[:6]) + (self._payload(existing[6]),)
                        if actual == expected and prior and prior[0] == status and prior[1] == digest:
                            return {"created": False, "payload_digest": digest, "event_id": event_id}
                        raise RegistryConflict("EVENT_ID_REUSE_CONFLICT")
                    cursor.execute(
                        """SELECT 1 FROM institutional_records
                           WHERE tenant_id=%s AND resource_type=%s AND resource_id=%s
                           LIMIT 1""",
                        (tenant_id, resource_type, resource_id),
                    )
                    if cursor.fetchone():
                        raise RegistryConflict("RESOURCE_ALREADY_REGISTERED")
                    cursor.execute(
                        """INSERT INTO institutional_records
                           (tenant_id,resource_type,resource_id,version,status,
                            payload_json,payload_digest,created_at)
                           VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s)""",
                        (tenant_id, resource_type, resource_id, version, status,
                         body, digest, now),
                    )
                    cursor.execute(
                        """INSERT INTO institutional_events
                           (tenant_id,event_id,resource_type,resource_id,version,
                            event_type,actor_ref,evidence_ref,payload_json,created_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                        (tenant_id, event_id, resource_type, resource_id, version,
                         event_type, actor_ref, evidence_ref, event_body, now),
                    )
        return {"created": True, "payload_digest": digest, "event_id": event_id}

    def create_record(self, *, tenant_id: str, resource_type: str,
                      resource_id: str, version: str, status: str,
                      payload: dict[str, Any]) -> dict[str, Any]:
        self._check(tenant_id, resource_type, resource_id, version, status)
        if not isinstance(payload, dict):
            raise RegistryError("RECORD_MUST_BE_OBJECT")
        body, digest = _json(payload), _digest(payload)
        with self._tenant_connection(tenant_id) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """INSERT INTO institutional_records
                           (tenant_id,resource_type,resource_id,version,status,
                            payload_json,payload_digest,created_at)
                           VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                           ON CONFLICT (tenant_id,resource_type,resource_id,version)
                           DO NOTHING RETURNING record_order""",
                        (tenant_id, resource_type, resource_id, version, status,
                         body, digest, datetime.now(timezone.utc)),
                    )
                    if cursor.fetchone():
                        return {"created": True, "payload_digest": digest}
                    cursor.execute(
                        """SELECT status,payload_digest FROM institutional_records
                           WHERE tenant_id=%s AND resource_type=%s
                             AND resource_id=%s AND version=%s""",
                        (tenant_id, resource_type, resource_id, version),
                    )
                    old = cursor.fetchone()
                    if old and old[0] == status and old[1] == digest:
                        return {"created": False, "payload_digest": digest}
                    raise RegistryConflict("IMMUTABLE_VERSION_CONFLICT")

    def transition_record(self, *, tenant_id: str, resource_type: str,
                          resource_id: str, expected_version: str,
                          new_version: str, event_id: str,
                          transition: dict[str, Any]) -> dict[str, Any]:
        self._check(tenant_id, resource_type, resource_id, expected_version,
                    new_version, event_id)
        if (not isinstance(transition, dict)
                or not isinstance(transition.get("record"), dict)
                or not isinstance(transition.get("event"), dict)):
            raise RegistryError("INVALID_TRANSITION_RESULT")
        snapshot, event = transition["record"], transition["event"]
        if event.get("resource_type") != resource_type or event.get("resource_id") != resource_id:
            raise RegistryError("TRANSITION_RESOURCE_MISMATCH")
        if event.get("from_status") is None or event.get("to_status") != snapshot.get("status"):
            raise RegistryError("TRANSITION_STATUS_MISMATCH")
        if event.get("expected_version") not in (None, expected_version):
            raise RegistryError("TRANSITION_EXPECTED_VERSION_MISMATCH")
        event_type, actor_ref, evidence_ref = (
            event.get("event_type"), event.get("actor_ref"), event.get("evidence_ref")
        )
        if not all(isinstance(x, str) and x.strip()
                   for x in (event_type, actor_ref, evidence_ref)):
            raise RegistryError("TRANSITION_AUDIT_FIELDS_REQUIRED")
        body, digest, event_body = _json(snapshot), _digest(snapshot), _json(event)
        now = datetime.now(timezone.utc)
        with self._tenant_connection(tenant_id) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    self._lock_scopes(
                        cursor,
                        self._resource_scope(tenant_id, resource_type, resource_id),
                        self._event_scope(tenant_id, event_id),
                    )
                    existing = self._event_by_id(cursor, tenant_id, event_id)
                    if existing:
                        cursor.execute(
                            """SELECT status,payload_digest FROM institutional_records
                               WHERE tenant_id=%s AND resource_type=%s
                                 AND resource_id=%s AND version=%s""",
                            (tenant_id, resource_type, resource_id, new_version),
                        )
                        prior = cursor.fetchone()
                        expected_event = (resource_type, resource_id, new_version,
                                           event_type, actor_ref, evidence_ref, event)
                        actual_event = tuple(existing[:6]) + (self._payload(existing[6]),)
                        if (actual_event == expected_event and prior
                                and prior[0] == snapshot["status"] and prior[1] == digest):
                            return {"created": False, "event_id": event_id, "version": new_version}
                        raise RegistryConflict("EVENT_ID_REUSE_CONFLICT")
                    cursor.execute(
                        """SELECT version,status FROM institutional_records
                           WHERE tenant_id=%s AND resource_type=%s AND resource_id=%s
                           ORDER BY record_order DESC LIMIT 1 FOR UPDATE""",
                        (tenant_id, resource_type, resource_id),
                    )
                    current = cursor.fetchone()
                    if current is None:
                        raise RegistryError("TRANSITION_PARENT_NOT_FOUND")
                    if current[0] != expected_version:
                        raise RegistryConflict("STALE_EXPECTED_VERSION")
                    if current[1] != event["from_status"]:
                        raise RegistryConflict("STALE_EXPECTED_STATUS")
                    cursor.execute(
                        """INSERT INTO institutional_records
                           (tenant_id,resource_type,resource_id,version,status,
                            payload_json,payload_digest,created_at)
                           VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                           ON CONFLICT (tenant_id,resource_type,resource_id,version)
                           DO NOTHING RETURNING record_order""",
                        (tenant_id, resource_type, resource_id, new_version,
                         snapshot["status"], body, digest, now),
                    )
                    if cursor.fetchone() is None:
                        raise RegistryConflict("IMMUTABLE_VERSION_CONFLICT")
                    cursor.execute(
                        """INSERT INTO institutional_events
                           (tenant_id,event_id,resource_type,resource_id,version,
                            event_type,actor_ref,evidence_ref,payload_json,created_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                        (tenant_id, event_id, resource_type, resource_id, new_version,
                         event_type, actor_ref, evidence_ref, event_body, now),
                    )
        return {"created": True, "event_id": event_id, "version": new_version}

    def get_record(self, *, tenant_id: str, resource_type: str,
                   resource_id: str, version: str) -> dict[str, Any] | None:
        self._check(tenant_id, resource_type, resource_id, version)
        with self._tenant_connection(tenant_id) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT status,payload_json,payload_digest,created_at
                       FROM institutional_records
                       WHERE tenant_id=%s AND resource_type=%s
                         AND resource_id=%s AND version=%s""",
                    (tenant_id, resource_type, resource_id, version),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return {"tenant_id": tenant_id, "resource_type": resource_type,
                "resource_id": resource_id, "version": version, "status": row[0],
                "payload": self._payload(row[1]), "payload_digest": row[2],
                "created_at": row[3].isoformat()}

    def get_current_record(self, *, tenant_id: str, resource_type: str,
                           resource_id: str) -> dict[str, Any] | None:
        self._check(tenant_id, resource_type, resource_id)
        with self._tenant_connection(tenant_id) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT version,status,payload_json,payload_digest,created_at
                       FROM institutional_records
                       WHERE tenant_id=%s AND resource_type=%s AND resource_id=%s
                       ORDER BY record_order DESC LIMIT 1""",
                    (tenant_id, resource_type, resource_id),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return {"tenant_id": tenant_id, "resource_type": resource_type,
                "resource_id": resource_id, "version": row[0], "status": row[1],
                "payload": self._payload(row[2]), "payload_digest": row[3],
                "created_at": row[4].isoformat()}

    def list_records(self, *, tenant_id: str, resource_type: str,
                     resource_id: str | None = None) -> list[dict[str, Any]]:
        self._check(tenant_id, resource_type)
        query = """SELECT resource_id,version,status,payload_digest,created_at
                   FROM institutional_records WHERE tenant_id=%s AND resource_type=%s"""
        args: tuple[Any, ...] = (tenant_id, resource_type)
        if resource_id is not None:
            if not isinstance(resource_id, str) or not resource_id.strip():
                raise RegistryError("INVALID_RESOURCE_ID")
            query += " AND resource_id=%s"
            args += (resource_id,)
        query += " ORDER BY resource_id,record_order"
        with self._tenant_connection(tenant_id) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, args)
                rows = cursor.fetchall()
        return [{"resource_id": r[0], "version": r[1], "status": r[2],
                 "payload_digest": r[3], "created_at": r[4].isoformat()} for r in rows]

    def append_event(self, *, tenant_id: str, event_id: str,
                     resource_type: str, resource_id: str, version: str,
                     event_type: str, actor_ref: str, payload: dict[str, Any],
                     evidence_ref: str | None = None) -> dict[str, Any]:
        self._check(tenant_id, resource_type, event_id, resource_id,
                    version, event_type, actor_ref)
        if evidence_ref is not None and (not isinstance(evidence_ref, str) or not evidence_ref.strip()):
            raise RegistryError("EVIDENCE_REFERENCE_INVALID")
        if not isinstance(payload, dict):
            raise RegistryError("EVENT_PAYLOAD_MUST_BE_OBJECT")
        body = _json(payload)
        with self._tenant_connection(tenant_id) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    self._lock_scopes(cursor, self._event_scope(tenant_id, event_id))
                    cursor.execute(
                        """SELECT 1 FROM institutional_records
                           WHERE tenant_id=%s AND resource_type=%s
                             AND resource_id=%s AND version=%s""",
                        (tenant_id, resource_type, resource_id, version),
                    )
                    if cursor.fetchone() is None:
                        raise RegistryError("TENANT_SCOPED_PARENT_NOT_FOUND")
                    existing = self._event_by_id(cursor, tenant_id, event_id)
                    if existing:
                        expected = (resource_type, resource_id, version, event_type,
                                    actor_ref, evidence_ref, payload)
                        actual = tuple(existing[:6]) + (self._payload(existing[6]),)
                        if actual != expected:
                            raise RegistryConflict("EVENT_ID_REUSE_CONFLICT")
                        return {"created": False, "event_id": event_id}
                    cursor.execute(
                        """INSERT INTO institutional_events
                           (tenant_id,event_id,resource_type,resource_id,version,
                            event_type,actor_ref,evidence_ref,payload_json,created_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                        (tenant_id, event_id, resource_type, resource_id, version,
                         event_type, actor_ref, evidence_ref, body, datetime.now(timezone.utc)),
                    )
        return {"created": True, "event_id": event_id}

    def list_events(self, *, tenant_id: str, resource_type: str,
                    resource_id: str) -> list[dict[str, Any]]:
        self._check(tenant_id, resource_type, resource_id)
        with self._tenant_connection(tenant_id) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT event_id,version,event_type,actor_ref,evidence_ref,
                              payload_json,created_at
                       FROM institutional_events
                       WHERE tenant_id=%s AND resource_type=%s AND resource_id=%s
                       ORDER BY event_order""",
                    (tenant_id, resource_type, resource_id),
                )
                rows = cursor.fetchall()
        return [{"event_id": r[0], "version": r[1], "event_type": r[2],
                 "actor_ref": r[3], "evidence_ref": r[4],
                 "payload": self._payload(r[5]), "created_at": r[6].isoformat()}
                for r in rows]
