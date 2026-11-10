# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""PostgreSQL-backed durable federation inbox state machine.

This module persists a verified delivery before receiver execution, supports
leased worker claims, and stores a stable receiver decision reference. It does
not make CEE/IVM execution and inbox completion one atomic transaction.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import closing
from typing import Any, Callable

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
ERROR_CODE = re.compile(r"^[A-Z0-9_:-]{1,120}$")
STATUSES = {"RECEIVED", "PROCESSING", "COMPLETED", "REJECTED", "RETRYABLE_FAILURE"}


class FederationInboxError(RuntimeError):
    """The durable inbox rejected an operation or its backing store failed."""


def _identifier(value: Any, code: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise FederationInboxError(code)
    return value


def _canonical_json(value: Any) -> tuple[str, str]:
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise FederationInboxError("FEDERATION_INBOX_ENVELOPE_INVALID") from exc
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class PostgreSQLFederationInbox:
    """Durable inbox using PostgreSQL row locks and expiring worker leases.

    connection_factory must return a fresh psycopg 3 connection. Each method
    owns and closes its connection. All workers for one receiver tenant must
    use the same PostgreSQL authority.
    """

    def __init__(self, connection_factory: Callable[[], Any]):
        if not callable(connection_factory):
            raise FederationInboxError("FEDERATION_INBOX_CONFIGURATION_INVALID")
        self._connect = connection_factory

    def accept_verified(
        self, *, receiver_tenant_id: str, issuer_installation_id: str,
        message_id: str, operation_id: str, idempotency_key: str,
        envelope: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a message only after the caller has verified its signature.

        Repeated identical deliveries return the existing row. Reuse of a
        message ID or idempotency key with different content is rejected.
        This method is intentionally separate from signature verification:
        callers must never treat an unverified envelope as accepted.
        """
        tenant = _identifier(receiver_tenant_id, "INBOX_TENANT_INVALID")
        issuer = _identifier(issuer_installation_id, "INBOX_ISSUER_INVALID")
        message = _identifier(message_id, "INBOX_MESSAGE_ID_INVALID")
        operation = _identifier(operation_id, "INBOX_OPERATION_ID_INVALID")
        idem = _identifier(idempotency_key, "INBOX_IDEMPOTENCY_KEY_INVALID")
        encoded, digest = _canonical_json(envelope)
        try:
            with closing(self._connect()) as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """INSERT INTO federation_inbox
                               (receiver_tenant_id, issuer_installation_id,
                                message_id, operation_id, idempotency_key,
                                envelope_json, envelope_sha256, status)
                               VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, 'RECEIVED')
                               ON CONFLICT DO NOTHING
                               RETURNING receiver_tenant_id, issuer_installation_id,
                                         message_id, operation_id, idempotency_key,
                                         status, attempt_count, receiver_decision_ref""",
                            (tenant, issuer, message, operation, idem, encoded, digest),
                        )
                        row = cursor.fetchone()
                        inserted = row is not None
                        if row is None:
                            cursor.execute(
                                """SELECT receiver_tenant_id, issuer_installation_id,
                                          message_id, operation_id, idempotency_key,
                                          status, attempt_count, receiver_decision_ref,
                                          envelope_sha256
                                   FROM federation_inbox
                                   WHERE receiver_tenant_id = %s
                                     AND issuer_installation_id = %s
                                     AND (message_id = %s OR idempotency_key = %s)
                                   FOR UPDATE""",
                                (tenant, issuer, message, idem),
                            )
                            row = cursor.fetchone()
                            if row is None:
                                raise FederationInboxError("INBOX_IDEMPOTENCY_CONFLICT")
                            if (row[2] != message or row[3] != operation
                                    or row[4] != idem or row[8] != digest):
                                raise FederationInboxError("INBOX_IDEMPOTENCY_CONFLICT")
                        return {
                            "inserted": inserted,
                            "receiver_tenant_id": row[0],
                            "issuer_installation_id": row[1],
                            "message_id": row[2],
                            "operation_id": row[3],
                            "idempotency_key": row[4],
                            "status": row[5],
                            "attempt_count": row[6],
                            "receiver_decision_ref": row[7],
                        }
        except FederationInboxError:
            raise
        except Exception as exc:
            raise FederationInboxError("FEDERATION_INBOX_STORE_UNAVAILABLE") from exc

    def claim(
        self, *, receiver_tenant_id: str, issuer_installation_id: str,
        message_id: str, worker_id: str, lease_seconds: int = 60,
    ) -> dict[str, Any] | None:
        """Claim one delivery or reclaim an expired worker lease."""
        tenant = _identifier(receiver_tenant_id, "INBOX_TENANT_INVALID")
        issuer = _identifier(issuer_installation_id, "INBOX_ISSUER_INVALID")
        message = _identifier(message_id, "INBOX_MESSAGE_ID_INVALID")
        worker = _identifier(worker_id, "INBOX_WORKER_ID_INVALID")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise FederationInboxError("INBOX_LEASE_DURATION_INVALID")
        try:
            with closing(self._connect()) as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """UPDATE federation_inbox
                               SET status = 'PROCESSING', lease_owner = %s,
                                   lease_expires_at = CURRENT_TIMESTAMP
                                       + (%s * INTERVAL '1 second'),
                                   attempt_count = attempt_count + 1,
                                   updated_at = CURRENT_TIMESTAMP
                               WHERE receiver_tenant_id = %s
                                 AND issuer_installation_id = %s
                                 AND message_id = %s
                                 AND (status IN ('RECEIVED', 'RETRYABLE_FAILURE')
                                      OR (status = 'PROCESSING'
                                          AND lease_expires_at <= CURRENT_TIMESTAMP))
                               RETURNING operation_id, idempotency_key, envelope_json,
                                         attempt_count, lease_expires_at""",
                            (worker, lease_seconds, tenant, issuer, message),
                        )
                        row = cursor.fetchone()
                        if row is None:
                            return None
                        return {
                            "receiver_tenant_id": tenant,
                            "issuer_installation_id": issuer,
                            "message_id": message,
                            "operation_id": row[0],
                            "idempotency_key": row[1],
                            "envelope": row[2],
                            "attempt_count": row[3],
                            "lease_expires_at": row[4],
                            "lease_owner": worker,
                        }
        except FederationInboxError:
            raise
        except Exception as exc:
            raise FederationInboxError("FEDERATION_INBOX_STORE_UNAVAILABLE") from exc

    def complete(
        self, *, receiver_tenant_id: str, issuer_installation_id: str,
        message_id: str, worker_id: str, decision_ref: str,
        response: dict[str, Any],
    ) -> bool:
        """Persist a terminal receiver decision; only the lease owner may do so."""
        tenant = _identifier(receiver_tenant_id, "INBOX_TENANT_INVALID")
        issuer = _identifier(issuer_installation_id, "INBOX_ISSUER_INVALID")
        message = _identifier(message_id, "INBOX_MESSAGE_ID_INVALID")
        worker = _identifier(worker_id, "INBOX_WORKER_ID_INVALID")
        decision = _identifier(decision_ref, "INBOX_DECISION_REF_INVALID")
        encoded, _ = _canonical_json(response)
        try:
            with closing(self._connect()) as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """UPDATE federation_inbox
                               SET status = 'COMPLETED', receiver_decision_ref = %s,
                                   response_json = %s::jsonb, completed_at = CURRENT_TIMESTAMP,
                                   updated_at = CURRENT_TIMESTAMP, lease_owner = NULL,
                                   lease_expires_at = NULL, last_error_code = NULL
                               WHERE receiver_tenant_id = %s
                                 AND issuer_installation_id = %s AND message_id = %s
                                 AND status = 'PROCESSING' AND lease_owner = %s
                                 AND lease_expires_at > CURRENT_TIMESTAMP""",
                            (decision, encoded, tenant, issuer, message, worker),
                        )
                        return cursor.rowcount == 1
        except Exception as exc:
            raise FederationInboxError("FEDERATION_INBOX_STORE_UNAVAILABLE") from exc

    def fail(
        self, *, receiver_tenant_id: str, issuer_installation_id: str,
        message_id: str, worker_id: str, error_code: str, retryable: bool,
    ) -> bool:
        """Release a worker lease as retryable or permanently rejected."""
        tenant = _identifier(receiver_tenant_id, "INBOX_TENANT_INVALID")
        issuer = _identifier(issuer_installation_id, "INBOX_ISSUER_INVALID")
        message = _identifier(message_id, "INBOX_MESSAGE_ID_INVALID")
        worker = _identifier(worker_id, "INBOX_WORKER_ID_INVALID")
        if not isinstance(error_code, str) or not ERROR_CODE.fullmatch(error_code):
            raise FederationInboxError("INBOX_ERROR_CODE_INVALID")
        if type(retryable) is not bool:
            raise FederationInboxError("INBOX_RETRYABILITY_INVALID")
        status = "RETRYABLE_FAILURE" if retryable else "REJECTED"
        try:
            with closing(self._connect()) as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """UPDATE federation_inbox
                               SET status = %s, last_error_code = %s,
                                   updated_at = CURRENT_TIMESTAMP, lease_owner = NULL,
                                   lease_expires_at = NULL
                               WHERE receiver_tenant_id = %s
                                 AND issuer_installation_id = %s AND message_id = %s
                                 AND status = 'PROCESSING' AND lease_owner = %s
                                 AND lease_expires_at > CURRENT_TIMESTAMP""",
                            (status, error_code, tenant, issuer, message, worker),
                        )
                        return cursor.rowcount == 1
        except Exception as exc:
            raise FederationInboxError("FEDERATION_INBOX_STORE_UNAVAILABLE") from exc

    def get(
        self, *, receiver_tenant_id: str, issuer_installation_id: str,
        message_id: str,
    ) -> dict[str, Any] | None:
        tenant = _identifier(receiver_tenant_id, "INBOX_TENANT_INVALID")
        issuer = _identifier(issuer_installation_id, "INBOX_ISSUER_INVALID")
        message = _identifier(message_id, "INBOX_MESSAGE_ID_INVALID")
        try:
            with closing(self._connect()) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT operation_id, idempotency_key, status, attempt_count,
                                  receiver_decision_ref, response_json, last_error_code
                           FROM federation_inbox
                           WHERE receiver_tenant_id = %s AND issuer_installation_id = %s
                             AND message_id = %s""",
                        (tenant, issuer, message),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        return None
                    return {
                        "operation_id": row[0], "idempotency_key": row[1],
                        "status": row[2], "attempt_count": row[3],
                        "receiver_decision_ref": row[4], "response": row[5],
                        "last_error_code": row[6],
                    }
        except Exception as exc:
            raise FederationInboxError("FEDERATION_INBOX_STORE_UNAVAILABLE") from exc
