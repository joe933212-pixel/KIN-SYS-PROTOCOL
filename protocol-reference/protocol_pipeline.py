# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Synthetic MINAH -> CEE -> final-decision protocol boundary.

MINAH review is durably recorded before CEE admission. CEE is the sole
externally visible execution boundary; the legacy transition store remains a
private reference implementation detail behind that boundary.

This prototype uses separate SQLite stores and does NOT claim cross-store
atomicity, production identity/legal adapters, or distributed finality.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from integrated_demo import DeterministicMinahAdapter
from transition import EventIdConflict, ProtocolStore


RULE_SET = "KIN-SYS-DEMO-RULES"
RULE_VERSION = "1.0"
AUTHORITY_ID = "AUTHORITY-001"
LIFECYCLE = (
    "REQUESTED",
    "MINAH_REVIEWED",
    "ADMITTED",
    "CONTEXT_ESTABLISHED",
    "AUTHORITY_EVALUATED",
    "EXECUTING",
    "PREPARED",
    "COMMITTING",
    "COMMITTED",
    "FINALIZED",
)


class ExecutionConflict(ValueError):
    """An operation ID was reused with different instruction content."""


class LifecycleViolation(RuntimeError):
    """An illegal CEE lifecycle transition was requested."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class CEEJournal:
    """Durable local lifecycle journal with explicit phase and event history."""

    def __init__(self, database: str = ":memory:"):
        self.db = sqlite3.connect(database)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS cee_operations (
                operation_id TEXT PRIMARY KEY,
                request_digest TEXT NOT NULL,
                request_json TEXT NOT NULL,
                phase TEXT NOT NULL,
                minah_json TEXT,
                ivm_json TEXT,
                outcome TEXT,
                result_json TEXT
            );
            CREATE TABLE IF NOT EXISTS cee_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                status TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                UNIQUE(operation_id, phase),
                FOREIGN KEY(operation_id) REFERENCES cee_operations(operation_id)
            );
            """
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def get(self, operation_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM cee_operations WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in ("request_json", "minah_json", "ivm_json", "result_json"):
            if result[key] is not None:
                result[key] = json.loads(result[key])
        return result

    def events(self, operation_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT phase,status,detail_json FROM cee_events WHERE operation_id=? ORDER BY event_id",
            (operation_id,),
        ).fetchall()
        return [
            {"phase": row["phase"], "status": row["status"], "detail": json.loads(row["detail_json"])}
            for row in rows
        ]

    def start(self, operation_id: str, request: dict[str, Any]) -> dict[str, Any]:
        digest = _digest(request)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing = self.db.execute(
                "SELECT * FROM cee_operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if existing:
                if existing["request_digest"] != digest:
                    raise ExecutionConflict("OPERATION_ID_REUSED_WITH_DIFFERENT_CONTENT")
                self.db.commit()
                return self.get(operation_id)  # type: ignore[return-value]
            self.db.execute(
                """INSERT INTO cee_operations
                   (operation_id,request_digest,request_json,phase)
                   VALUES(?,?,?,'REQUESTED')""",
                (operation_id, digest, _canonical(request)),
            )
            self.db.execute(
                "INSERT INTO cee_events(operation_id,phase,status,detail_json) VALUES(?,?,?,?)",
                (operation_id, "REQUESTED", "ACCEPTED", _canonical({"request_digest": digest})),
            )
            self.db.commit()
            return self.get(operation_id)  # type: ignore[return-value]
        except Exception:
            self.db.rollback()
            raise

    def advance(
        self,
        operation_id: str,
        next_phase: str,
        *,
        status: str = "ACCEPTED",
        detail: dict[str, Any] | None = None,
        minah: dict[str, Any] | None = None,
        ivm: dict[str, Any] | None = None,
        outcome: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if next_phase not in LIFECYCLE:
            raise LifecycleViolation("UNKNOWN_CEE_PHASE")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT * FROM cee_operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if row is None:
                raise LifecycleViolation("OPERATION_NOT_FOUND")
            current = row["phase"]
            if current == next_phase:
                self.db.commit()
                return self.get(operation_id)  # type: ignore[return-value]
            if current == "FINALIZED":
                raise LifecycleViolation("FINALIZED_OPERATION_IS_IMMUTABLE")
            current_index = LIFECYCLE.index(current)
            next_index = LIFECYCLE.index(next_phase)
            allowed = next_index == current_index + 1
            terminal_branch = (
                next_phase == "FINALIZED"
                and (
                    (current in {"MINAH_REVIEWED", "AUTHORITY_EVALUATED"} and outcome in {"DEFERRED", "REJECTED"})
                    or (current == "COMMITTING" and outcome == "REJECTED")
                )
            )
            if not allowed and not terminal_branch:
                raise LifecycleViolation(f"ILLEGAL_CEE_TRANSITION:{current}->{next_phase}")
            self.db.execute(
                """UPDATE cee_operations SET phase=?,minah_json=COALESCE(?,minah_json),
                   ivm_json=COALESCE(?,ivm_json),outcome=COALESCE(?,outcome),
                   result_json=COALESCE(?,result_json) WHERE operation_id=?""",
                (
                    next_phase,
                    _canonical(minah) if minah is not None else None,
                    _canonical(ivm) if ivm is not None else None,
                    outcome,
                    _canonical(result) if result is not None else None,
                    operation_id,
                ),
            )
            self.db.execute(
                "INSERT INTO cee_events(operation_id,phase,status,detail_json) VALUES(?,?,?,?)",
                (operation_id, next_phase, status, _canonical(detail or {})),
            )
            self.db.commit()
            return self.get(operation_id)  # type: ignore[return-value]
        except Exception:
            self.db.rollback()
            raise


class KinSysProtocolPipeline:
    """Synthetic full-path harness that makes MINAH, IVM and CEE boundaries observable."""

    def __init__(
        self,
        *,
        cee_database: str = ":memory:",
        ivm_database: str | None = None,
        minah_adapter: Any | None = None,
        authority_active: bool = True,
    ):
        if ivm_database is not None and ivm_database not in {cee_database, ":memory:"}:
            raise ValueError("SPLIT_RUNTIME_STORAGE_NO_LONGER_SUPPORTED")
        self.cee = CEEJournal(cee_database)
        # CEE owns the connection. The transition implementation is a private
        # runtime detail sharing CEE's local store, not a separate execution DB.
        self.ivm = ProtocolStore(connection=self.cee.db)
        self.minah = minah_adapter or DeterministicMinahAdapter()
        self.authority_active = authority_active

    def close(self) -> None:
        self.cee.close()
        self.ivm.close()

    def execute(self, request: dict[str, Any], *, fail_after_ivm_commit: bool = False) -> dict[str, Any]:
        self._validate(request)
        operation_id = request["operation_id"]
        operation = self.cee.start(operation_id, request)
        if operation["phase"] == "FINALIZED":
            result = operation["result_json"]
            return self._response_result(operation_id, result, duplicate=True)

        evidence = request["evidence"]
        operation = self.cee.get(operation_id)
        assert operation is not None

        # MINAH-first ingress: the request has a durable REQUESTED receipt, but
        # CEE does not admit it or establish an execution context until MINAH's
        # non-authorising preliminary review has been validated and pinned.
        if operation["phase"] == "REQUESTED":
            disposition = self.minah.review(evidence)
            contract_error = self._minah_contract_error(disposition)
            if contract_error:
                disposition = {
                    "status": "INVALID_DISPOSITION",
                    "missing_fields": [],
                    "source_refs": [],
                    "uncertainty": "HIGH",
                    "may_authorise": False,
                    "model_version": "UNTRUSTED_OUTPUT",
                    "contract_error": contract_error,
                }
                self.cee.advance(
                    operation_id, "MINAH_REVIEWED", status="REJECTED",
                    detail={"minah_contract_error": contract_error, "cee_not_admitted": True},
                    minah=disposition,
                )
                result = {
                    "operation_id": operation_id,
                    "status": "REJECTED",
                    "final_decision": "REJECTED",
                    "reason": "MINAH_DISPOSITION_CONTRACT_VIOLATION",
                    "minah_disposition": disposition,
                    "execution_result": None,
                    "funds_moved": False,
                }
                self.cee.advance(
                    operation_id, "FINALIZED", status="REJECTED",
                    outcome="REJECTED", result=result,
                    detail={"reason": result["reason"], "cee_not_admitted": True},
                )
                return self._response_result(operation_id, result, duplicate=False)
            self.cee.advance(
                operation_id, "MINAH_REVIEWED",
                status="DEFERRED" if disposition["status"] == "NEEDS_INFORMATION" else "REVIEWED",
                detail={"may_authorise": False, "cee_admission": "NOT_YET_GRANTED"},
                minah=disposition,
            )

        operation = self.cee.get(operation_id)
        assert operation is not None
        disposition = operation["minah_json"]

        if disposition and disposition["status"] == "NEEDS_INFORMATION":
            result = {
                "operation_id": operation_id,
                "status": "DEFERRED",
                "final_decision": "DEFERRED",
                "reason": "MINAH_REQUIRES_ADDITIONAL_EVIDENCE",
                "minah_disposition": disposition,
                "execution_result": None,
                "funds_moved": False,
            }
            if operation["phase"] == "MINAH_REVIEWED":
                self.cee.advance(
                    operation_id, "FINALIZED", status="DEFERRED",
                    outcome="DEFERRED", result=result,
                    detail={"reason": result["reason"], "cee_not_admitted": True},
                )
            return self._response_result(operation_id, result, duplicate=False)

        # Synthetic server-side pre-admission policy gate. This checks the
        # configured demo authority and pinned demo rules; it is NOT identity
        # verification against a national/enterprise registry.
        if operation["phase"] == "MINAH_REVIEWED":
            gate_error = self._pre_admission_error(request)
            if gate_error:
                result = {
                    "operation_id": operation_id,
                    "status": "REJECTED",
                    "final_decision": "REJECTED",
                    "reason": gate_error,
                    "minah_disposition": disposition,
                    "execution_result": None,
                    "funds_moved": False,
                }
                self.cee.advance(
                    operation_id, "FINALIZED", status="REJECTED",
                    outcome="REJECTED", result=result,
                    detail={
                        "reason": gate_error,
                        "cee_not_admitted": True,
                        "pre_admission_gate": "SYNTHETIC_SERVER_SIDE_POLICY_ONLY",
                    },
                )
                return self._response_result(operation_id, result, duplicate=False)

        # Only an acceptable MINAH disposition and server-side pre-admission
        # checks can cross into CEE admission.
        if operation["phase"] == "MINAH_REVIEWED":
            self.cee.advance(
                operation_id, "ADMITTED",
                detail={"admission": "MINAH_PRELIMINARY_REVIEW_ACCEPTED"},
            )
        operation = self.cee.get(operation_id)
        assert operation is not None
        if operation["phase"] == "ADMITTED":
            self.cee.advance(
                operation_id, "CONTEXT_ESTABLISHED",
                detail={
                    "jurisdiction": request["jurisdiction"],
                    "rule_set": request["rule_set"],
                    "rule_version": request["rule_version"],
                    "evidence_digest": _digest(evidence),
                },
            )
        operation = self.cee.get(operation_id)
        assert operation is not None
        if operation["phase"] == "CONTEXT_ESTABLISHED":
            self.cee.advance(
                operation_id, "AUTHORITY_EVALUATED",
                detail={
                    "cee_execution_context_pinned": True,
                    "minah_review_pinned": True,
                    "authority_adapter": "SYNTHETIC_REFERENCE_ONLY",
                },
                minah=disposition,
            )
        operation = self.cee.get(operation_id)
        assert operation is not None
        disposition = operation["minah_json"]

        # Resume safely from any durable phase. The IVM operation key is stable across retries.
        operation = self.cee.get(operation_id)
        assert operation is not None
        if operation["phase"] == "AUTHORITY_EVALUATED":
            self.cee.advance(operation_id, "EXECUTING", detail={"executor": "IVM_REFERENCE_RUNTIME"})
        operation = self.cee.get(operation_id)
        assert operation is not None

        if operation["phase"] == "EXECUTING":
            process_id = request["process_id"]
            try:
                existing_process = self.ivm.process(process_id)
            except Exception as exc:
                from transition import ProcessNotFound
                if not isinstance(exc, ProcessNotFound):
                    raise
                self.ivm.create_process(
                    process_id=process_id,
                    initial_state="PENDING",
                    authority_id=AUTHORITY_ID,
                    authority_active=self.authority_active,
                    rule_set=RULE_SET,
                    rule_version=RULE_VERSION,
                    eligible=request["evidence"].get("commodity") != "SYNTHETIC_PROHIBITED_COMMODITY",
                )
            else:
                expected_immutable = (
                    AUTHORITY_ID, RULE_SET, RULE_VERSION,
                    int(request["evidence"].get("commodity") != "SYNTHETIC_PROHIBITED_COMMODITY"),
                )
                actual_immutable = (
                    existing_process["authority_id"], existing_process["rule_set"],
                    existing_process["rule_version"], existing_process["eligible"],
                )
                if expected_immutable != actual_immutable:
                    raise ExecutionConflict("PROCESS_CONTEXT_DIFFERS_FROM_PINNED_CONTEXT")
                # Authority status is mutable and must be refreshed from the configured
                # authority adapter before IVM evaluates a resumed instruction.
                if bool(existing_process["authority_active"]) != self.authority_active:
                    self.ivm.set_authority_active(process_id, self.authority_active)
            self.cee.advance(
                operation_id, "PREPARED",
                detail={"cee_preconditions_pinned": True, "process_id": process_id},
            )
        operation = self.cee.get(operation_id)
        assert operation is not None

        if operation["phase"] == "PREPARED":
            self.cee.advance(operation_id, "COMMITTING", detail={"commit_boundary": "CEE_REFERENCE_TRANSACTION"})
        operation = self.cee.get(operation_id)
        assert operation is not None

        ivm_result: dict[str, Any]
        if operation["phase"] == "COMMITTING":
            decision = self.ivm.execute({
                "event_id": request["event_id"],
                "process_id": request["process_id"],
                "actor_id": request["actor_id"],
                "event_type": request["event_type"],
                "rule_set": request["rule_set"],
                "rule_version": request["rule_version"],
            })
            ivm_result = {
                "status": decision.status,
                "event_id": decision.event_id,
                "process_id": decision.process_id,
                "from_state": decision.from_state,
                "to_state": decision.to_state,
                "history_id": decision.history_id,
                "reason": decision.reason,
                "duplicate": decision.duplicate,
            }
            if fail_after_ivm_commit and decision.status == "ACCEPTED":
                raise RuntimeError("INJECTED_FAILURE_AFTER_IVM_COMMIT_BEFORE_CEE_ACK")
            if decision.status != "ACCEPTED":
                result = {
                    "operation_id": operation_id,
                    "status": "REJECTED",
                    "final_decision": "REJECTED",
                    "reason": decision.reason or "CEE_EXECUTION_REJECTED",
                    "minah_disposition": disposition,
                    "execution_result": ivm_result,
                    "funds_moved": False,
                }
                self.cee.advance(
                    operation_id, "FINALIZED", status="REJECTED", outcome="REJECTED",
                    ivm=ivm_result, result=result,
                    detail={"reason": result["reason"], "no_authoritative_transition": True},
                )
                return self._response_result(operation_id, result, duplicate=False)
            self.cee.advance(
                operation_id, "COMMITTED", ivm=ivm_result,
                detail={"cee_commit_observed": True, "history_id": decision.history_id},
            )
        operation = self.cee.get(operation_id)
        assert operation is not None

        if operation["phase"] == "COMMITTED":
            ivm_result = operation["ivm_json"]
            result = {
                "operation_id": operation_id,
                "status": "PASSED",
                "final_decision": "PASSED",
                "reason": None,
                "minah_disposition": disposition,
                "execution_result": ivm_result,
                "funds_moved": False,
                "commit_scope": "LOCAL_CEE_REFERENCE_STORE_ONLY_NOT_DISTRIBUTED_FINALITY",
            }
            self.cee.advance(
                operation_id, "FINALIZED", status="PASSED", outcome="PASSED",
                result=result,
                detail={"result_status": "PASSED", "distributed_finality": False},
            )
            return self._response_result(operation_id, result, duplicate=bool(ivm_result.get("duplicate")))

        operation = self.cee.get(operation_id)
        assert operation is not None
        if operation["phase"] == "FINALIZED":
            result = operation["result_json"]
            return self._response_result(operation_id, result, duplicate=True)
        raise LifecycleViolation(f"UNHANDLED_RESUMABLE_PHASE:{operation['phase']}")

    def inspect(self, operation_id: str) -> dict[str, Any] | None:
        """Read a lifecycle snapshot without mutating or resuming the operation."""
        operation = self.cee.get(operation_id)
        if operation is None:
            return None
        events = self.cee.events(operation_id)
        request = operation["request_json"]
        ivm_event = self.ivm.event(request["event_id"])
        if operation["phase"] == "FINALIZED" and isinstance(operation["result_json"], dict):
            result = operation["result_json"]
            response = self._response_result(operation_id, result, duplicate=False)
            return {
                "operation_id": operation_id,
                "phase": operation["phase"],
                "resumable": False,
                "result": result,
                "cee_events": events,
                "component_trace": response["component_trace"],
                "execution_record": ivm_event,
            }
        return {
            "operation_id": operation_id,
            "phase": operation["phase"],
            "resumable": True,
            "minah_disposition": operation["minah_json"],
            "execution_result": operation["ivm_json"],
            "execution_record": ivm_event,
            "cee_events": events,
            "component_trace": [],
        }

    def _response_result(
        self, operation_id: str, result: dict[str, Any], *, duplicate: bool
    ) -> dict[str, Any]:
        """Return both the CEE phase journal and a unified component-level trace."""
        events = self.cee.events(operation_id)
        trace: list[dict[str, Any]] = []
        sequence = 0
        minah_inserted = False
        ivm_inserted = False
        minah = result.get("minah_disposition")
        ivm = result.get("execution_result")

        def append(component: str, status: str, detail: dict[str, Any]) -> None:
            nonlocal sequence
            sequence += 1
            trace.append({
                "sequence": sequence,
                "component": component,
                "status": status,
                "detail": detail,
            })

        for event in events:
            # REQUESTED is an ingress receipt, not CEE admission; omit it from
            # the component trace so the public flow begins with MINAH.
            if event["phase"] == "REQUESTED":
                continue
            if event["phase"] == "MINAH_REVIEWED" and not minah_inserted and isinstance(minah, dict):
                append("MINAH", str(minah.get("status", "UNKNOWN")), {
                    "advisory_only": True,
                    "may_authorise": False,
                    "model_version": minah.get("model_version"),
                    "source_refs": minah.get("source_refs", []),
                    "uncertainty": minah.get("uncertainty"),
                    "contract_error": minah.get("contract_error"),
                })
                minah_inserted = True
            detail = {
                "phase": event["phase"],
                "event_status": event["status"],
                **event["detail"],
            }
            # The execution engine is an internal CEE implementation detail.
            # Keep its result in the CEE trace rather than exposing a second path.
            if event["phase"] in {"COMMITTED", "FINALIZED"} and isinstance(ivm, dict):
                detail["execution_result"] = {
                    "status": ivm.get("status"),
                    "event_id": ivm.get("event_id"),
                    "from_state": ivm.get("from_state"),
                    "to_state": ivm.get("to_state"),
                    "history_id": ivm.get("history_id"),
                    "reason": ivm.get("reason"),
                    "duplicate": ivm.get("duplicate", False),
                }
            append("CEE", event["phase"], detail)
        return {
            **result,
            "duplicate": duplicate,
            "cee_events": events,
            "component_trace": trace,
        }

    def _pre_admission_error(self, request: dict[str, Any]) -> str | None:
        """Return a fail-closed synthetic policy rejection before CEE admission."""
        if request["actor_id"] != AUTHORITY_ID:
            return "AUTHORITY_MISMATCH"
        if not self.authority_active:
            return "AUTHORITY_REVOKED"
        if request["rule_set"] != RULE_SET or request["rule_version"] != RULE_VERSION:
            return "UNSUPPORTED_RULE_VERSION"
        if request["evidence"].get("commodity") == "SYNTHETIC_PROHIBITED_COMMODITY":
            return "RULE_REJECTED"
        return None

    @staticmethod
    def _minah_contract_error(disposition: Any) -> str | None:
        if not isinstance(disposition, dict):
            return "OUTPUT_NOT_OBJECT"
        required = {"status", "missing_fields", "source_refs", "uncertainty", "may_authorise", "model_version"}
        if required - set(disposition):
            return "MISSING_REQUIRED_FIELDS"
        if disposition.get("status") not in {"NEEDS_INFORMATION", "ADVISORY_ONLY"}:
            return "INVALID_STATUS"
        if disposition.get("may_authorise") is not False:
            return "MINAH_MUST_NOT_AUTHORISE"
        if not isinstance(disposition.get("missing_fields"), list) or not all(
            isinstance(item, str) for item in disposition["missing_fields"]
        ):
            return "INVALID_MISSING_FIELDS"
        if not isinstance(disposition.get("source_refs"), list) or not all(
            isinstance(item, str) for item in disposition["source_refs"]
        ):
            return "INVALID_SOURCE_REFERENCES"
        if disposition.get("uncertainty") not in {"LOW", "MEDIUM", "HIGH"}:
            return "INVALID_UNCERTAINTY"
        if not isinstance(disposition.get("model_version"), str) or not disposition["model_version"]:
            return "INVALID_MODEL_VERSION"
        return None

    @staticmethod
    def _validate(request: dict[str, Any]) -> None:
        required = {
            "operation_id", "event_id", "process_id", "actor_id", "event_type",
            "jurisdiction", "rule_set", "rule_version", "evidence",
        }
        if not isinstance(request, dict) or required - set(request):
            missing = sorted(required - set(request)) if isinstance(request, dict) else sorted(required)
            raise ValueError("MISSING_REQUIRED_FIELDS:" + ",".join(missing))
        unknown = sorted(set(request) - required)
        if unknown:
            raise ValueError("UNKNOWN_FIELDS:" + ",".join(unknown))
        for key in (
            "operation_id", "event_id", "process_id", "actor_id", "event_type",
            "jurisdiction", "rule_set", "rule_version",
        ):
            if not isinstance(request[key], str) or not request[key]:
                raise ValueError("INVALID_FIELD:" + key)
        if request["event_type"] != "ACTIVATE":
            raise ValueError("UNSUPPORTED_EVENT_TYPE")
        if not isinstance(request["evidence"], dict):
            raise ValueError("EVIDENCE_MUST_BE_OBJECT")
        if request["rule_set"] != RULE_SET or request["rule_version"] != RULE_VERSION:
            raise ValueError("UNRECOGNIZED_SYNTHETIC_RULE_CONTEXT")
