# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""PostgreSQL-backed CEE lifecycle journal.

The adapter implements the CEEJournal method contract using a shared PostgreSQL
authority. Operation state changes and their append-only lifecycle event are
committed in one transaction. IVM remains a separate store and therefore is
not made atomic by this journal.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Callable

from protocol_pipeline import LIFECYCLE, ExecutionConflict, LifecycleViolation


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class PostgreSQLCEEJournal:
    """Shared durable implementation of the CEEJournal interface.

    connection_factory must return a fresh, idle psycopg 3 connection. This
    adapter owns and closes each connection it opens.
    """

    def __init__(self, connection_factory: Callable[[], Any]):
        if not callable(connection_factory):
            raise ValueError("POSTGRES_CONNECTION_FACTORY_REQUIRED")
        self._connect = connection_factory

    def close(self) -> None:
        """No persistent connection is held by this adapter."""
        return None

    def verify_integrity(self) -> None:
        """Check journal invariants needed before a recovery attempt."""
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT o.operation_id,o.request_digest,o.request_json,o.phase,
                               EXISTS (
                                   SELECT 1 FROM cee_events e
                                    WHERE e.operation_id=o.operation_id AND e.phase=o.phase
                               )
                          FROM cee_operations o
                        """
                    )
                    for operation_id, stored_digest, request, phase, has_phase_event in cursor.fetchall():
                        if (phase not in LIFECYCLE or not has_phase_event
                                or not isinstance(request, dict)
                                or not hmac.compare_digest(stored_digest, _digest(request))):
                            raise LifecycleViolation(
                                "CEE_STORE_INTEGRITY_CHECK_FAILED_REQUIRES_MANUAL_RECONCILIATION"
                            )
                    cursor.execute(
                        """SELECT count(*) FROM cee_events e
                             LEFT JOIN cee_operations o ON o.operation_id=e.operation_id
                            WHERE o.operation_id IS NULL"""
                    )
                    if int(cursor.fetchone()[0]) != 0:
                        raise LifecycleViolation(
                            "CEE_STORE_INTEGRITY_CHECK_FAILED_REQUIRES_MANUAL_RECONCILIATION"
                        )

    @staticmethod
    def _operation(row: Any) -> dict[str, Any]:
        names = (
            "operation_id", "request_digest", "request_json", "phase",
            "minah_json", "ivm_json", "outcome", "result_json",
        )
        result = dict(zip(names, row))
        return result

    def get(self, operation_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT operation_id,request_digest,request_json,phase,
                                  minah_json,ivm_json,outcome,result_json
                             FROM cee_operations WHERE operation_id=%s""",
                        (operation_id,),
                    )
                    row = cursor.fetchone()
                    return self._operation(row) if row is not None else None

    def events(self, operation_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT phase,status,detail_json FROM cee_events
                            WHERE operation_id=%s ORDER BY event_id""",
                        (operation_id,),
                    )
                    return [
                        {"phase": row[0], "status": row[1], "detail": row[2]}
                        for row in cursor.fetchall()
                    ]

    def record_observation(
        self, operation_id: str, kind: str, status: str, detail: dict[str, Any],
    ) -> None:
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT operation_id FROM cee_operations WHERE operation_id=%s FOR UPDATE",
                        (operation_id,),
                    )
                    if cursor.fetchone() is None:
                        raise LifecycleViolation("OPERATION_NOT_FOUND")
                    cursor.execute(
                        """INSERT INTO cee_observations(operation_id,kind,status,detail_json)
                           VALUES(%s,%s,%s,%s::jsonb)""",
                        (operation_id, kind, status, _canonical(detail)),
                    )

    def observations(self, operation_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT kind,status,detail_json,created_at
                             FROM cee_observations WHERE operation_id=%s
                             ORDER BY observation_id""",
                        (operation_id,),
                    )
                    return [
                        {"kind": row[0], "status": row[1], "detail": row[2],
                         "created_at": row[3].astimezone(timezone.utc).isoformat()}
                        for row in cursor.fetchall()
                    ]

    def start(self, operation_id: str, request: dict[str, Any]) -> dict[str, Any]:
        digest = _digest(request)
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """INSERT INTO cee_operations
                           (operation_id,request_digest,request_json,phase)
                           VALUES(%s,%s,%s::jsonb,'REQUESTED')
                           ON CONFLICT(operation_id) DO NOTHING
                           RETURNING operation_id""",
                        (operation_id, digest, _canonical(request)),
                    )
                    inserted = cursor.fetchone() is not None
                    if inserted:
                        cursor.execute(
                            """INSERT INTO cee_events(operation_id,phase,status,detail_json)
                               VALUES(%s,'REQUESTED','ACCEPTED',%s::jsonb)""",
                            (operation_id, _canonical({"request_digest": digest})),
                        )
                    else:
                        cursor.execute(
                            """SELECT operation_id,request_digest,request_json,phase,
                                      minah_json,ivm_json,outcome,result_json
                                 FROM cee_operations WHERE operation_id=%s FOR UPDATE""",
                            (operation_id,),
                        )
                        row = cursor.fetchone()
                        if row is None:
                            raise LifecycleViolation("OPERATION_DISAPPEARED")
                        if row[1] != digest:
                            raise ExecutionConflict("OPERATION_ID_REUSED_WITH_DIFFERENT_CONTENT")
                        return self._operation(row)
                    cursor.execute(
                        """SELECT operation_id,request_digest,request_json,phase,
                                  minah_json,ivm_json,outcome,result_json
                             FROM cee_operations WHERE operation_id=%s""",
                        (operation_id,),
                    )
                    return self._operation(cursor.fetchone())

    def advance(
        self, operation_id: str, next_phase: str, *, status: str = "ACCEPTED",
        detail: dict[str, Any] | None = None, minah: dict[str, Any] | None = None,
        ivm: dict[str, Any] | None = None, outcome: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if next_phase not in LIFECYCLE:
            raise LifecycleViolation("UNKNOWN_CEE_PHASE")
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT operation_id,request_digest,request_json,phase,
                                  minah_json,ivm_json,outcome,result_json
                             FROM cee_operations WHERE operation_id=%s FOR UPDATE""",
                        (operation_id,),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise LifecycleViolation("OPERATION_NOT_FOUND")
                    current = row[3]
                    if current == next_phase:
                        return self._operation(row)
                    if current == "FINALIZED":
                        raise LifecycleViolation("FINALIZED_OPERATION_IS_IMMUTABLE")
                    current_index = LIFECYCLE.index(current)
                    next_index = LIFECYCLE.index(next_phase)
                    allowed = next_index == current_index + 1
                    terminal_branch = (
                        next_phase == "FINALIZED"
                        and (
                            (current in {"MINAH_REVIEWED", "IDENTITY_VERIFIED",
                                         "AUTHORITY_VERIFIED", "AUTHORITY_EVALUATED"}
                             and outcome in {"DEFERRED", "REJECTED", "UNKNOWN"})
                            or (current == "COMMITTING" and outcome in {"REJECTED", "UNKNOWN"})
                        )
                    )
                    if not allowed and not terminal_branch:
                        raise LifecycleViolation(f"ILLEGAL_CEE_TRANSITION:{current}->{next_phase}")
                    cursor.execute(
                        """UPDATE cee_operations
                              SET phase=%s,
                                  minah_json=COALESCE(%s::jsonb,minah_json),
                                  ivm_json=COALESCE(%s::jsonb,ivm_json),
                                  outcome=COALESCE(%s,outcome),
                                  result_json=COALESCE(%s::jsonb,result_json),
                                  updated_at=CURRENT_TIMESTAMP
                            WHERE operation_id=%s""",
                        (
                            next_phase,
                            _canonical(minah) if minah is not None else None,
                            _canonical(ivm) if ivm is not None else None,
                            outcome,
                            _canonical(result) if result is not None else None,
                            operation_id,
                        ),
                    )
                    cursor.execute(
                        """INSERT INTO cee_events(operation_id,phase,status,detail_json)
                           VALUES(%s,%s,%s,%s::jsonb)""",
                        (operation_id, next_phase, status, _canonical(detail or {})),
                    )
                    cursor.execute(
                        """SELECT operation_id,request_digest,request_json,phase,
                                  minah_json,ivm_json,outcome,result_json
                             FROM cee_operations WHERE operation_id=%s""",
                        (operation_id,),
                    )
                    return self._operation(cursor.fetchone())
