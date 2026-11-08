# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""PostgreSQL-backed shared execution lease and fencing authority.

Lifecycle writers MUST update the deployment head and increment its epoch in
the same transaction, locking the same head/epoch rows. The guarded callback
is serialized against those writers, but writes made by the callback to a
separate IVM/CEE store are not made atomic by this adapter.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from institutional_minah_deployment import MinahDeploymentError


class PostgreSQLInstitutionalExecutionCoordinator:
    """Shared PostgreSQL implementation of the execution coordinator contract.

    connection_factory must return a fresh psycopg 3 connection. The adapter
    owns each connection it opens and closes it after each operation.
    """

    def __init__(self, connection_factory: Callable[[], Any]):
        if not callable(connection_factory):
            raise MinahDeploymentError("POSTGRES_CONNECTION_FACTORY_REQUIRED")
        self._connect = connection_factory

    @staticmethod
    def _scope(tenant_id: Any, deployment_id: Any) -> None:
        for field, value in (("tenant_id", tenant_id), ("deployment_id", deployment_id)):
            if not isinstance(value, str) or not value.strip() or len(value) > 120:
                raise MinahDeploymentError("MINAH_EXECUTION_" + field.upper() + "_INVALID")

    @staticmethod
    def _lease_claims(lease: Any) -> tuple[str, str, str, str, int, int]:
        if not isinstance(lease, dict):
            raise MinahDeploymentError("MINAH_EXECUTION_LEASE_INVALID")
        tenant_id, deployment_id = lease.get("tenant_id"), lease.get("deployment_id")
        lease_id, token = lease.get("lease_id"), lease.get("lease_token")
        epoch, fencing_token = lease.get("execution_epoch"), lease.get("fencing_token")
        PostgreSQLInstitutionalExecutionCoordinator._scope(tenant_id, deployment_id)
        if not all(isinstance(value, str) and value for value in (lease_id, token)):
            raise MinahDeploymentError("MINAH_EXECUTION_LEASE_INVALID")
        if (isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1
                or isinstance(fencing_token, bool) or not isinstance(fencing_token, int)
                or fencing_token < 1):
            raise MinahDeploymentError("MINAH_EXECUTION_LEASE_INVALID")
        return tenant_id, deployment_id, lease_id, token, epoch, fencing_token

    @staticmethod
    def _validate_locked(cursor: Any, lease: dict[str, Any]) -> dict[str, Any]:
        tenant_id, deployment_id, lease_id, token, epoch, fencing_token = (
            PostgreSQLInstitutionalExecutionCoordinator._lease_claims(lease)
        )
        cursor.execute(
            """
            SELECT l.epoch, l.fencing_token, l.token_digest, l.expires_at,
                   l.released_at, v.status, v.version, e.epoch AS current_epoch
              FROM minah_execution_leases AS l
              JOIN minah_deployment_heads AS h
                ON h.tenant_id=l.tenant_id AND h.deployment_id=l.deployment_id
              JOIN minah_deployment_versions AS v
                ON v.tenant_id=h.tenant_id AND v.deployment_id=h.deployment_id
               AND v.version=h.current_version
              JOIN minah_deployment_epochs AS e
                ON e.tenant_id=h.tenant_id AND e.deployment_id=h.deployment_id
             WHERE l.lease_id=%s AND l.tenant_id=%s AND l.deployment_id=%s
             FOR UPDATE OF h, e, l
            """,
            (lease_id, tenant_id, deployment_id),
        )
        row = cursor.fetchone()
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if row is None or not hmac.compare_digest(row[2], digest):
            raise MinahDeploymentError("MINAH_EXECUTION_LEASE_INVALID")
        if row[4] is not None:
            raise MinahDeploymentError("MINAH_EXECUTION_LEASE_RELEASED")
        expires_at = row[3]
        if expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc):
            raise MinahDeploymentError("MINAH_EXECUTION_LEASE_EXPIRED")
        if int(row[0]) != int(row[7]):
            raise MinahDeploymentError("MINAH_EXECUTION_LEASE_FENCED")
        if row[5] != "ACTIVE":
            raise MinahDeploymentError("MINAH_DEPLOYMENT_NOT_ACTIVE")
        if epoch != int(row[0]):
            raise MinahDeploymentError("MINAH_EXECUTION_LEASE_EPOCH_MISMATCH")
        if fencing_token != int(row[1]):
            raise MinahDeploymentError("MINAH_EXECUTION_LEASE_FENCE_TOKEN_MISMATCH")
        return {
            "lease_id": lease_id, "execution_epoch": int(row[0]),
            "fencing_token": int(row[1]), "version": row[6],
            "status": "VALID", "expires_at": expires_at.isoformat(),
        }

    def acquire_execution_lease(
        self, *, tenant_id: str, deployment_id: str, ttl_seconds: int = 30
    ) -> dict[str, Any]:
        self._scope(tenant_id, deployment_id)
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 300:
            raise MinahDeploymentError("MINAH_EXECUTION_LEASE_TTL_INVALID")
        token, lease_id = secrets.token_urlsafe(32), secrets.token_urlsafe(18)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT v.status, v.version, e.epoch
                          FROM minah_deployment_heads h
                          JOIN minah_deployment_versions v
                            ON v.tenant_id=h.tenant_id AND v.deployment_id=h.deployment_id
                           AND v.version=h.current_version
                          JOIN minah_deployment_epochs e
                            ON e.tenant_id=h.tenant_id AND e.deployment_id=h.deployment_id
                         WHERE h.tenant_id=%s AND h.deployment_id=%s
                         FOR UPDATE OF h, e
                        """,
                        (tenant_id, deployment_id),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise MinahDeploymentError("MINAH_DEPLOYMENT_NOT_FOUND")
                    if row[0] != "ACTIVE":
                        raise MinahDeploymentError("MINAH_DEPLOYMENT_NOT_ACTIVE")
                    cursor.execute(
                        """
                        UPDATE minah_deployment_fence_sequences
                           SET last_token=last_token+1
                         WHERE tenant_id=%s AND deployment_id=%s
                         RETURNING last_token
                        """,
                        (tenant_id, deployment_id),
                    )
                    sequence = cursor.fetchone()
                    if sequence is None:
                        raise MinahDeploymentError("MINAH_EXECUTION_FENCE_SEQUENCE_UNAVAILABLE")
                    fencing_token = int(sequence[0])
                    cursor.execute(
                        """
                        INSERT INTO minah_execution_leases
                          (lease_id, tenant_id, deployment_id, epoch, fencing_token,
                           token_digest, expires_at, released_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,NULL)
                        """,
                        (lease_id, tenant_id, deployment_id, int(row[2]), fencing_token,
                         hashlib.sha256(token.encode("utf-8")).hexdigest(), expires_at),
                    )
        return {
            "lease_id": lease_id, "lease_token": token, "tenant_id": tenant_id,
            "deployment_id": deployment_id, "execution_epoch": int(row[2]),
            "fencing_token": fencing_token, "version": row[1],
            "expires_at": expires_at.isoformat(),
        }

    def validate_execution_lease(self, *, lease: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    return self._validate_locked(cursor, lease)

    def execute_under_execution_lease(
        self, *, lease: dict[str, Any], action: Callable[[], Any]
    ) -> Any:
        if not callable(action):
            raise MinahDeploymentError("MINAH_EXECUTION_ACTION_INVALID")
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    self._validate_locked(cursor, lease)
                    # Row locks remain held until the callback completes.
                    # Side effects in separate IVM/CEE stores remain separate
                    # transactions and require explicit recovery semantics.
                    return action()

    def release_execution_lease(self, *, lease: dict[str, Any]) -> None:
        if not isinstance(lease, dict):
            return
        lease_id, token = lease.get("lease_id"), lease.get("lease_token")
        if not isinstance(lease_id, str) or not isinstance(token, str):
            return
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE minah_execution_leases
                           SET released_at=COALESCE(released_at, CURRENT_TIMESTAMP)
                         WHERE lease_id=%s AND token_digest=%s
                        """,
                        (lease_id, digest),
                    )
