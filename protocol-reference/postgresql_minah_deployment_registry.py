# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""PostgreSQL-backed institutional MINAH deployment lifecycle registry.

This registry shares the deployment head and epoch rows used by the PostgreSQL
execution coordinator. All lifecycle writes lock the head row and update the
version head, epoch, immutable version record and audit event in one transaction.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Callable

from institutional_minah_deployment import (
    MinahDeploymentConflict,
    MinahDeploymentError,
    STATUSES,
    TRANSITIONS,
    _canonical,
    _identifier,
    _validate,
)


class PostgreSQLInstitutionalMinahDeploymentRegistry:
    """PostgreSQL implementation of registration, revision and lifecycle APIs.

    connection_factory must return a fresh psycopg 3 connection. Connections
    are closed after each operation; the factory must return a fresh, idle
    connection with no open transaction. Authority/identity authentication remains
    the responsibility of InstitutionalMinahDeploymentAdministration.
    """

    def __init__(self, connection_factory: Callable[[], Any]):
        if not callable(connection_factory):
            raise MinahDeploymentError("POSTGRES_CONNECTION_FACTORY_REQUIRED")
        self._connect = connection_factory

    @staticmethod
    def _event_fields(tenant_id: str, event_id: str,
                      actor_ref: str, evidence_ref: str) -> None:
        for value, field in ((tenant_id, "tenant_id"), (event_id, "event_id"),
                             (actor_ref, "actor_ref"), (evidence_ref, "evidence_ref")):
            _identifier(value, field)

    @staticmethod
    def _digest(body: str) -> str:
        return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()

    def register(self, *, tenant_id: str, config: dict[str, Any], event_id: str,
                 actor_ref: str, evidence_ref: str) -> dict[str, Any]:
        self._event_fields(tenant_id, event_id, actor_ref, evidence_ref)
        clean = _validate(config, tenant_id)
        body, now = _canonical(clean), datetime.now(timezone.utc)
        digest = self._digest(body)
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    # Serialize first registration for a tenant/deployment,
                    # including the case where no head row exists yet.
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (tenant_id + ":" + clean["deployment_id"],),
                    )
                    cursor.execute(
                        "SELECT 1 FROM minah_deployment_heads WHERE tenant_id=%s AND deployment_id=%s",
                        (tenant_id, clean["deployment_id"]),
                    )
                    if cursor.fetchone() is not None:
                        raise MinahDeploymentConflict("MINAH_DEPLOYMENT_ALREADY_REGISTERED")
                    cursor.execute(
                        """INSERT INTO minah_deployment_versions
                           (tenant_id,deployment_id,version,status,config_json,digest,created_at)
                           VALUES (%s,%s,%s,'PENDING',%s::jsonb,%s,%s)""",
                        (tenant_id, clean["deployment_id"], clean["version"], body, digest, now),
                    )
                    cursor.execute(
                        "INSERT INTO minah_deployment_heads VALUES (%s,%s,%s)",
                        (tenant_id, clean["deployment_id"], clean["version"]),
                    )
                    cursor.execute(
                        "INSERT INTO minah_deployment_epochs VALUES (%s,%s,1)",
                        (tenant_id, clean["deployment_id"]),
                    )
                    cursor.execute(
                        "INSERT INTO minah_deployment_fence_sequences VALUES (%s,%s,0)",
                        (tenant_id, clean["deployment_id"]),
                    )
                    cursor.execute(
                        """INSERT INTO minah_deployment_events
                           (tenant_id,event_id,deployment_id,version,event_type,actor_ref,
                            evidence_ref,payload_json,created_at)
                           VALUES (%s,%s,%s,%s,'MINAH_DEPLOYMENT_REGISTERED',%s,%s,%s::jsonb,%s)""",
                        (tenant_id, event_id, clean["deployment_id"], clean["version"],
                         actor_ref, evidence_ref, _canonical({"digest": digest}), now),
                    )
        return {"deployment_id": clean["deployment_id"], "version": clean["version"],
                "status": "PENDING", "digest": digest, "execution_epoch": 1}

    def revise(self, *, tenant_id: str, deployment_id: str, expected_version: str,
               config: dict[str, Any], event_id: str, actor_ref: str,
               evidence_ref: str) -> dict[str, Any]:
        self._event_fields(tenant_id, event_id, actor_ref, evidence_ref)
        _identifier(deployment_id, "deployment_id")
        _identifier(expected_version, "expected_version")
        clean = _validate(config, tenant_id)
        if clean["deployment_id"] != deployment_id or clean["version"] == expected_version:
            raise MinahDeploymentError("MINAH_REVISION_SCOPE_INVALID")
        body, now = _canonical(clean), datetime.now(timezone.utc)
        digest = self._digest(body)
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT current_version FROM minah_deployment_heads
                           WHERE tenant_id=%s AND deployment_id=%s FOR UPDATE""",
                        (tenant_id, deployment_id),
                    )
                    head = cursor.fetchone()
                    if head is None:
                        raise MinahDeploymentError("MINAH_DEPLOYMENT_NOT_FOUND")
                    if head[0] != expected_version:
                        raise MinahDeploymentConflict("STALE_MINAH_DEPLOYMENT_VERSION")
                    cursor.execute(
                        """INSERT INTO minah_deployment_versions
                           (tenant_id,deployment_id,version,status,config_json,digest,created_at)
                           VALUES (%s,%s,%s,'PENDING',%s::jsonb,%s,%s)
                           ON CONFLICT (tenant_id,deployment_id,version) DO NOTHING
                           RETURNING version""",
                        (tenant_id, deployment_id, clean["version"], body, digest, now),
                    )
                    if cursor.fetchone() is None:
                        raise MinahDeploymentConflict("MINAH_DEPLOYMENT_VERSION_EXISTS")
                    cursor.execute(
                        """UPDATE minah_deployment_heads SET current_version=%s
                           WHERE tenant_id=%s AND deployment_id=%s AND current_version=%s""",
                        (clean["version"], tenant_id, deployment_id, expected_version),
                    )
                    if cursor.rowcount != 1:
                        raise MinahDeploymentConflict("STALE_MINAH_DEPLOYMENT_VERSION")
                    cursor.execute(
                        """UPDATE minah_deployment_epochs SET epoch=epoch+1
                           WHERE tenant_id=%s AND deployment_id=%s RETURNING epoch""",
                        (tenant_id, deployment_id),
                    )
                    epoch = int(cursor.fetchone()[0])
                    cursor.execute(
                        """INSERT INTO minah_deployment_events
                           (tenant_id,event_id,deployment_id,version,event_type,actor_ref,
                            evidence_ref,payload_json,created_at)
                           VALUES (%s,%s,%s,%s,'MINAH_DEPLOYMENT_REVISED',%s,%s,%s::jsonb,%s)""",
                        (tenant_id, event_id, deployment_id, clean["version"], actor_ref,
                         evidence_ref, _canonical({"from_version": expected_version,
                         "to_version": clean["version"], "digest": digest}), now),
                    )
        return {"deployment_id": deployment_id, "version": clean["version"],
                "status": "PENDING", "digest": digest, "execution_epoch": epoch}

    def transition(self, *, tenant_id: str, deployment_id: str, expected_version: str,
                   new_version: str, new_status: str, actor_ref: str,
                   event_id: str, evidence_ref: str) -> dict[str, Any]:
        self._event_fields(tenant_id, event_id, actor_ref, evidence_ref)
        for value, field in ((deployment_id, "deployment_id"),
                             (expected_version, "expected_version"),
                             (new_version, "new_version")):
            _identifier(value, field)
        if not isinstance(new_status, str) or new_status not in STATUSES or new_version == expected_version:
            raise MinahDeploymentError("MINAH_TRANSITION_INVALID")
        now = datetime.now(timezone.utc)
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT h.current_version,v.status,v.config_json
                           FROM minah_deployment_heads h
                           JOIN minah_deployment_versions v
                             ON v.tenant_id=h.tenant_id AND v.deployment_id=h.deployment_id
                            AND v.version=h.current_version
                           WHERE h.tenant_id=%s AND h.deployment_id=%s
                           FOR UPDATE OF h""",
                        (tenant_id, deployment_id),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise MinahDeploymentError("MINAH_DEPLOYMENT_NOT_FOUND")
                    if row[0] != expected_version:
                        raise MinahDeploymentConflict("STALE_MINAH_DEPLOYMENT_VERSION")
                    if new_status not in TRANSITIONS[row[1]]:
                        raise MinahDeploymentError("MINAH_DEPLOYMENT_TRANSITION_NOT_ALLOWED")
                    cursor.execute(
                        """SELECT 1 FROM minah_deployment_versions
                           WHERE tenant_id=%s AND deployment_id=%s AND version=%s""",
                        (tenant_id, deployment_id, new_version),
                    )
                    if cursor.fetchone() is not None:
                        raise MinahDeploymentConflict("MINAH_DEPLOYMENT_VERSION_EXISTS")
                    snapshot = row[2] if isinstance(row[2], dict) else json.loads(row[2])
                    snapshot = dict(snapshot)
                    snapshot["version"], snapshot["status"] = new_version, new_status
                    body = _canonical(snapshot)
                    digest = self._digest(body)
                    cursor.execute(
                        """INSERT INTO minah_deployment_versions
                           (tenant_id,deployment_id,version,status,config_json,digest,created_at)
                           VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s)""",
                        (tenant_id, deployment_id, new_version, new_status, body, digest, now),
                    )
                    cursor.execute(
                        """UPDATE minah_deployment_heads SET current_version=%s
                           WHERE tenant_id=%s AND deployment_id=%s AND current_version=%s""",
                        (new_version, tenant_id, deployment_id, expected_version),
                    )
                    if cursor.rowcount != 1:
                        raise MinahDeploymentConflict("STALE_MINAH_DEPLOYMENT_VERSION")
                    cursor.execute(
                        """UPDATE minah_deployment_epochs SET epoch=epoch+1
                           WHERE tenant_id=%s AND deployment_id=%s RETURNING epoch""",
                        (tenant_id, deployment_id),
                    )
                    epoch = int(cursor.fetchone()[0])
                    cursor.execute(
                        """INSERT INTO minah_deployment_events
                           (tenant_id,event_id,deployment_id,version,event_type,actor_ref,
                            evidence_ref,payload_json,created_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                        (tenant_id, event_id, deployment_id, new_version,
                         "MINAH_DEPLOYMENT_" + new_status, actor_ref, evidence_ref,
                         _canonical({"from_status": row[1], "to_status": new_status,
                         "expected_version": expected_version, "digest": digest}), now),
                    )
        return {"deployment_id": deployment_id, "version": new_version,
                "status": new_status, "digest": digest, "execution_epoch": epoch}

    def get(self, *, tenant_id: str, deployment_id: str) -> dict[str, Any] | None:
        _identifier(tenant_id, "tenant_id")
        _identifier(deployment_id, "deployment_id")
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT v.version,v.status,v.config_json,v.digest,v.created_at,e.epoch
                       FROM minah_deployment_heads h
                       JOIN minah_deployment_versions v
                         ON v.tenant_id=h.tenant_id AND v.deployment_id=h.deployment_id
                        AND v.version=h.current_version
                       JOIN minah_deployment_epochs e
                         ON e.tenant_id=h.tenant_id AND e.deployment_id=h.deployment_id
                       WHERE h.tenant_id=%s AND h.deployment_id=%s""",
                    (tenant_id, deployment_id),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        config = row[2] if isinstance(row[2], dict) else json.loads(row[2])
        return {"tenant_id": tenant_id, "deployment_id": deployment_id,
                "version": row[0], "status": row[1], "config": config,
                "digest": row[3], "created_at": row[4].isoformat(),
                "execution_epoch": int(row[5])}

    def list_events(self, *, tenant_id: str, deployment_id: str) -> list[dict[str, Any]]:
        _identifier(tenant_id, "tenant_id")
        _identifier(deployment_id, "deployment_id")
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT event_id,version,event_type,actor_ref,evidence_ref,payload_json,created_at
                       FROM minah_deployment_events WHERE tenant_id=%s AND deployment_id=%s
                       ORDER BY created_at,event_id""",
                    (tenant_id, deployment_id),
                )
                rows = cursor.fetchall()
        return [{"event_id": r[0], "version": r[1], "event_type": r[2],
                 "actor_ref": r[3], "evidence_ref": r[4],
                 "payload": r[5] if isinstance(r[5], dict) else json.loads(r[5]),
                 "created_at": r[6].isoformat()} for r in rows]
