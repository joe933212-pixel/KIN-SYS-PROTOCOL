# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""PostgreSQL-backed sender outbox for cross-installation federation.

The outbox provides at-least-once delivery attempts and idempotent persistence
of a receiver-verified acknowledgement. It does not provide exactly-once
network delivery or execute the receiver's instruction.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import closing, contextmanager
from typing import Any, Callable

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
ERROR_CODE = re.compile(r"^[A-Z0-9_:-]{1,120}$")
SIGNATURE = re.compile(r"^[A-Za-z0-9_-]{86}$")


class FederationOutboxError(RuntimeError):
    """The durable sender outbox rejected an operation or its backing store failed."""


def _identifier(value: Any, code: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise FederationOutboxError(code)
    return value


def _canonical_json(value: Any) -> tuple[str, str]:
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise FederationOutboxError("FEDERATION_OUTBOX_JSON_INVALID") from exc
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()



    @contextmanager
    def _tenant_connection(self, tenant_id: str):
        """Bind every database operation to its authenticated tenant context."""
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

class PostgreSQLFederationOutbox:
    """Durable sender state using PostgreSQL row updates and expiring leases.

    connection_factory returns a fresh psycopg 3 connection. Each method owns
    and closes its connection. All sender workers for an installation must
    share the same PostgreSQL authority.
    """

    def __init__(
        self, connection_factory: Callable[[], Any],
        acknowledgement_verifier: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    ):
        if not callable(connection_factory) or not callable(acknowledgement_verifier):
            raise FederationOutboxError("FEDERATION_OUTBOX_CONFIGURATION_INVALID")
        self._connect = connection_factory
        self._verify_acknowledgement = acknowledgement_verifier

    def enqueue(
        self, *, sender_tenant_id: str, sender_installation_id: str,
        envelope: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a signed message before transport; identical retries are safe."""
        tenant = _identifier(sender_tenant_id, "OUTBOX_TENANT_INVALID")
        sender = _identifier(sender_installation_id, "OUTBOX_SENDER_INVALID")
        if not isinstance(envelope, dict) or set(envelope) != {"claims", "signature"}:
            raise FederationOutboxError("OUTBOX_ENVELOPE_INVALID")
        claims = envelope.get("claims")
        signature = envelope.get("signature")
        if not isinstance(signature, str) or not SIGNATURE.fullmatch(signature):
            raise FederationOutboxError("OUTBOX_SIGNATURE_FORMAT_INVALID")
        required = {
            "message_id", "operation_id", "idempotency_key",
            "issuer_installation_id", "receiver_installation_id",
            "receiver_institution_id",
        }
        if not isinstance(claims, dict) or not required.issubset(claims):
            raise FederationOutboxError("OUTBOX_ENVELOPE_INVALID")
        if claims["issuer_installation_id"] != sender:
            raise FederationOutboxError("OUTBOX_SENDER_MISMATCH")
        message = _identifier(claims["message_id"], "OUTBOX_MESSAGE_ID_INVALID")
        operation = _identifier(claims["operation_id"], "OUTBOX_OPERATION_ID_INVALID")
        idem = _identifier(claims["idempotency_key"], "OUTBOX_IDEMPOTENCY_KEY_INVALID")
        receiver = _identifier(
            claims["receiver_installation_id"], "OUTBOX_RECEIVER_INVALID"
        )
        receiver_institution = _identifier(
            claims["receiver_institution_id"], "OUTBOX_RECEIVER_INSTITUTION_INVALID"
        )
        encoded, digest = _canonical_json(envelope)
        try:
            with self._tenant_connection(tenant) as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """INSERT INTO federation_outbox
                               (sender_tenant_id, sender_installation_id, message_id,
                                operation_id, idempotency_key, receiver_installation_id,
                                receiver_institution_id, envelope_json, envelope_sha256, status)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, 'PENDING')
                               ON CONFLICT DO NOTHING
                               RETURNING message_id, operation_id, idempotency_key,
                                         receiver_installation_id, receiver_institution_id,
                                         status, attempt_count, envelope_sha256""",
                            (tenant, sender, message, operation, idem, receiver,
                             receiver_institution, encoded, digest),
                        )
                        row = cursor.fetchone()
                        inserted = row is not None
                        if row is None:
                            cursor.execute(
                                """SELECT message_id, operation_id, idempotency_key,
                                          receiver_installation_id, receiver_institution_id,
                                          status, attempt_count, envelope_sha256
                                   FROM federation_outbox
                                   WHERE sender_tenant_id = %s
                                     AND sender_installation_id = %s
                                     AND (message_id = %s OR idempotency_key = %s)
                                   FOR UPDATE""",
                                (tenant, sender, message, idem),
                            )
                            row = cursor.fetchone()
                            if (row is None or row[0] != message or row[1] != operation
                                    or row[2] != idem or row[3] != receiver
                                    or row[4] != receiver_institution or row[7] != digest):
                                raise FederationOutboxError("OUTBOX_IDEMPOTENCY_CONFLICT")
                        return {
                            "inserted": inserted, "message_id": row[0],
                            "operation_id": row[1], "idempotency_key": row[2],
                            "receiver_installation_id": row[3],
                            "receiver_institution_id": row[4], "status": row[5],
                            "attempt_count": row[6], "envelope_sha256": row[7],
                        }
        except FederationOutboxError:
            raise
        except Exception as exc:
            raise FederationOutboxError("FEDERATION_OUTBOX_STORE_UNAVAILABLE") from exc

    def claim(
        self, *, sender_tenant_id: str, sender_installation_id: str,
        message_id: str, worker_id: str, lease_seconds: int = 60,
    ) -> dict[str, Any] | None:
        """Claim pending delivery or reclaim a retryable/expired delivery."""
        tenant = _identifier(sender_tenant_id, "OUTBOX_TENANT_INVALID")
        sender = _identifier(sender_installation_id, "OUTBOX_SENDER_INVALID")
        message = _identifier(message_id, "OUTBOX_MESSAGE_ID_INVALID")
        worker = _identifier(worker_id, "OUTBOX_WORKER_ID_INVALID")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise FederationOutboxError("OUTBOX_LEASE_DURATION_INVALID")
        try:
            with self._tenant_connection(tenant) as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """UPDATE federation_outbox
                               SET status = 'DEAD_LETTER', dead_lettered_at = CURRENT_TIMESTAMP,
                                   next_attempt_at = NULL, lease_owner = NULL,
                                   lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
                               WHERE sender_tenant_id = %s AND sender_installation_id = %s
                                 AND message_id = %s AND attempt_count >= max_attempts
                                 AND (status IN ('PENDING', 'RETRYABLE_FAILURE')
                                      OR (status = 'DELIVERING'
                                          AND lease_expires_at <= CURRENT_TIMESTAMP))""",
                            (tenant, sender, message),
                        )
                        cursor.execute(
                            """UPDATE federation_outbox
                               SET status = 'DELIVERING', lease_owner = %s,
                                   lease_expires_at = CURRENT_TIMESTAMP
                                       + (%s * INTERVAL '1 second'),
                                   attempt_count = attempt_count + 1,
                                   next_attempt_at = NULL,
                                   updated_at = CURRENT_TIMESTAMP
                               WHERE sender_tenant_id = %s AND sender_installation_id = %s
                                 AND message_id = %s AND attempt_count < max_attempts
                                 AND (status = 'PENDING'
                                      OR (status = 'RETRYABLE_FAILURE'
                                          AND (next_attempt_at IS NULL
                                               OR next_attempt_at <= CURRENT_TIMESTAMP))
                                      OR (status = 'DELIVERING'
                                          AND lease_expires_at <= CURRENT_TIMESTAMP))
                               RETURNING operation_id, idempotency_key,
                                         receiver_installation_id, receiver_institution_id,
                                         envelope_json, attempt_count, lease_expires_at""",
                            (worker, lease_seconds, tenant, sender, message),
                        )
                        row = cursor.fetchone()
                        if row is None:
                            return None
                        return {
                            "sender_tenant_id": tenant,
                            "sender_installation_id": sender,
                            "message_id": message, "operation_id": row[0],
                            "idempotency_key": row[1],
                            "receiver_installation_id": row[2],
                            "receiver_institution_id": row[3],
                            "envelope": row[4], "attempt_count": row[5],
                            "lease_expires_at": row[6], "lease_owner": worker,
                        }
        except FederationOutboxError:
            raise
        except Exception as exc:
            raise FederationOutboxError("FEDERATION_OUTBOX_STORE_UNAVAILABLE") from exc

    def mark_retryable(
        self, *, sender_tenant_id: str, sender_installation_id: str,
        message_id: str, worker_id: str, error_code: str,
    ) -> bool:
        """Release a delivery lease after a transport failure."""
        tenant = _identifier(sender_tenant_id, "OUTBOX_TENANT_INVALID")
        sender = _identifier(sender_installation_id, "OUTBOX_SENDER_INVALID")
        message = _identifier(message_id, "OUTBOX_MESSAGE_ID_INVALID")
        worker = _identifier(worker_id, "OUTBOX_WORKER_ID_INVALID")
        if not isinstance(error_code, str) or not ERROR_CODE.fullmatch(error_code):
            raise FederationOutboxError("OUTBOX_ERROR_CODE_INVALID")
        try:
            with self._tenant_connection(tenant) as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """UPDATE federation_outbox
                               SET status = CASE WHEN attempt_count >= max_attempts
                                                 THEN 'DEAD_LETTER'
                                                 ELSE 'RETRYABLE_FAILURE' END,
                                   dead_lettered_at = CASE WHEN attempt_count >= max_attempts
                                                           THEN CURRENT_TIMESTAMP ELSE NULL END,
                                   next_attempt_at = CASE WHEN attempt_count >= max_attempts
                                                          THEN NULL
                                                          ELSE CURRENT_TIMESTAMP
                                                            + (LEAST(3600::double precision,
                                                                5::double precision * power(
                                                                  2::double precision,
                                                                  LEAST(10::bigint, GREATEST(
                                                                    0::bigint, attempt_count - 1))))
                                                               * INTERVAL '1 second') END,
                                   last_error_code = %s, updated_at = CURRENT_TIMESTAMP,
                                   lease_owner = NULL, lease_expires_at = NULL
                               WHERE sender_tenant_id = %s AND sender_installation_id = %s
                                 AND message_id = %s AND status = 'DELIVERING'
                                 AND lease_owner = %s
                                 AND lease_expires_at > CURRENT_TIMESTAMP""",
                            (error_code, tenant, sender, message, worker),
                        )
                        return cursor.rowcount == 1
        except Exception as exc:
            raise FederationOutboxError("FEDERATION_OUTBOX_STORE_UNAVAILABLE") from exc

    def acknowledge(
        self, *, sender_tenant_id: str, sender_installation_id: str,
        message_id: str, receipt: dict[str, Any],
    ) -> dict[str, Any]:
        """Verify and persist a receiver receipt, independent of delivery lease.

        The injected verifier must use sender-local receiver trust configuration
        and cryptographically verify the receipt against the immutable original
        envelope. A late acknowledgement remains valid after a worker crash.
        """
        tenant = _identifier(sender_tenant_id, "OUTBOX_TENANT_INVALID")
        sender = _identifier(sender_installation_id, "OUTBOX_SENDER_INVALID")
        message = _identifier(message_id, "OUTBOX_MESSAGE_ID_INVALID")
        if not isinstance(receipt, dict):
            raise FederationOutboxError("OUTBOX_ACKNOWLEDGEMENT_INVALID")
        try:
            with self._tenant_connection(tenant) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT envelope_json FROM federation_outbox
                           WHERE sender_tenant_id = %s AND sender_installation_id = %s
                             AND message_id = %s""",
                        (tenant, sender, message),
                    )
                    original_row = cursor.fetchone()
                    if original_row is None:
                        raise FederationOutboxError("OUTBOX_MESSAGE_NOT_FOUND")
                    original_envelope = original_row[0]
            try:
                verified = self._verify_acknowledgement(receipt, original_envelope)
            except Exception as exc:
                raise FederationOutboxError("OUTBOX_ACKNOWLEDGEMENT_VERIFICATION_FAILED") from exc
            if (not isinstance(verified, dict) or verified.get("verified") is not True
                    or not isinstance(verified.get("claims"), dict)
                    or not isinstance(verified.get("response"), dict)):
                raise FederationOutboxError("OUTBOX_VERIFIED_ACKNOWLEDGEMENT_REQUIRED")
            claims, response = verified["claims"], verified["response"]
            encoded, receipt_digest = _canonical_json(receipt)
            with self._tenant_connection(tenant) as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """SELECT operation_id, idempotency_key, receiver_installation_id,
                                      receiver_institution_id, envelope_sha256, status,
                                      acknowledgement_sha256
                               FROM federation_outbox
                               WHERE sender_tenant_id = %s AND sender_installation_id = %s
                                 AND message_id = %s FOR UPDATE""",
                            (tenant, sender, message),
                        )
                        row = cursor.fetchone()
                        if row is None:
                            raise FederationOutboxError("OUTBOX_MESSAGE_NOT_FOUND")
                        if (claims.get("message_id") != message
                                or claims.get("operation_id") != row[0]
                                or claims.get("idempotency_key") != row[1]
                                or claims.get("sender_installation_id") != sender
                                or claims.get("receiver_installation_id") != row[2]
                                or claims.get("receiver_institution_id") != row[3]
                                or claims.get("original_envelope_digest") != row[4]):
                            raise FederationOutboxError("OUTBOX_ACKNOWLEDGEMENT_BINDING_MISMATCH")
                        if row[5] == "ACKNOWLEDGED":
                            if row[6] != receipt_digest:
                                raise FederationOutboxError("OUTBOX_ACKNOWLEDGEMENT_CONFLICT")
                            return {
                                "acknowledged": True, "duplicate": True,
                                "message_id": message, "status": "ACKNOWLEDGED",
                                "response": response,
                            }
                        cursor.execute(
                            """UPDATE federation_outbox
                               SET status = 'ACKNOWLEDGED', acknowledgement_json = %s::jsonb,
                                   acknowledgement_sha256 = %s,
                                   acknowledged_at = CURRENT_TIMESTAMP,
                                   updated_at = CURRENT_TIMESTAMP, lease_owner = NULL,
                                   lease_expires_at = NULL, last_error_code = NULL
                               WHERE sender_tenant_id = %s AND sender_installation_id = %s
                                 AND message_id = %s""",
                            (encoded, receipt_digest, tenant, sender, message),
                        )
                        return {
                            "acknowledged": True, "duplicate": False,
                            "message_id": message, "status": "ACKNOWLEDGED",
                            "response": response,
                        }
        except FederationOutboxError:
            raise
        except Exception as exc:
            raise FederationOutboxError("FEDERATION_OUTBOX_STORE_UNAVAILABLE") from exc

    def get(
        self, *, sender_tenant_id: str, sender_installation_id: str,
        message_id: str,
    ) -> dict[str, Any] | None:
        tenant = _identifier(sender_tenant_id, "OUTBOX_TENANT_INVALID")
        sender = _identifier(sender_installation_id, "OUTBOX_SENDER_INVALID")
        message = _identifier(message_id, "OUTBOX_MESSAGE_ID_INVALID")
        try:
            with self._tenant_connection(tenant) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT operation_id, idempotency_key, receiver_installation_id,
                                  receiver_institution_id, status, attempt_count,
                                  envelope_json, acknowledgement_json,
                                  acknowledgement_sha256, last_error_code,
                                  max_attempts, next_attempt_at, dead_lettered_at
                           FROM federation_outbox
                           WHERE sender_tenant_id = %s AND sender_installation_id = %s
                             AND message_id = %s""",
                        (tenant, sender, message),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        return None
                    return {
                        "operation_id": row[0], "idempotency_key": row[1],
                        "receiver_installation_id": row[2],
                        "receiver_institution_id": row[3], "status": row[4],
                        "attempt_count": row[5], "envelope": row[6],
                        "acknowledgement": row[7],
                        "acknowledgement_sha256": row[8], "last_error_code": row[9],
                        "max_attempts": row[10], "next_attempt_at": row[11],
                        "dead_lettered_at": row[12],
                    }
        except Exception as exc:
            raise FederationOutboxError("FEDERATION_OUTBOX_STORE_UNAVAILABLE") from exc


    def claim_batch(
        self, *, sender_tenant_id: str, sender_installation_id: str,
        worker_id: str, limit: int = 20, lease_seconds: int = 60,
    ) -> list[dict[str, Any]]:
        """Atomically claim a bounded batch without blocking peer workers.

        PostgreSQL row locks and SKIP LOCKED distribute work across concurrent
        dispatchers. Every returned row receives its own expiring lease before
        this method commits. Network I/O must happen after this transaction.
        """
        tenant = _identifier(sender_tenant_id, "OUTBOX_TENANT_INVALID")
        sender = _identifier(sender_installation_id, "OUTBOX_SENDER_INVALID")
        worker = _identifier(worker_id, "OUTBOX_WORKER_ID_INVALID")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise FederationOutboxError("OUTBOX_BATCH_LIMIT_INVALID")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise FederationOutboxError("OUTBOX_LEASE_DURATION_INVALID")
        try:
            with self._tenant_connection(tenant) as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """UPDATE federation_outbox
                               SET status = 'DEAD_LETTER', dead_lettered_at = CURRENT_TIMESTAMP,
                                   next_attempt_at = NULL, lease_owner = NULL,
                                   lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
                               WHERE sender_tenant_id = %s AND sender_installation_id = %s
                                 AND attempt_count >= max_attempts
                                 AND (status IN ('PENDING', 'RETRYABLE_FAILURE')
                                      OR (status = 'DELIVERING'
                                          AND lease_expires_at <= CURRENT_TIMESTAMP))""",
                            (tenant, sender),
                        )
                        cursor.execute(
                            """WITH candidates AS (
                                   SELECT sender_tenant_id, sender_installation_id, message_id
                                   FROM federation_outbox
                                   WHERE sender_tenant_id = %s
                                     AND sender_installation_id = %s
                                     AND attempt_count < max_attempts
                                     AND (status = 'PENDING'
                                          OR (status = 'RETRYABLE_FAILURE'
                                              AND (next_attempt_at IS NULL
                                                   OR next_attempt_at <= CURRENT_TIMESTAMP))
                                          OR (status = 'DELIVERING'
                                              AND lease_expires_at <= CURRENT_TIMESTAMP))
                                   ORDER BY created_at, message_id
                                   FOR UPDATE SKIP LOCKED
                                   LIMIT %s
                               )
                               UPDATE federation_outbox AS outbox
                               SET status = 'DELIVERING', lease_owner = %s,
                                   lease_expires_at = CURRENT_TIMESTAMP
                                       + (%s * INTERVAL '1 second'),
                                   attempt_count = outbox.attempt_count + 1,
                                   next_attempt_at = NULL,
                                   updated_at = CURRENT_TIMESTAMP
                               FROM candidates
                               WHERE outbox.sender_tenant_id = candidates.sender_tenant_id
                                 AND outbox.sender_installation_id = candidates.sender_installation_id
                                 AND outbox.message_id = candidates.message_id
                               RETURNING outbox.sender_tenant_id,
                                         outbox.sender_installation_id, outbox.message_id,
                                         outbox.operation_id, outbox.idempotency_key,
                                         outbox.receiver_installation_id,
                                         outbox.receiver_institution_id,
                                         outbox.envelope_json, outbox.attempt_count,
                                         outbox.lease_expires_at, outbox.lease_owner""",
                            (tenant, sender, limit, worker, lease_seconds),
                        )
                        rows = cursor.fetchall()
                        claims = [{
                            "sender_tenant_id": row[0],
                            "sender_installation_id": row[1],
                            "message_id": row[2],
                            "operation_id": row[3],
                            "idempotency_key": row[4],
                            "receiver_installation_id": row[5],
                            "receiver_institution_id": row[6],
                            "envelope": row[7],
                            "attempt_count": row[8],
                            "lease_expires_at": row[9],
                            "lease_owner": row[10],
                        } for row in rows]
                        return claims
        except FederationOutboxError:
            raise
        except Exception as exc:
            raise FederationOutboxError("FEDERATION_OUTBOX_STORE_UNAVAILABLE") from exc
