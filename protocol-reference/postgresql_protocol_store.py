# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Shared PostgreSQL implementation of the IVM transition-store contract.

Each operation uses a fresh connection. Process state, event, transition
history and an optional execution-fence watermark commit in one transaction.
CEE remains a separate persistence boundary; this adapter does not claim a
distributed transaction across CEE and IVM.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import closing
from typing import Any, Callable

from transition import Decision, EventIdConflict, ProcessNotFound, ProtocolError


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _request_digest(event: dict[str, Any]) -> str:
    stable = {key: value for key, value in event.items() if key != "execution_fence"}
    return hashlib.sha256(_canonical(stable).encode("utf-8")).hexdigest()


class PostgreSQLProtocolStore:
    """Durable, multi-worker IVM store backed by a shared PostgreSQL database.

    connection_factory must return a fresh, idle psycopg 3 connection. The
    adapter closes every connection it opens; callers retain no connection.
    """

    def __init__(self, connection_factory: Callable[[], Any]):
        if not callable(connection_factory):
            raise ValueError("POSTGRES_CONNECTION_FACTORY_REQUIRED")
        self._connect = connection_factory

    def close(self) -> None:
        """No persistent connection is held by this adapter."""
        return None

    def verify_integrity(self) -> None:
        """Validate durable event/history/process consistency before recovery."""
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT e.event_id,e.outcome,count(h.history_id)
                             FROM ivm_events e
                             LEFT JOIN ivm_transition_history h ON h.event_id=e.event_id
                            GROUP BY e.event_id,e.outcome
                           HAVING (e.outcome='ACCEPTED' AND count(h.history_id)<>1)
                               OR (e.outcome='REJECTED' AND count(h.history_id)<>0)"""
                    )
                    if cursor.fetchone() is not None:
                        raise ProtocolError(
                            "IVM_STORE_INTEGRITY_CHECK_FAILED_REQUIRES_MANUAL_RECONCILIATION"
                        )
                    cursor.execute(
                        """SELECT p.process_id,p.state,p.state_version,count(h.history_id)
                             FROM ivm_processes p
                             LEFT JOIN ivm_transition_history h ON h.process_id=p.process_id
                            GROUP BY p.process_id,p.state,p.state_version
                           HAVING p.state_version<>count(h.history_id)
                               OR (count(h.history_id)>0 AND p.state<>'ACTIVE')"""
                    )
                    if cursor.fetchone() is not None:
                        raise ProtocolError(
                            "IVM_STORE_INTEGRITY_CHECK_FAILED_REQUIRES_MANUAL_RECONCILIATION"
                        )

    def create_process(
        self, *, process_id: str, initial_state: str = "PENDING",
        authority_id: str = "AUTHORITY-001", authority_active: bool = True,
        rule_set: str = "LICENCE-RULES", rule_version: str = "1.0",
        eligible: bool = True,
    ) -> None:
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """INSERT INTO ivm_processes
                           (process_id,state,authority_id,authority_active,rule_set,rule_version,eligible)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (process_id, initial_state, authority_id, authority_active,
                         rule_set, rule_version, eligible),
                    )

    def set_authority_active(self, process_id: str, active: bool) -> None:
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE ivm_processes SET authority_active=%s WHERE process_id=%s",
                        (active, process_id),
                    )

    def set_rule(self, process_id: str, *, version: str, eligible: bool) -> None:
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE ivm_processes SET rule_version=%s,eligible=%s WHERE process_id=%s",
                        (version, eligible, process_id),
                    )

    def process(self, process_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT process_id,state,state_version,authority_id,authority_active,
                                  rule_set,rule_version,eligible
                             FROM ivm_processes WHERE process_id=%s""",
                        (process_id,),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise ProcessNotFound(process_id)
                    names = ("process_id","state","state_version","authority_id",
                             "authority_active","rule_set","rule_version","eligible")
                    return dict(zip(names, row))

    def history(self, process_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT history_id,event_id,process_id,from_state,to_state,
                                  authority_id,rule_set,rule_version
                             FROM ivm_transition_history WHERE process_id=%s
                             ORDER BY created_at,history_id""",
                        (process_id,),
                    )
                    names = ("history_id","event_id","process_id","from_state","to_state",
                             "authority_id","rule_set","rule_version")
                    return [dict(zip(names, row)) for row in cursor.fetchall()]

    def event(self, event_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT event_id,process_id,request_digest,actor_id,event_type,outcome,reason
                             FROM ivm_events WHERE event_id=%s""",
                        (event_id,),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        return None
                    names = ("event_id","process_id","request_digest","actor_id",
                             "event_type","outcome","reason")
                    return dict(zip(names, row))

    def execute(self, event: dict[str, Any], *, fail_before_commit: bool = False) -> Decision:
        required = {"event_id","process_id","actor_id","event_type","rule_set","rule_version"}
        if not isinstance(event, dict) or required - set(event):
            missing = sorted(required - set(event)) if isinstance(event, dict) else sorted(required)
            raise ValueError(f"INVALID_EVENT_FIELDS:{','.join(missing)}")
        if any(not isinstance(event[key], str) or not event[key] for key in required):
            raise ValueError("INVALID_EVENT_FIELD_TYPE_OR_VALUE")
        event = dict(event)
        digest = _request_digest(event)

        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    # Serialize reuse of a global event identity, including
                    # collisions submitted against different process rows.
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                        ("ivm-event:" + event["event_id"],),
                    )
                    cursor.execute(
                        """SELECT event_id,process_id,request_digest,actor_id,event_type,outcome,reason
                             FROM ivm_events WHERE event_id=%s""",
                        (event["event_id"],),
                    )
                    previous = cursor.fetchone()
                    if previous is not None:
                        if previous[2] != digest or previous[1] != event["process_id"]:
                            raise EventIdConflict("EVENT_ID_REUSED_WITH_DIFFERENT_CONTENT")
                        cursor.execute(
                            "SELECT from_state,to_state,history_id FROM ivm_transition_history WHERE event_id=%s",
                            (event["event_id"],),
                        )
                        history = cursor.fetchone()
                        return Decision(
                            status=previous[5], event_id=event["event_id"],
                            process_id=event["process_id"],
                            from_state=history[0] if history else None,
                            to_state=history[1] if history else None,
                            history_id=history[2] if history else None,
                            reason=previous[6], duplicate=True,
                        )

                    cursor.execute(
                        """SELECT process_id,state,state_version,authority_id,authority_active,
                                  rule_set,rule_version,eligible
                             FROM ivm_processes WHERE process_id=%s FOR UPDATE""",
                        (event["process_id"],),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise ProcessNotFound(event["process_id"])
                    names = ("process_id","state","state_version","authority_id",
                             "authority_active","rule_set","rule_version","eligible")
                    process = dict(zip(names, row))

                    reason = None
                    if event["actor_id"] != process["authority_id"]:
                        reason = "AUTHORITY_MISMATCH"
                    elif not process["authority_active"]:
                        reason = "AUTHORITY_REVOKED"
                    elif event["rule_set"] != process["rule_set"] or event["rule_version"] != process["rule_version"]:
                        reason = "RULE_VERSION_MISMATCH"
                    elif not process["eligible"]:
                        reason = "RULE_REJECTED"
                    elif event["event_type"] != "ACTIVATE" or process["state"] != "PENDING":
                        reason = "UNDEFINED_TRANSITION"

                    if reason:
                        cursor.execute(
                            """INSERT INTO ivm_events
                               (event_id,process_id,request_digest,actor_id,event_type,outcome,reason)
                               VALUES (%s,%s,%s,%s,%s,'REJECTED',%s)""",
                            (event["event_id"],event["process_id"],digest,event["actor_id"],
                             event["event_type"],reason),
                        )
                        return Decision(status="REJECTED",event_id=event["event_id"],
                                        process_id=event["process_id"],reason=reason)

                    fence = event.get("execution_fence")
                    if fence is not None:
                        if not isinstance(fence, dict):
                            raise ProtocolError("EXECUTION_FENCE_INVALID")
                        tenant_id = fence.get("tenant_id")
                        deployment_id = fence.get("deployment_id")
                        epoch = fence.get("execution_epoch")
                        token = fence.get("fencing_token")
                        if (not isinstance(tenant_id,str) or not tenant_id
                                or not isinstance(deployment_id,str) or not deployment_id
                                or isinstance(epoch,bool) or not isinstance(epoch,int) or epoch < 1
                                or isinstance(token,bool) or not isinstance(token,int) or token < 1):
                            raise ProtocolError("EXECUTION_FENCE_INVALID")
                        cursor.execute(
                            "SELECT pg_advisory_xact_lock(hashtextextended(%s,1))",
                            ("ivm-fence:" + tenant_id + ":" + deployment_id,),
                        )
                        cursor.execute(
                            """SELECT highest_token,highest_epoch FROM ivm_execution_fences
                                WHERE tenant_id=%s AND deployment_id=%s FOR UPDATE""",
                            (tenant_id,deployment_id),
                        )
                        watermark = cursor.fetchone()
                        if watermark is not None and (token <= int(watermark[0]) or epoch < int(watermark[1])):
                            raise ProtocolError("EXECUTION_FENCE_STALE")
                        cursor.execute(
                            """INSERT INTO ivm_execution_fences
                               (tenant_id,deployment_id,highest_token,highest_epoch)
                               VALUES (%s,%s,%s,%s)
                               ON CONFLICT (tenant_id,deployment_id) DO UPDATE SET
                                 highest_token=GREATEST(ivm_execution_fences.highest_token,EXCLUDED.highest_token),
                                 highest_epoch=GREATEST(ivm_execution_fences.highest_epoch,EXCLUDED.highest_epoch)""",
                            (tenant_id,deployment_id,token,epoch),
                        )

                    from_state = process["state"]
                    to_state = "ACTIVE"
                    history_id = "H-" + hashlib.sha256(event["event_id"].encode("utf-8")).hexdigest()[:16]
                    cursor.execute(
                        """INSERT INTO ivm_events
                           (event_id,process_id,request_digest,actor_id,event_type,outcome,reason)
                           VALUES (%s,%s,%s,%s,%s,'ACCEPTED',NULL)""",
                        (event["event_id"],event["process_id"],digest,event["actor_id"],event["event_type"]),
                    )
                    cursor.execute(
                        """UPDATE ivm_processes SET state=%s,state_version=state_version+1
                            WHERE process_id=%s AND state=%s""",
                        (to_state,event["process_id"],from_state),
                    )
                    if cursor.rowcount != 1:
                        raise ProtocolError("STATE_CHANGED_DURING_TRANSITION")
                    if fail_before_commit:
                        raise RuntimeError("INJECTED_FAILURE_BEFORE_COMMIT")
                    cursor.execute(
                        """INSERT INTO ivm_transition_history
                           (history_id,event_id,process_id,from_state,to_state,authority_id,rule_set,rule_version)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (history_id,event["event_id"],event["process_id"],from_state,to_state,
                         process["authority_id"],process["rule_set"],process["rule_version"]),
                    )
                    return Decision(status="ACCEPTED",event_id=event["event_id"],
                                    process_id=event["process_id"],from_state=from_state,
                                    to_state=to_state,history_id=history_id)
