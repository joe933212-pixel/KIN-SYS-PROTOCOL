# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""MINAH-first ingress gate for the KIN-SYS research prototype.

This module provides a durable ingress journal. In HTTP integration mode it wraps
the canonical KinSysProtocolPipeline rather than implementing a second execution
engine; the pipeline remains responsible for MINAH, identity, authority, CEE/IVM,
and final decision ordering. Its standalone adapter mode remains a conformance
harness. An uncertain in-flight operation is never automatically resubmitted.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from minah_profile_governance import MinahApprovalViolation
from governed_contracts import assert_instruction_binding, verify_instruction_payload_digest, validate_decision_envelope


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class IngressConflict(ValueError):
    """The same operation ID was reused with different instruction content."""


class MinahAdapter(Protocol):
    def review_instruction(self, instruction: dict[str, Any]) -> dict[str, Any]: ...


class IdentityAdapter(Protocol):
    def verify_principal(self, instruction: dict[str, Any]) -> dict[str, Any]: ...


class AuthorityAdapter(Protocol):
    def verify_authority(self, instruction: dict[str, Any]) -> dict[str, Any]: ...


class CEEAdapter(Protocol):
    def execute_governed_instruction(
        self, instruction: dict[str, Any], *, minah_disposition: dict[str, Any],
        identity_result: dict[str, Any], authority_result: dict[str, Any],
    ) -> dict[str, Any]: ...


class MinahFirstIngress:
    """Persist ingress idempotency and enforce MINAH -> identity/authority -> CEE.

    A PASS from MINAH is advisory, never institutional authorisation. Identity and
    authority adapters must return explicit verified=True. The CEE adapter must
    still enforce institutional rules, IVM/CISA semantics, and effect controls.
    """

    def __init__(
        self, database: str, *, minah: MinahAdapter | None = None,
        identity: IdentityAdapter | None = None,
        authority: AuthorityAdapter | None = None,
        cee: CEEAdapter | None = None,
        pipeline_executor: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        pipeline_recovery: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ):
        adapters = (minah, identity, authority, cee)
        if pipeline_executor is None and any(adapter is None for adapter in adapters):
            raise ValueError("MINAH_IDENTITY_AUTHORITY_AND_CEE_ADAPTERS_REQUIRED")
        if pipeline_executor is not None and any(adapter is not None for adapter in adapters):
            raise ValueError("PIPELINE_EXECUTOR_CANNOT_BE_COMBINED_WITH_GATE_ADAPTERS")
        self.pipeline_executor = pipeline_executor
        self.pipeline_recovery = pipeline_recovery
        self.db = sqlite3.connect(database, timeout=10.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS minah_first_ingress (
                operation_id TEXT PRIMARY KEY,
                request_fingerprint TEXT NOT NULL,
                instruction_json TEXT NOT NULL,
                phase TEXT NOT NULL,
                result_json TEXT,
                execution_request_json TEXT,
                execution_request_fingerprint TEXT
            )
        """)
        columns = {
            row["name"] for row in self.db.execute(
                "PRAGMA table_info(minah_first_ingress)"
            ).fetchall()
        }
        if "execution_request_json" not in columns:
            self.db.execute(
                "ALTER TABLE minah_first_ingress ADD COLUMN execution_request_json TEXT"
            )
        if "execution_request_fingerprint" not in columns:
            self.db.execute(
                "ALTER TABLE minah_first_ingress ADD COLUMN execution_request_fingerprint TEXT"
            )
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS minah_first_ingress_recovery_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL,
                action TEXT NOT NULL,
                phase_before TEXT,
                phase_after TEXT,
                evidence_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        self.db.commit()
        self.minah, self.identity, self.authority, self.cee = minah, identity, authority, cee

    def close(self) -> None:
        self.db.close()

    def inspect(self, operation_id: str) -> dict[str, Any] | None:
        """Return a minimal ingress status record without exposing the stored request body."""
        row = self.db.execute(
            "SELECT operation_id, phase, result_json FROM minah_first_ingress WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "operation_id": row["operation_id"],
            "phase": row["phase"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
        }

    def recovery_events(self, operation_id: str) -> list[dict[str, Any]]:
        """Return the append-only recovery audit trail for one operation."""
        rows = self.db.execute(
            """SELECT action, phase_before, phase_after, evidence_json, created_at
               FROM minah_first_ingress_recovery_events
               WHERE operation_id=? ORDER BY event_id""",
            (operation_id,),
        ).fetchall()
        return [{
            "action": row["action"],
            "phase_before": row["phase_before"],
            "phase_after": row["phase_after"],
            "evidence": json.loads(row["evidence_json"]),
            "created_at": row["created_at"],
        } for row in rows]

    def _record_recovery_event(
        self, operation_id: str, action: str, phase_before: str | None,
        phase_after: str | None, evidence: dict[str, Any],
    ) -> None:
        self.db.execute(
            """INSERT INTO minah_first_ingress_recovery_events
               (operation_id, action, phase_before, phase_after, evidence_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                operation_id, action, phase_before, phase_after, _canonical(evidence),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.db.commit()

    def recover(
        self, operation_id: str, *,
        pipeline_recovery: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        pipeline_inspector: Callable[[str], dict[str, Any] | None] | None = None,
    ) -> dict[str, Any] | None:
        """Explicitly recover from the pinned CEE lifecycle; never resubmit a new request."""
        row = self.db.execute(
            """SELECT operation_id, instruction_json, phase, result_json,
                      execution_request_json, execution_request_fingerprint
               FROM minah_first_ingress WHERE operation_id=?""",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        if row["result_json"]:
            return {**json.loads(row["result_json"]), "duplicate": True}

        phase_before = row["phase"]
        recovery_callback = pipeline_recovery or self.pipeline_recovery
        if recovery_callback is None:
            self._record_recovery_event(
                operation_id, "RECOVERY_BLOCKED", phase_before, phase_before,
                {"reason": "PIPELINE_RECOVERY_NOT_CONFIGURED"},
            )
            return self._unknown(operation_id, "PIPELINE_RECOVERY_NOT_CONFIGURED")
        if not row["execution_request_json"] or not row["execution_request_fingerprint"]:
            self._record_recovery_event(
                operation_id, "RECOVERY_BLOCKED", phase_before, phase_before,
                {"reason": "DURABLE_EXECUTION_REQUEST_MISSING"},
            )
            return self._unknown(operation_id, "DURABLE_EXECUTION_REQUEST_MISSING")

        instruction = json.loads(row["instruction_json"])
        execution_request = json.loads(row["execution_request_json"])
        if _fingerprint(execution_request) != row["execution_request_fingerprint"]:
            self._record_recovery_event(
                operation_id, "RECOVERY_BLOCKED", phase_before, phase_before,
                {"reason": "DURABLE_EXECUTION_REQUEST_FINGERPRINT_MISMATCH"},
            )
            return self._unknown(operation_id, "DURABLE_EXECUTION_REQUEST_FINGERPRINT_MISMATCH")

        cee_snapshot = None
        if pipeline_inspector is not None:
            try:
                cee_snapshot = pipeline_inspector(operation_id)
            except Exception as exc:
                self._record_recovery_event(
                    operation_id, "RECOVERY_BLOCKED", phase_before, phase_before,
                    {"reason": "CEE_INSPECTION_UNAVAILABLE", "exception_type": type(exc).__name__},
                )
                return self._unknown(operation_id, "CEE_INSPECTION_UNAVAILABLE")
            if cee_snapshot is None:
                self._record_recovery_event(
                    operation_id, "RECOVERY_BLOCKED", phase_before, phase_before,
                    {"reason": "CEE_OPERATION_NOT_FOUND_REQUIRES_MANUAL_RECONCILIATION"},
                )
                return self._unknown(operation_id, "CEE_OPERATION_NOT_FOUND_REQUIRES_MANUAL_RECONCILIATION")
        self._record_recovery_event(
            operation_id, "RECOVERY_STARTED", phase_before, phase_before,
            {
                "execution_request_fingerprint": row["execution_request_fingerprint"],
                "policy": "RESUME_PINNED_CEE_PHASE_ONLY",
                "cee_phase_before": cee_snapshot.get("phase") if isinstance(cee_snapshot, dict) else None,
            },
        )
        try:
            pipeline_result = recovery_callback(operation_id, execution_request)
            status_map = {
                "COMMITTED": "PASSED", "PASSED": "PASSED", "REJECTED": "REJECTED",
                "DEFERRED": "DEFERRED", "UNKNOWN": "UNKNOWN",
            }
            status = status_map.get(
                pipeline_result.get("status") if isinstance(pipeline_result, dict) else None,
                "UNKNOWN",
            )
            if (
                status == "UNKNOWN"
                and isinstance(pipeline_result, dict)
                and (
                    pipeline_result.get("recovery_required") is True
                    or pipeline_result.get("reason") in {
                        "RECOVERY_REQUIRES_MANUAL_RECONCILIATION",
                        "FINALIZED_CEE_RESULT_MISSING",
                        "OPERATION_NOT_FOUND",
                    }
                )
            ):
                # UNKNOWN with recovery_required is a non-final operational state,
                # not a terminal protocol decision. Keep result_json empty so a
                # later recovery attempt can proceed when durable evidence returns.
                self._set_phase(operation_id, "UNKNOWN_REQUIRES_RECONCILIATION")
                self._record_recovery_event(
                    operation_id, "RECOVERY_BLOCKED", phase_before,
                    "UNKNOWN_REQUIRES_RECONCILIATION",
                    {
                        "reason": str(pipeline_result.get("reason")),
                        "cee_phase": pipeline_result.get("cee_phase"),
                    },
                )
                unresolved = self._unknown(
                    operation_id, str(pipeline_result.get("reason") or "RECOVERY_REQUIRES_RECONCILIATION")
                )
                unresolved.update({
                    "recovery_required": True,
                    "contract_version": instruction.get("contract_version"),
                    "request_id": instruction.get("request_id"),
                    "rule_pack_version": instruction.get("rule_pack_version"),
                })
                return unresolved
            messages = {
                "PASSED": "The durable protocol lifecycle was recovered. No external effect is implied.",
                "REJECTED": "The recovered protocol lifecycle rejected the instruction.",
                "DEFERRED": "The recovered protocol lifecycle requires additional information or review.",
                "UNKNOWN": "The operation remains uncertain. Do not retry it; further reconciliation is required.",
            }
            result = self._decision(
                operation_id, status,
                str(pipeline_result.get("reason") or "RECOVERED_DURABLE_LIFECYCLE")
                if isinstance(pipeline_result, dict) else "INVALID_PIPELINE_RECOVERY_RESPONSE",
                messages[status],
                minah_ref=f"minah-disposition:{operation_id}",
                cee_ref=f"cee-operation:{operation_id}",
                external_effect_status="NOT_ATTEMPTED",
                evidence_refs=instruction.get("evidence_refs", []),
            )
            result.update({
                "contract_version": instruction.get("contract_version"),
                "request_id": instruction.get("request_id"),
                "rule_pack_version": instruction.get("rule_pack_version"),
                "pipeline_result": pipeline_result,
                "recovery": {
                    "recovered": status != "UNKNOWN",
                    "source": "PINNED_CEE_LIFECYCLE",
                    "execution_request_fingerprint": row["execution_request_fingerprint"],
                },
            })
            return self._finish_recovery(
                operation_id, result, phase_before,
                {
                    "pipeline_status": pipeline_result.get("status") if isinstance(pipeline_result, dict) else None,
                    "protocol_decision": result["protocol_decision"],
                    "cee_phase": pipeline_result.get("phase") if isinstance(pipeline_result, dict) else None,
                },
            )
        except Exception as exc:
            self._set_phase(operation_id, "UNKNOWN_REQUIRES_RECONCILIATION")
            self._record_recovery_event(
                operation_id, "RECOVERY_BLOCKED", phase_before,
                "UNKNOWN_REQUIRES_RECONCILIATION",
                {"reason": str(exc)[:240], "exception_type": type(exc).__name__},
            )
            return self._unknown(operation_id, "RECOVERY_REQUIRES_RECONCILIATION")

    def reconcile(
        self, operation_id: str, pipeline_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Finalize an uncertain ingress record only when CEE already has a final result.

        This method never resumes execution. Non-final or absent CEE state remains
        unresolved and must not be resubmitted automatically.
        """
        row = self.db.execute(
            "SELECT operation_id, phase, instruction_json, result_json FROM minah_first_ingress WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        if row["result_json"]:
            return self.inspect(operation_id)
        if not isinstance(pipeline_snapshot, dict) or pipeline_snapshot.get("phase") != "FINALIZED":
            return self.inspect(operation_id)
        raw_result = pipeline_snapshot.get("result")
        if not isinstance(raw_result, dict):
            return self.inspect(operation_id)

        instruction = verify_instruction_payload_digest(json.loads(row["instruction_json"]))
        status = {
            "COMMITTED": "PASSED",
            "PASSED": "PASSED",
            "REJECTED": "REJECTED",
            "DEFERRED": "DEFERRED",
            "UNKNOWN": "UNKNOWN",
        }.get(raw_result.get("status"), "UNKNOWN")
        messages = {
            "PASSED": "The required synthetic protocol gates passed. No external effect is implied.",
            "REJECTED": "A required protocol gate rejected the instruction. No external effect was performed.",
            "DEFERRED": "The instruction needs additional information or review before it can proceed.",
            "UNKNOWN": "The outcome could not be established safely. Reconcile the operation before retrying.",
        }
        recovered_pipeline_result = dict(raw_result)
        recovered_pipeline_result["cee_events"] = pipeline_snapshot.get("cee_events", [])
        recovered_pipeline_result["component_trace"] = pipeline_snapshot.get("component_trace", [])
        recovered_pipeline_result["reconciled_from_cee"] = True
        decision = self._decision(
            operation_id, status,
            str(raw_result.get("reason") or "REQUIRED_GATES_PASSED"),
            messages[status],
            minah_ref=f"minah-disposition:{operation_id}",
            cee_ref=f"cee-operation:{operation_id}",
            external_effect_status="NOT_ATTEMPTED",
            evidence_refs=instruction.get("evidence_refs", []),
        )
        decision.update({
            "contract_version": instruction["contract_version"],
            "request_id": instruction["request_id"],
            "rule_pack_version": instruction["rule_pack_version"],
            "pipeline_result": recovered_pipeline_result,
            "reconciled": True,
        })
        checked = validate_decision_envelope({
            key: decision[key] for key in (
                "contract_version", "request_id", "operation_id", "protocol_decision",
                "reason_code", "user_message", "rule_pack_version", "minah_disposition_ref",
                "cee_execution_ref", "external_effect_status", "evidence_refs",
            )
        })
        assert_instruction_binding(instruction, checked)
        return self._finish(operation_id, decision)

    def execute(
        self, instruction: dict[str, Any], *,
        execution_request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._validate_minimum_instruction(instruction)
        operation_id = instruction["operation_id"]
        fingerprint = _fingerprint(instruction)
        effective_execution_request = dict(execution_request or instruction)
        execution_request_fingerprint = _fingerprint(effective_execution_request)

        self.db.execute("BEGIN IMMEDIATE")
        try:
            prior = self.db.execute(
                "SELECT * FROM minah_first_ingress WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if prior:
                if prior["request_fingerprint"] != fingerprint:
                    raise IngressConflict("OPERATION_ID_REUSED_WITH_DIFFERENT_INSTRUCTION")
                if (
                    prior["execution_request_fingerprint"] is not None
                    and prior["execution_request_fingerprint"] != execution_request_fingerprint
                ):
                    raise IngressConflict("OPERATION_ID_REUSED_WITH_DIFFERENT_EXECUTION_REQUEST")
                if prior["result_json"]:
                    result = json.loads(prior["result_json"])
                    self.db.commit()
                    return {**result, "duplicate": True}
                # A crash may have occurred after a downstream call. Never guess
                # whether it took effect; require reconciliation, not blind replay.
                stored_instruction = json.loads(prior["instruction_json"])
                self.db.commit()
                unknown = self._unknown(
                    operation_id, "INGRESS_IN_FLIGHT_REQUIRES_RECONCILIATION"
                )
                if self.pipeline_executor is not None:
                    unknown.update({
                        "contract_version": stored_instruction["contract_version"],
                        "request_id": stored_instruction["request_id"],
                        "rule_pack_version": stored_instruction["rule_pack_version"],
                    })
                return unknown
            self.db.execute(
                """INSERT INTO minah_first_ingress
                   (operation_id, request_fingerprint, instruction_json, phase,
                    execution_request_json, execution_request_fingerprint)
                   VALUES (?, ?, ?, 'MINAH_PENDING', ?, ?)""",
                (
                    operation_id, fingerprint, _canonical(instruction),
                    _canonical(effective_execution_request), execution_request_fingerprint,
                ),
            )
            self.db.commit()
        except Exception:
            if self.db.in_transaction:
                self.db.rollback()
            raise

        if self.pipeline_executor is not None:
            return self._execute_with_pipeline(
                operation_id, instruction, effective_execution_request
            )

        try:
            # MINAH is the first adapter to receive the instruction.
            minah_result = self.minah.review_instruction(dict(instruction))
            if not isinstance(minah_result, dict) or minah_result.get("status") not in {
                "PASS", "REJECTED", "DEFERRED", "UNKNOWN"
            }:
                return self._finish(operation_id, self._decision(
                    operation_id, "REJECTED", "MINAH_INVALID_DISPOSITION",
                    "The request could not be checked safely and was stopped.",
                    minah_ref="minah:invalid",
                ))
            if minah_result["status"] != "PASS":
                status = minah_result["status"]
                return self._finish(operation_id, self._decision(
                    operation_id, status, "MINAH_" + status,
                    "The request did not pass the preliminary review. No downstream execution was started.",
                    minah_ref=str(minah_result.get("disposition_ref", "minah:unreferenced")),
                ))

            identity_result = self.identity.verify_principal(dict(instruction))
            if not isinstance(identity_result, dict) or identity_result.get("verified") is not True:
                return self._finish(operation_id, self._decision(
                    operation_id, "REJECTED", "IDENTITY_NOT_VERIFIED",
                    "Your identity could not be verified. No downstream execution was started.",
                    minah_ref=str(minah_result.get("disposition_ref", "minah:unreferenced")),
                ))

            authority_result = self.authority.verify_authority(dict(instruction))
            if not isinstance(authority_result, dict) or authority_result.get("verified") is not True:
                return self._finish(operation_id, self._decision(
                    operation_id, "REJECTED", "AUTHORITY_NOT_VERIFIED",
                    "Your authority to perform this action could not be verified. No downstream execution was started.",
                    minah_ref=str(minah_result.get("disposition_ref", "minah:unreferenced")),
                ))

            self._set_phase(operation_id, "CEE_PENDING")
            # From this point a process crash can make the downstream outcome
            # uncertain. The operation is not automatically sent again.
            cee_result = self.cee.execute_governed_instruction(
                dict(instruction), minah_disposition=minah_result,
                identity_result=identity_result, authority_result=authority_result,
            )
            if not isinstance(cee_result, dict):
                return self._finish(operation_id, self._decision(
                    operation_id, "UNKNOWN", "CEE_INVALID_RESPONSE",
                    "The execution result could not be confirmed. Check the operation status before retrying.",
                    minah_ref=str(minah_result.get("disposition_ref", "minah:unreferenced")),
                ))
            result = self._decision(
                operation_id,
                cee_result.get("protocol_decision", "UNKNOWN"),
                str(cee_result.get("reason_code", "CEE_RESULT")),
                str(cee_result.get("user_message", "The operation returned a result requiring review.")),
                minah_ref=str(minah_result.get("disposition_ref", "minah:unreferenced")),
                cee_ref=str(cee_result.get("cee_execution_ref", "cee:unreferenced")),
                external_effect_status=str(cee_result.get("external_effect_status", "UNKNOWN")),
                evidence_refs=cee_result.get("evidence_refs", []),
            )
            return self._finish(operation_id, result)
        except Exception:
            # Do not persist a false rejection or retry a potentially executed
            # CEE call. The durable non-final row makes subsequent calls UNKNOWN.
            self._set_phase(operation_id, "UNKNOWN_REQUIRES_RECONCILIATION")
            raise

    def _execute_with_pipeline(
        self, operation_id: str, instruction: dict[str, Any],
        execution_request: dict[str, Any],
    ) -> dict[str, Any]:
        """Journal a contract instruction while delegating all gates to the canonical pipeline."""
        try:
            self._set_phase(operation_id, "PIPELINE_PENDING")
            pipeline_result = self.pipeline_executor(dict(execution_request))
            status_map = {
                "COMMITTED": "PASSED",
                "PASSED": "PASSED",
                "REJECTED": "REJECTED",
                "DEFERRED": "DEFERRED",
                "UNKNOWN": "UNKNOWN",
            }
            status = status_map.get(
                pipeline_result.get("status") if isinstance(pipeline_result, dict) else None,
                "UNKNOWN",
            )
            messages = {
                "PASSED": "The required synthetic protocol gates passed. No external effect is implied.",
                "REJECTED": "A required protocol gate rejected the instruction. No external effect was performed.",
                "DEFERRED": "The instruction needs additional information or review before it can proceed.",
                "UNKNOWN": "The outcome could not be established safely. Reconcile the operation before retrying.",
            }
            result = self._decision(
                operation_id, status,
                str(pipeline_result.get("reason") or "REQUIRED_GATES_PASSED")
                if isinstance(pipeline_result, dict) else "INVALID_PIPELINE_RESPONSE",
                messages[status],
                minah_ref=f"minah-disposition:{operation_id}",
                cee_ref=f"cee-operation:{operation_id}",
                external_effect_status="NOT_ATTEMPTED",
                evidence_refs=instruction.get("evidence_refs", []),
            )
            result.update({
                "contract_version": instruction["contract_version"],
                "request_id": instruction["request_id"],
                "rule_pack_version": instruction["rule_pack_version"],
                "pipeline_result": pipeline_result,
            })
            return self._finish(operation_id, result)
        except MinahApprovalViolation as exc:
            # Profile approval is checked before MINAH and before CEE execution.
            # Record a definitive rejection, not an ambiguous in-flight operation.
            result = self._decision(
                operation_id, "REJECTED", "MINAH_PROFILE_APPROVAL_REJECTED",
                "The configured MINAH profile approval could not be verified. No execution was started.",
                minah_ref=f"minah-disposition:{operation_id}",
            )
            result.update({
                "contract_version": instruction["contract_version"],
                "request_id": instruction["request_id"],
                "rule_pack_version": instruction["rule_pack_version"],
                "error_code": "MINAH_PROFILE_APPROVAL_REJECTED",
                "error_detail": str(exc)[:240],
            })
            return self._finish(operation_id, result)
        except Exception:
            # The pipeline may have reached a durable transition before failing.
            # Do not blindly invoke it again; reconciliation is required.
            self._set_phase(operation_id, "UNKNOWN_REQUIRES_RECONCILIATION")
            raise

    @staticmethod
    def _validate_minimum_instruction(instruction: Any) -> None:
        if not isinstance(instruction, dict):
            raise ValueError("INSTRUCTION_MUST_BE_OBJECT")
        for key in ("request_id", "operation_id", "idempotency_key", "principal_ref", "authority_ref", "action", "target_ref", "purpose", "jurisdiction", "rule_pack_version"):
            if not isinstance(instruction.get(key), str) or not instruction[key].strip():
                raise ValueError("MISSING_OR_INVALID_" + key.upper())

    @staticmethod
    def _decision(
        operation_id: str, status: str, reason_code: str, user_message: str, *,
        minah_ref: str, cee_ref: str = "cee:not-started",
        external_effect_status: str = "NOT_ATTEMPTED",
        evidence_refs: Any = None,
    ) -> dict[str, Any]:
        allowed = {"PASSED", "REJECTED", "DEFERRED", "UNKNOWN"}
        if status not in allowed:
            status = "UNKNOWN"
            reason_code = "INVALID_CEE_DECISION"
        return {
            "operation_id": operation_id,
            "protocol_decision": status,
            "reason_code": reason_code,
            "user_message": user_message,
            "minah_disposition_ref": minah_ref,
            "cee_execution_ref": cee_ref,
            "external_effect_status": external_effect_status,
            "evidence_refs": evidence_refs if isinstance(evidence_refs, list) else [],
        }

    def _finish(self, operation_id: str, result: dict[str, Any]) -> dict[str, Any]:
        self.db.execute(
            "UPDATE minah_first_ingress SET phase=?, result_json=? WHERE operation_id=?",
            (result["protocol_decision"], _canonical(result), operation_id),
        )
        self.db.commit()
        return result

    def _finish_recovery(
        self, operation_id: str, result: dict[str, Any], phase_before: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Commit the recovered decision and its completion audit event atomically."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT result_json FROM minah_first_ingress WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise IngressConflict("INGRESS_OPERATION_NOT_FOUND_DURING_RECOVERY")
            if row["result_json"]:
                existing = json.loads(row["result_json"])
                self.db.commit()
                return {**existing, "duplicate": True}
            self.db.execute(
                "UPDATE minah_first_ingress SET phase=?, result_json=? WHERE operation_id=?",
                (result["protocol_decision"], _canonical(result), operation_id),
            )
            self.db.execute(
                """INSERT INTO minah_first_ingress_recovery_events
                   (operation_id, action, phase_before, phase_after, evidence_json, created_at)
                   VALUES (?, 'RECOVERY_COMPLETED', ?, ?, ?, ?)""",
                (
                    operation_id, phase_before, result["protocol_decision"],
                    _canonical(evidence), datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.db.commit()
            return result
        except Exception:
            if self.db.in_transaction:
                self.db.rollback()
            raise

    def _set_phase(self, operation_id: str, phase: str) -> None:
        self.db.execute(
            "UPDATE minah_first_ingress SET phase=? WHERE operation_id=?",
            (phase, operation_id),
        )
        self.db.commit()

    @staticmethod
    def _unknown(operation_id: str, reason: str) -> dict[str, Any]:
        return MinahFirstIngress._decision(
            operation_id, "UNKNOWN", reason,
            "The operation may still be in progress. Reconcile its status before retrying.",
            minah_ref="minah:recorded-or-pending", cee_ref="cee:unknown",
            external_effect_status="UNKNOWN",
        )
