# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""MINAH-first ingress gate for the KIN-SYS research prototype.

This is a reference orchestration boundary, not yet wired into the HTTP service or
CEECaseCoordinator. Adapters are injected so no AI, identity, authority, cloud,
hardware, or payment vendor is mandatory. An uncertain in-flight operation is
never automatically resubmitted to CEE; it must be reconciled against CEE state.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Protocol


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
        self, database: str, *, minah: MinahAdapter, identity: IdentityAdapter,
        authority: AuthorityAdapter, cee: CEEAdapter,
    ):
        self.db = sqlite3.connect(database, timeout=10.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS minah_first_ingress (
                operation_id TEXT PRIMARY KEY,
                request_fingerprint TEXT NOT NULL,
                instruction_json TEXT NOT NULL,
                phase TEXT NOT NULL,
                result_json TEXT
            )
        """)
        self.db.commit()
        self.minah, self.identity, self.authority, self.cee = minah, identity, authority, cee

    def close(self) -> None:
        self.db.close()

    def execute(self, instruction: dict[str, Any]) -> dict[str, Any]:
        self._validate_minimum_instruction(instruction)
        operation_id = instruction["operation_id"]
        fingerprint = _fingerprint(instruction)

        self.db.execute("BEGIN IMMEDIATE")
        try:
            prior = self.db.execute(
                "SELECT * FROM minah_first_ingress WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if prior:
                if prior["request_fingerprint"] != fingerprint:
                    raise IngressConflict("OPERATION_ID_REUSED_WITH_DIFFERENT_INSTRUCTION")
                if prior["result_json"]:
                    result = json.loads(prior["result_json"])
                    self.db.commit()
                    return {**result, "duplicate": True}
                # A crash may have occurred after a downstream call. Never guess
                # whether it took effect; require reconciliation, not blind replay.
                self.db.commit()
                return self._unknown(operation_id, "INGRESS_IN_FLIGHT_REQUIRES_RECONCILIATION")
            self.db.execute(
                """INSERT INTO minah_first_ingress
                   (operation_id, request_fingerprint, instruction_json, phase)
                   VALUES (?, ?, ?, 'MINAH_PENDING')""",
                (operation_id, fingerprint, _canonical(instruction)),
            )
            self.db.commit()
        except Exception:
            if self.db.in_transaction:
                self.db.rollback()
            raise

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
