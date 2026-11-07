# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""End-to-end MINAH -> IVM -> CEE execution slice, using synthetic adapters only.

The CEE lifecycle journal and IVM transition store are separate SQLite stores.
This prototype explicitly does NOT claim a distributed atomic commit across them.
A retry after an IVM commit replays the same idempotency key and reconciles CEE
from the durable IVM outcome. Production deployment requires an independently
reviewed recovery protocol and real identity/legal/model/runtime adapters.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from minah_deployment_profile import PROFILE_VERSION, validate_minah_profile
from minah_profile_governance import MinahApprovalViolation, profile_digest, verify_minah_profile_approval
from governed_contracts import verify_instruction_payload_digest

from integrated_demo import DeterministicMinahAdapter
from transition import EventIdConflict, ProtocolStore


RULE_SET = "KIN-SYS-DEMO-RULES"
RULE_VERSION = "1.0"
AUTHORITY_ID = "AUTHORITY-001"
LIFECYCLE = (
    "REQUESTED",
    "MINAH_REVIEWED",
    "IDENTITY_VERIFIED",
    "AUTHORITY_VERIFIED",
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


class SyntheticIdentityAdapter:
    """Local demonstration stub; this is not authentication of a real person."""

    def verify_principal(self, request: dict[str, Any]) -> dict[str, Any]:
        actor = request.get("actor_id")
        return {"verified": isinstance(actor, str) and bool(actor.strip()), "mode": "SYNTHETIC_SCHEMA_CHECK"}


class SyntheticAuthorityAdapter:
    """Local demonstration stub; this does not establish legal authority."""

    def __init__(self, *, authority_active: bool = True):
        self.authority_active = authority_active

    def verify_action(self, request: dict[str, Any]) -> dict[str, Any]:
        actor = request.get("actor_id")
        action = request.get("event_type")
        return {
            "verified": self.authority_active and actor == AUTHORITY_ID and action == "ACTIVATE",
            "mode": "SYNTHETIC_RULE_CHECK",
        }


class LifecycleViolation(RuntimeError):
    """An illegal CEE lifecycle transition was requested."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe_verification_metadata(result: dict[str, Any], allowed_fields: tuple[str, ...]) -> dict[str, str]:
    """Keep stable evidence references and version metadata, never raw identity data."""
    metadata: dict[str, str] = {}
    for field in allowed_fields:
        value = result.get(field)
        if isinstance(value, str) and value.strip() and len(value.strip()) <= 256:
            metadata[field] = value.strip()
    return metadata

_VERIFICATION_POSITIVE = {"VERIFIED", "APPROVED", "ACTIVE", "VALID", "PASSED"}
_VERIFICATION_NEGATIVE = {"REJECTED", "DENIED", "REVOKED", "EXPIRED", "INVALID", "NOT_AUTHORISED", "NOT_AUTHORIZED"}
_VERIFICATION_UNKNOWN = {"UNKNOWN", "UNAVAILABLE", "TIMEOUT", "PENDING", "ERROR", "INDETERMINATE", "VERIFICATION_UNAVAILABLE", "REVOCATION_UNKNOWN"}


def _verification_status(result: Any) -> str:
    """Interpret adapter output conservatively; contradictory or malformed output never passes."""
    if not isinstance(result, dict):
        return "UNKNOWN"
    verified = result.get("verified")
    status = result.get("status")
    if isinstance(status, str):
        status = status.strip().upper()
    elif status is not None:
        return "UNKNOWN"

    if verified is True:
        if status is None or status in _VERIFICATION_POSITIVE:
            return "VERIFIED"
        if status in _VERIFICATION_NEGATIVE:
            return "REJECTED"
        return "UNKNOWN"
    if verified is False:
        if status in _VERIFICATION_UNKNOWN:
            return "UNKNOWN"
        if status in _VERIFICATION_POSITIVE:
            return "UNKNOWN"
        if status is None or status in _VERIFICATION_NEGATIVE:
            return "REJECTED"
        return "UNKNOWN"
    return "UNKNOWN"


def _safe_verification_result(result: Any) -> dict[str, Any]:
    """Return a bounded decision summary, never echo arbitrary adapter payloads."""
    status = _verification_status(result)
    safe: dict[str, Any] = {"verified": status == "VERIFIED", "status": status}
    if not isinstance(result, dict):
        safe["mode"] = "INVALID_ADAPTER_RESPONSE"
        return safe
    mode = result.get("mode")
    if isinstance(mode, str) and mode.strip() and len(mode.strip()) <= 128:
        safe["mode"] = mode.strip()
    else:
        safe["mode"] = "ADAPTER"
    reason_code = result.get("reason_code")
    if isinstance(reason_code, str) and reason_code.strip() and len(reason_code.strip()) <= 128:
        safe["reason_code"] = reason_code.strip()
    safe.update(_safe_verification_metadata(
        result,
        ("evidence_ref", "verification_ref", "verification_id", "identity_provider_ref",
         "subject_ref", "principal_ref", "mandate_ref", "authority_ref", "policy_version",
         "mandate_version", "scope_ref", "issuer_ref", "verified_at", "valid_until",
         "assurance_level"),
    ))
    return safe


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
            CREATE TABLE IF NOT EXISTS cee_observations (
                observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
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

    def record_observation(
        self, operation_id: str, kind: str, status: str, detail: dict[str, Any],
    ) -> None:
        """Append non-lifecycle verification evidence without rewriting phase history."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT phase FROM cee_operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if row is None:
                raise LifecycleViolation("OPERATION_NOT_FOUND")
            self.db.execute(
                """INSERT INTO cee_observations
                   (operation_id,kind,status,detail_json,created_at)
                   VALUES(?,?,?,?,?)""",
                (operation_id, kind, status, _canonical(detail), datetime.now(timezone.utc).isoformat()),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def observations(self, operation_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """SELECT kind,status,detail_json,created_at FROM cee_observations
               WHERE operation_id=? ORDER BY observation_id""",
            (operation_id,),
        ).fetchall()
        return [
            {"kind": row["kind"], "status": row["status"],
             "detail": json.loads(row["detail_json"]), "created_at": row["created_at"]}
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
                    (current in {"MINAH_REVIEWED", "IDENTITY_VERIFIED", "AUTHORITY_VERIFIED", "AUTHORITY_EVALUATED"} and outcome in {"DEFERRED", "REJECTED", "UNKNOWN"})
                    or (current == "COMMITTING" and outcome in {"REJECTED", "UNKNOWN"})
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

    EXECUTION_ORDER = ("MINAH", "IDENTITY", "AUTHORITY", "CEE", "FINAL_DECISION")
    SUPPORTS_INSTITUTIONAL_RUNTIME_PINS = True

    def __init__(
        self,
        *,
        cee_database: str = ":memory:",
        ivm_database: str = ":memory:",
        minah_adapter: Any | None = None,
        identity_adapter: Any | None = None,
        authority_adapter: Any | None = None,
        authority_active: bool = True,
        minah_profile: dict[str, Any] | None = None,
        minah_profile_approval: dict[str, Any] | None = None,
        profile_signature_verifier: Any | None = None,
        profile_revocation_checker: Any | None = None,
        require_profile_approval: bool = False,
        identity_provider_id: str | None = None,
        identity_provider_version: str | None = None,
        jurisdiction_package_id: str | None = None,
        jurisdiction_package_version: str | None = None,
        authority_profile_version: str | None = None,
    ):
        self.identity_provider_id = identity_provider_id
        self.identity_provider_version = identity_provider_version
        self.jurisdiction_package_id = jurisdiction_package_id
        self.jurisdiction_package_version = jurisdiction_package_version
        self.authority_profile_version = authority_profile_version
        self.cee = CEEJournal(cee_database)
        self.ivm = ProtocolStore(ivm_database)
        self.minah = minah_adapter or DeterministicMinahAdapter()
        self.identity_adapter = identity_adapter or SyntheticIdentityAdapter()
        self.authority_adapter = authority_adapter or SyntheticAuthorityAdapter(authority_active=authority_active)
        self.authority_active = authority_active
        self.minah_profile = validate_minah_profile(minah_profile or {
            "profile_version": PROFILE_VERSION,
            "deployment_id": "synthetic-local-default",
            "owner_scope": "INSTITUTION",
            "owner_ref": "LOCAL-DEMONSTRATOR",
            "use_case": "PROTOCOL_REFERENCE_TEST",
            "jurisdictions": ["UG-DEMO"],
            "policy_pack_version": "KIN-SYS-DEMO-RULES:1.0",
            "source_pack_version": "SYNTHETIC-NO-LEGAL-SOURCES",
            "model_ref": "deterministic-minah-mock",
            "deployment_mode": "SELF_HOSTED",
            "may_authorise": False,
        })
        self.minah_profile_approval = minah_profile_approval
        self.profile_signature_verifier = profile_signature_verifier
        self.profile_revocation_checker = profile_revocation_checker
        self.require_profile_approval = require_profile_approval
        if require_profile_approval and (
            not callable(profile_signature_verifier) or not callable(profile_revocation_checker)
        ):
            raise MinahApprovalViolation("TRUSTED_SIGNATURE_AND_REVOCATION_CHECKERS_REQUIRED")

    def close(self) -> None:
        self.cee.close()
        self.ivm.close()

    def _assert_ivm_event_consistent(self, request: dict[str, Any]) -> None:
        """Reject contradictory IVM event/history evidence before recovery or execution."""
        event_request = {
            key: request[key] for key in (
                "event_id", "process_id", "actor_id", "event_type",
                "rule_set", "rule_version",
            )
        }
        event_id = event_request["event_id"]
        stored_event = self.ivm.event(event_id)
        matching_history = [
            item for item in self.ivm.history(event_request["process_id"])
            if item.get("event_id") == event_id
        ]
        if stored_event is None:
            if matching_history:
                raise ExecutionConflict(
                    "IVM_HISTORY_WITHOUT_EVENT_REQUIRES_MANUAL_RECONCILIATION"
                )
            return
        if stored_event.get("request_digest") != _digest(event_request):
            raise ExecutionConflict("IVM_EVENT_DIGEST_MISMATCH_REQUIRES_MANUAL_RECONCILIATION")
        if stored_event.get("process_id") != event_request["process_id"]:
            raise ExecutionConflict("IVM_EVENT_PROCESS_BINDING_MISMATCH")
        if len(matching_history) > 1:
            raise ExecutionConflict("DUPLICATE_IVM_HISTORY_REQUIRES_MANUAL_RECONCILIATION")
        if stored_event.get("outcome") == "ACCEPTED" and len(matching_history) != 1:
            raise ExecutionConflict("IVM_COMMIT_HISTORY_MISSING_REQUIRES_MANUAL_RECONCILIATION")
        if stored_event.get("outcome") != "ACCEPTED" and matching_history:
            raise ExecutionConflict("REJECTED_IVM_EVENT_HAS_TRANSITION_HISTORY")

    def _assert_store_integrity(self) -> None:
        """Fail closed on SQLite structural errors; this is not a tamper-evidence check."""
        for label, connection in (("CEE", self.cee.db), ("IVM", self.ivm.db)):
            try:
                rows = connection.execute("PRAGMA integrity_check").fetchall()
            except sqlite3.DatabaseError as exc:
                raise ExecutionConflict(
                    f"{label}_STORE_INTEGRITY_CHECK_FAILED_REQUIRES_MANUAL_RECONCILIATION"
                ) from exc
            if len(rows) != 1 or rows[0][0] != "ok":
                raise ExecutionConflict(
                    f"{label}_STORE_INTEGRITY_CHECK_FAILED_REQUIRES_MANUAL_RECONCILIATION"
                )

    def _assert_cross_store_consistent(self, operation: dict[str, Any]) -> None:
        """Require CEE lifecycle claims and durable IVM evidence to agree.

        COMMITTING plus a consistent durable IVM event is the one expected
        crash window: recovery may reconcile it. Every other phase/event
        combination must be internally consistent or stop for manual review.
        """
        pinned_request = operation.get("request_json")
        if not isinstance(pinned_request, dict):
            raise ExecutionConflict("CEE_REQUEST_MISSING_REQUIRES_MANUAL_RECONCILIATION")
        request = {
            key: value for key, value in pinned_request.items()
            if not key.startswith("_minah_")
        }
        self._assert_ivm_event_consistent(request)

        phase = operation.get("phase")
        event = self.ivm.event(request["event_id"])
        ivm_decision = operation.get("ivm_json")
        if phase not in LIFECYCLE:
            raise ExecutionConflict("CEE_PHASE_INVALID_REQUIRES_MANUAL_RECONCILIATION")

        if event is not None and phase not in {"COMMITTING", "COMMITTED", "FINALIZED"}:
            raise ExecutionConflict(
                "IVM_EVENT_EXISTS_BEFORE_CEE_COMMIT_BOUNDARY_REQUIRES_MANUAL_RECONCILIATION"
            )

        committed_claim = (
            phase == "COMMITTED"
            or (phase == "FINALIZED" and operation.get("outcome") == "COMMITTED")
        )
        if committed_claim and event is None:
            raise ExecutionConflict(
                "CEE_COMMIT_CLAIM_WITHOUT_DURABLE_IVM_EVENT_REQUIRES_MANUAL_RECONCILIATION"
            )
        if phase == "COMMITTED" and not isinstance(ivm_decision, dict):
            raise ExecutionConflict(
                "CEE_COMMITTED_PHASE_WITHOUT_IVM_DECISION_REQUIRES_MANUAL_RECONCILIATION"
            )

        if event is None:
            if ivm_decision is not None:
                raise ExecutionConflict(
                    "CEE_IVM_DECISION_WITHOUT_DURABLE_EVENT_REQUIRES_MANUAL_RECONCILIATION"
                )
            return

        # An event found after CEE has already finalized is only reconcilable
        # when CEE durably recorded the matching IVM decision as well.
        if phase == "FINALIZED" and not isinstance(ivm_decision, dict):
            raise ExecutionConflict(
                "FINALIZED_CEE_WITH_UNRECORDED_IVM_EVENT_REQUIRES_MANUAL_RECONCILIATION"
            )
        if phase == "FINALIZED":
            expected_final_outcome = (
                "COMMITTED" if event.get("outcome") == "ACCEPTED" else "REJECTED"
            )
            if operation.get("outcome") != expected_final_outcome:
                raise ExecutionConflict(
                    "CEE_FINAL_OUTCOME_DIFFERS_FROM_IVM_EVENT_REQUIRES_MANUAL_RECONCILIATION"
                )
        if ivm_decision is not None:
            expected_status = "ACCEPTED" if event.get("outcome") == "ACCEPTED" else "REJECTED"
            if (
                ivm_decision.get("event_id") != event.get("event_id")
                or ivm_decision.get("process_id") != event.get("process_id")
                or ivm_decision.get("status") != expected_status
            ):
                raise ExecutionConflict(
                    "CEE_IVM_DECISION_DIFFERS_FROM_DURABLE_EVENT_REQUIRES_MANUAL_RECONCILIATION"
                )
            history = [
                item for item in self.ivm.history(event["process_id"])
                if item.get("event_id") == event["event_id"]
            ]
            if expected_status == "ACCEPTED":
                if len(history) != 1 or (
                    ivm_decision.get("history_id") != history[0].get("history_id")
                    or ivm_decision.get("from_state") != history[0].get("from_state")
                    or ivm_decision.get("to_state") != history[0].get("to_state")
                ):
                    raise ExecutionConflict(
                        "CEE_IVM_HISTORY_BINDING_MISMATCH_REQUIRES_MANUAL_RECONCILIATION"
                    )
            elif history:
                raise ExecutionConflict(
                    "REJECTED_IVM_EVENT_HAS_TRANSITION_HISTORY"
                )

    def execute(self, request: dict[str, Any], *, fail_after_ivm_commit: bool = False) -> dict[str, Any]:
        self._validate(request)
        operation_id = request["operation_id"]
        # Existing operations may need recovery after the approval expires. For a
        # genuinely new operation, verify approval before MINAH or CEE admission.
        # Recovery of an existing operation may rely only on the approval metadata
        # already pinned in that operation's durable CEE request record.
        self._assert_store_integrity()
        prior_operation = self.cee.get(operation_id)
        if prior_operation is not None:
            self._assert_cross_store_consistent(prior_operation)
        approval_metadata = None
        if self.require_profile_approval:
            if prior_operation is None:
                approval_metadata = verify_minah_profile_approval(
                    self.minah_profile,
                    self.minah_profile_approval,
                    signature_verifier=self.profile_signature_verifier,
                    revocation_checker=self.profile_revocation_checker,
                )
            else:
                pinned_request = prior_operation["request_json"]
                if _canonical(pinned_request.get("_minah_deployment_profile")) != _canonical(self.minah_profile):
                    raise ExecutionConflict("RECOVERY_PROFILE_DIFFERS_FROM_PINNED_PROFILE")
                if pinned_request.get("_minah_profile_approval") != self.minah_profile_approval:
                    raise ExecutionConflict("RECOVERY_APPROVAL_DIFFERS_FROM_PINNED_APPROVAL")
                approval_metadata = pinned_request.get("_minah_profile_approval_verification")
                if not isinstance(approval_metadata, dict) or approval_metadata.get("status") != "APPROVED":
                    raise ExecutionConflict("PINNED_PROFILE_APPROVAL_EVIDENCE_MISSING")
                # A pinned approval proves what was accepted at initial ingress;
                # it does not prove that the approval remains current after a crash.
                # Recheck expiry, trusted signature and revocation before resuming
                # any operation that has not durably committed its IVM event.
                durable_ivm_event = self.ivm.event(request["event_id"])
                protected_transition_already_durable = (
                    prior_operation["phase"] in {"COMMITTED", "FINALIZED"}
                    or (prior_operation["phase"] == "COMMITTING" and durable_ivm_event is not None)
                )
                if not protected_transition_already_durable:
                    try:
                        approval_metadata = verify_minah_profile_approval(
                            self.minah_profile,
                            self.minah_profile_approval,
                            signature_verifier=self.profile_signature_verifier,
                            revocation_checker=self.profile_revocation_checker,
                        )
                    except MinahApprovalViolation as exc:
                        self.cee.record_observation(
                            operation_id, "MINAH_PROFILE_APPROVAL_REVALIDATION",
                            "REJECTED",
                            {
                                "approval_id": self.minah_profile_approval.get("approval_id")
                                if isinstance(self.minah_profile_approval, dict) else None,
                                "profile_digest": profile_digest(self.minah_profile),
                                "reason": str(exc),
                                "protected_transition_already_durable": False,
                            },
                        )
                        raise
                    self.cee.record_observation(
                        operation_id, "MINAH_PROFILE_APPROVAL_REVALIDATION",
                        "VERIFIED",
                        {
                            "approval_id": approval_metadata["approval_id"],
                            "signer_ref": approval_metadata["signer_ref"],
                            "profile_digest": approval_metadata["profile_digest"],
                            "expires_at": approval_metadata["expires_at"],
                            "protected_transition_already_durable": False,
                        },
                    )
        # Pin the deployment profile into the durable operation fingerprint before
        # MINAH is invoked. Replays cannot silently switch to another profile.
        journal_request = dict(request)
        journal_request["_minah_deployment_profile"] = self.minah_profile
        if self.require_profile_approval:
            journal_request["_minah_profile_approval"] = self.minah_profile_approval
            journal_request["_minah_profile_approval_verification"] = approval_metadata
        if (
            prior_operation is not None
            and prior_operation["phase"] in {"ADMITTED", "CONTEXT_ESTABLISHED"}
            and prior_operation["minah_json"] is None
            and "_minah_deployment_profile" not in prior_operation["request_json"]
        ):
            raise ExecutionConflict("LEGACY_OPERATION_REQUIRES_RECONCILIATION")
        operation = self.cee.start(operation_id, journal_request)
        if operation["phase"] == "FINALIZED":
            result = operation["result_json"]
            return self._response_result(operation_id, result, duplicate=True)

        evidence = request["evidence"]
        operation = self.cee.get(operation_id)
        assert operation is not None

        # A database created by the earlier lifecycle ordering may contain an
        # unfinished CEE-admitted operation without a pinned MINAH disposition.
        # Do not guess or resume it under the new ordering; require reconciliation.
        if operation["phase"] in {"ADMITTED", "CONTEXT_ESTABLISHED"} and operation["minah_json"] is None:
            raise ExecutionConflict("LEGACY_OPERATION_REQUIRES_RECONCILIATION")

        # REQUESTED is an idempotency/receipt record only. MINAH receives the
        # instruction before CEE admission or context establishment.
        if operation["phase"] == "REQUESTED" and operation["minah_json"] is None:
            # A profile is scoped to an explicit jurisdiction set; it must not
            # silently act as a universal agent or fall back across jurisdictions.
            requested_jurisdiction = request["jurisdiction"]
            if requested_jurisdiction not in self.minah_profile["jurisdictions"]:
                disposition = {
                    "status": "REJECTED",
                    "missing_fields": [],
                    "source_refs": [],
                    "uncertainty": "HIGH",
                    "may_authorise": False,
                    "model_version": self.minah_profile["model_ref"],
                    "deployment_profile": self.minah_profile,
                    "reason": "MINAH_PROFILE_JURISDICTION_MISMATCH",
                }
            else:
                disposition = self.minah.review(evidence)
            if isinstance(disposition, dict):
                disposition = dict(disposition)
                disposition["deployment_profile"] = self.minah_profile
            contract_error = self._minah_contract_error(disposition)
            if contract_error:
                safe_disposition = {
                    "status": "INVALID_DISPOSITION",
                    "missing_fields": [],
                    "source_refs": [],
                    "uncertainty": "HIGH",
                    "may_authorise": False,
                    "model_version": "UNTRUSTED_OUTPUT",
                    "deployment_profile": self.minah_profile,
                    "contract_error": contract_error,
                }
                self.cee.advance(
                    operation_id, "MINAH_REVIEWED", status="REJECTED",
                    detail={
                        "minah_contract_error": contract_error,
                        "minah_deployment_id": self.minah_profile["deployment_id"],
                        "minah_profile_version": self.minah_profile["profile_version"],
                        "ivm_not_invoked": True,
                    },
                    minah=safe_disposition,
                )
                result = {
                    "operation_id": operation_id,
                    "status": "REJECTED",
                    "reason": "MINAH_DISPOSITION_CONTRACT_VIOLATION",
                    "minah_disposition": safe_disposition,
                    "ivm_decision": None,
                    "funds_moved": False,
                }
                self.cee.advance(
                    operation_id, "FINALIZED", status="REJECTED",
                    outcome="REJECTED", result=result,
                    detail={"reason": result["reason"], "ivm_not_invoked": True},
                )
                return self._response_result(operation_id, result, duplicate=False)
            self.cee.advance(
                operation_id, "MINAH_REVIEWED",
                status="DEFERRED" if disposition["status"] == "NEEDS_INFORMATION" else "EVALUATED",
                detail={
                    "minah_is_advisory": True,
                    "may_authorise": False,
                    "minah_deployment_id": self.minah_profile["deployment_id"],
                    "minah_profile_version": self.minah_profile["profile_version"],
                },
                minah=disposition,
            )

        operation = self.cee.get(operation_id)
        assert operation is not None
        disposition = operation["minah_json"]

        if disposition is not None and disposition["status"] in {"REJECTED", "UNKNOWN"}:
            result_status = disposition["status"]
            result = {
                "operation_id": operation_id,
                "status": result_status,
                "reason": (
                    "MINAH_REJECTED_INSTRUCTION"
                    if result_status == "REJECTED"
                    else "MINAH_DISPOSITION_UNKNOWN"
                ),
                "minah_disposition": disposition,
                "ivm_decision": None,
                "funds_moved": False,
            }
            if operation["phase"] == "MINAH_REVIEWED":
                self.cee.advance(
                    operation_id, "FINALIZED", status=result_status,
                    outcome=result_status, result=result,
                    detail={
                        "reason": result["reason"],
                        "minah_deployment_id": self.minah_profile["deployment_id"],
                        "minah_profile_version": self.minah_profile["profile_version"],
                        "ivm_not_invoked": True,
                    },
                )
            return self._response_result(operation_id, result, duplicate=False)

        if disposition is not None and disposition["status"] == "NEEDS_INFORMATION":
            result = {
                "operation_id": operation_id,
                "status": "DEFERRED",
                "reason": "MINAH_REQUIRES_ADDITIONAL_EVIDENCE",
                "minah_disposition": disposition,
                "ivm_decision": None,
                "funds_moved": False,
            }
            if operation["phase"] == "MINAH_REVIEWED":
                self.cee.advance(
                    operation_id, "FINALIZED", status="DEFERRED",
                    outcome="DEFERRED", result=result,
                    detail={"reason": result["reason"], "ivm_not_invoked": True},
                )
            return self._response_result(operation_id, result, duplicate=False)

        # These are explicit adapter boundaries. Defaults are synthetic schema/rule
        # checks only; they are not real identity authentication or legal authority.
        if operation["phase"] == "MINAH_REVIEWED":
            # Institution-owned MINAH agents may invoke their approved identity API.
            minah_identity_check = getattr(self.minah, "verify_principal", None)
            try:
                identity = minah_identity_check(request) if callable(minah_identity_check) else self.identity_adapter.verify_principal(request)
            except Exception:
                identity = {"verified": False, "status": "UNKNOWN", "mode": "VERIFICATION_UNAVAILABLE"}
            identity_status = _verification_status(identity)
            if identity_status != "VERIFIED":
                identity_unknown = identity_status == "UNKNOWN"
                result = {
                    "operation_id": operation_id,
                    "status": identity_status,
                    "reason": "IDENTITY_VERIFICATION_UNAVAILABLE" if identity_unknown else "IDENTITY_NOT_VERIFIED",
                    "minah_disposition": disposition,
                    "identity_result": _safe_verification_result(identity),
                    "ivm_decision": None, "funds_moved": False,
                }
                terminal_status = result["status"]
                self.cee.advance(
                    operation_id, "FINALIZED", status=terminal_status, outcome=terminal_status,
                    result=result, detail={"reason": result["reason"], "ivm_not_invoked": True},
                )
                return self._response_result(operation_id, result, duplicate=False)
            identity_metadata = _safe_verification_metadata(
                identity,
                ("evidence_ref", "verification_ref", "verification_id", "identity_provider_ref",
                 "subject_ref", "principal_ref", "policy_version", "verified_at",
                 "valid_until", "assurance_level"),
            )
            self.cee.advance(
                operation_id, "IDENTITY_VERIFIED", status="EVALUATED",
                detail={
                    "identity_mode": _safe_verification_result(identity)["mode"],
                    "verified": True,
                    "identity_evidence": {
                        "principal_ref": request["actor_id"],
                        "jurisdiction": request["jurisdiction"],
                        "metadata": identity_metadata,
                        "evidence_reference_supplied": bool(
                            identity_metadata.get("evidence_ref") or identity_metadata.get("verification_ref")
                        ),
                    },
                },
            )
        operation = self.cee.get(operation_id)
        assert operation is not None
        if operation["phase"] == "IDENTITY_VERIFIED":
            # MINAH may coordinate action-specific authority checks through approved APIs.
            minah_authority_check = getattr(self.minah, "verify_action", None)
            try:
                authority = minah_authority_check(request) if callable(minah_authority_check) else self.authority_adapter.verify_action(request)
            except Exception:
                authority = {"verified": False, "status": "UNKNOWN", "mode": "VERIFICATION_UNAVAILABLE"}
            authority_status = _verification_status(authority)
            if authority_status != "VERIFIED":
                authority_unknown = authority_status == "UNKNOWN"
                result = {
                    "operation_id": operation_id,
                    "status": authority_status,
                    "reason": "AUTHORITY_VERIFICATION_UNAVAILABLE" if authority_unknown else "AUTHORITY_NOT_VERIFIED",
                    "minah_disposition": disposition,
                    "authority_result": _safe_verification_result(authority),
                    "ivm_decision": None, "funds_moved": False,
                }
                terminal_status = result["status"]
                self.cee.advance(
                    operation_id, "FINALIZED", status=terminal_status, outcome=terminal_status,
                    result=result, detail={"reason": result["reason"], "ivm_not_invoked": True},
                )
                return self._response_result(operation_id, result, duplicate=False)
            authority_metadata = _safe_verification_metadata(
                authority,
                ("evidence_ref", "verification_ref", "verification_id", "mandate_ref",
                 "authority_ref", "policy_version", "mandate_version", "scope_ref",
                 "issuer_ref", "verified_at", "valid_until"),
            )
            contract_envelope = request.get("_contract_envelope")
            self.cee.advance(
                operation_id, "AUTHORITY_VERIFIED", status="EVALUATED",
                detail={
                    "authority_mode": _safe_verification_result(authority)["mode"],
                    "verified": True,
                    "authority_evidence": {
                        "principal_ref": request["actor_id"],
                        "authority_ref": (
                            contract_envelope.get("authority_ref")
                            if isinstance(contract_envelope, dict) else None
                        ),
                        "action": request["event_type"],
                        "target_ref": request["process_id"],
                        "jurisdiction": request["jurisdiction"],
                        "rule_set": request["rule_set"],
                        "rule_version": request["rule_version"],
                        "metadata": authority_metadata,
                        "evidence_reference_supplied": bool(
                            authority_metadata.get("evidence_ref") or authority_metadata.get("verification_ref")
                            or authority_metadata.get("mandate_ref")
                        ),
                    },
                },
            )

        # Admission and context pinning are downstream of MINAH, identity, and
        # action checks. Only then does CEE admit the instruction.
        operation = self.cee.get(operation_id)
        assert operation is not None
        if operation["phase"] == "AUTHORITY_VERIFIED":
            self.cee.advance(
                operation_id, "ADMITTED",
                detail={
                    "admission": "SYNTHETIC_REQUEST_SCHEMA_ACCEPTED",
                    "minah_gate_passed": True, "identity_gate_passed": True,
                    "authority_gate_passed": True,
                },
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
                status="EVALUATED",
                detail={"minah_is_advisory": True, "may_authorise": False},
                minah=disposition,
            )
        operation = self.cee.get(operation_id)
        assert operation is not None
        disposition = operation["minah_json"]

        if disposition["status"] == "NEEDS_INFORMATION":
            result = {
                "operation_id": operation_id,
                "status": "DEFERRED",
                "reason": "MINAH_REQUIRES_ADDITIONAL_EVIDENCE",
                "minah_disposition": disposition,
                "ivm_decision": None,
                "funds_moved": False,
            }
            if operation["phase"] == "AUTHORITY_EVALUATED":
                self.cee.advance(
                    operation_id, "FINALIZED", status="DEFERRED",
                    outcome="DEFERRED", result=result,
                    detail={"reason": result["reason"], "ivm_not_invoked": True},
                )
            return self._response_result(operation_id, result, duplicate=False)

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
                detail={"ivm_preconditions_pinned": True, "process_id": process_id},
            )
        operation = self.cee.get(operation_id)
        assert operation is not None

        if operation["phase"] == "PREPARED":
            self.cee.advance(operation_id, "COMMITTING", detail={"commit_boundary": "IVM_TRANSACTION"})
        operation = self.cee.get(operation_id)
        assert operation is not None

        ivm_result: dict[str, Any]
        if operation["phase"] == "COMMITTING":
            # A retry may safely reuse an event ID only if the IVM event and
            # transition history agree with the pinned request. Corrupt or
            # contradictory store evidence must remain unresolved.
            self._assert_ivm_event_consistent(request)
            existing_event = self.ivm.event(request["event_id"])
            # A COMMITTING operation may be resumed after a crash. Revalidate
            # action authority immediately before an uncommitted IVM transition;
            # if the IVM event already exists, its durable outcome is reconciled
            # instead of re-authorising or repeating the protected transition.
            if existing_event is None:
                minah_authority_check = getattr(self.minah, "verify_action", None)
                try:
                    authority = (
                        minah_authority_check(request)
                        if callable(minah_authority_check)
                        else self.authority_adapter.verify_action(request)
                    )
                except Exception:
                    authority = {"verified": False, "status": "UNKNOWN", "mode": "REVALIDATION_UNAVAILABLE"}
                revalidation_metadata = _safe_verification_metadata(
                    authority if isinstance(authority, dict) else {},
                    ("evidence_ref", "verification_ref", "verification_id", "mandate_ref",
                     "authority_ref", "policy_version", "mandate_version", "scope_ref",
                     "issuer_ref", "verified_at", "valid_until"),
                )
                revalidation_status = _verification_status(authority)
                revalidation_verified = revalidation_status == "VERIFIED"
                revalidation_unknown = revalidation_status == "UNKNOWN"
                self.cee.record_observation(
                    operation_id, "AUTHORITY_REVALIDATION",
                    "VERIFIED" if revalidation_verified else "UNKNOWN" if revalidation_unknown else "REJECTED",
                    {
                        "principal_ref": request["actor_id"],
                        "action": request["event_type"],
                        "target_ref": request["process_id"],
                        "jurisdiction": request["jurisdiction"],
                        "rule_set": request["rule_set"],
                        "rule_version": request["rule_version"],
                        "mode": _safe_verification_result(authority)["mode"],
                        "metadata": revalidation_metadata,
                        "evidence_reference_supplied": bool(
                            revalidation_metadata.get("evidence_ref") or revalidation_metadata.get("verification_ref")
                            or revalidation_metadata.get("mandate_ref")
                        ),
                    },
                )
                if not revalidation_verified:
                    authority_unknown = revalidation_status == "UNKNOWN"
                    terminal_status = revalidation_status
                    result = {
                        "operation_id": operation_id,
                        "status": terminal_status,
                        "reason": (
                            "AUTHORITY_REVALIDATION_UNAVAILABLE"
                            if authority_unknown
                            else "AUTHORITY_REVOKED_OR_NOT_VALID_AT_EXECUTION"
                        ),
                        "minah_disposition": disposition,
                        "authority_result": _safe_verification_result(authority),
                        "ivm_decision": None,
                        "funds_moved": False,
                    }
                    self.cee.advance(
                        operation_id, "FINALIZED", status=terminal_status,
                        outcome=terminal_status, result=result,
                        detail={
                            "reason": result["reason"],
                            "authority_revalidation": "FAILED_CLOSED",
                            "ivm_not_invoked": True,
                        },
                    )
                    return self._response_result(operation_id, result, duplicate=False)
            else:
                self.cee.record_observation(
                    operation_id, "AUTHORITY_REVALIDATION", "NOT_REQUIRED",
                    {
                        "event_id": request["event_id"],
                        "ivm_event_outcome": existing_event.get("outcome"),
                        "reason": "IVM_EVENT_ALREADY_DURABLE; RECONCILE_EXISTING_OUTCOME",
                    },
                )
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
                    "reason": decision.reason or "IVM_REJECTED",
                    "minah_disposition": disposition,
                    "ivm_decision": ivm_result,
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
                detail={"ivm_commit_observed": True, "history_id": decision.history_id},
            )
        operation = self.cee.get(operation_id)
        assert operation is not None

        if operation["phase"] == "COMMITTED":
            ivm_result = operation["ivm_json"]
            result = {
                "operation_id": operation_id,
                "status": "COMMITTED",
                "reason": None,
                "minah_disposition": disposition,
                "ivm_decision": ivm_result,
                "funds_moved": False,
                "commit_scope": "LOCAL_IVM_SQLITE_ONLY_NOT_DISTRIBUTED_FINALITY",
            }
            self.cee.advance(
                operation_id, "FINALIZED", status="FINALIZED", outcome="COMMITTED",
                result=result,
                detail={"result_status": "COMMITTED", "distributed_finality": False},
            )
            return self._response_result(operation_id, result, duplicate=bool(ivm_result.get("duplicate")))

        operation = self.cee.get(operation_id)
        assert operation is not None
        if operation["phase"] == "FINALIZED":
            result = operation["result_json"]
            return self._response_result(operation_id, result, duplicate=True)
        raise LifecycleViolation(f"UNHANDLED_RESUMABLE_PHASE:{operation['phase']}")

    def recover(
        self, operation_id: str, expected_request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resume an existing operation only from durable, recoverable lifecycle state.

        Recovery never creates a new operation or accepts replacement request content.
        A persisted MINAH disposition is required; after that point the canonical
        pipeline resumes in phase order, with IVM's stable event ID preventing a
        committed local transition from being applied twice.
        """
        self._assert_store_integrity()
        operation = self.cee.get(operation_id)
        if operation is None:
            return {
                "operation_id": operation_id,
                "status": "UNKNOWN",
                "reason": "OPERATION_NOT_FOUND",
                "recovery_required": True,
            }

        self._assert_cross_store_consistent(operation)

        pinned_request = operation["request_json"]
        request_without_pins = {
            key: value for key, value in pinned_request.items()
            if not key.startswith("_minah_")
        }
        if expected_request is not None:
            if _canonical(request_without_pins) != _canonical(expected_request):
                raise ExecutionConflict("RECOVERY_REQUEST_DIFFERS_FROM_DURABLE_INGRESS_REQUEST")

        # A final CEE record is immutable evidence. It can be returned for ingress
        # reconciliation even if the currently configured profile has since changed;
        # no execution is resumed in this branch.
        if operation["phase"] == "FINALIZED":
            result = operation["result_json"]
            if isinstance(result, dict):
                return self._response_result(operation_id, result, duplicate=True)
            return {
                "operation_id": operation_id,
                "status": "UNKNOWN",
                "reason": "FINALIZED_CEE_RESULT_MISSING",
                "recovery_required": True,
            }

        pinned_profile = pinned_request.get("_minah_deployment_profile")
        if _canonical(pinned_profile) != _canonical(self.minah_profile):
            raise ExecutionConflict("RECOVERY_PROFILE_DIFFERS_FROM_PINNED_PROFILE")
        if self.require_profile_approval:
            if pinned_request.get("_minah_profile_approval") != self.minah_profile_approval:
                raise ExecutionConflict("RECOVERY_APPROVAL_DIFFERS_FROM_PINNED_APPROVAL")
            pinned_approval_metadata = pinned_request.get("_minah_profile_approval_verification")
            if not isinstance(pinned_approval_metadata, dict) or pinned_approval_metadata.get("status") != "APPROVED":
                raise ExecutionConflict("PINNED_PROFILE_APPROVAL_EVIDENCE_MISSING")
        if operation["phase"] == "REQUESTED" and operation["minah_json"] is None:
            raise ExecutionConflict(
                "MINAH_DISPOSITION_NOT_DURABLE_REQUIRES_MANUAL_RECONCILIATION"
            )
        if (
            operation["phase"] in {"ADMITTED", "CONTEXT_ESTABLISHED"}
            and operation["minah_json"] is None
        ):
            raise ExecutionConflict("LEGACY_OPERATION_REQUIRES_RECONCILIATION")

        recoverable_phases = {
            "MINAH_REVIEWED", "IDENTITY_VERIFIED", "AUTHORITY_VERIFIED",
            "ADMITTED", "CONTEXT_ESTABLISHED", "AUTHORITY_EVALUATED",
            "EXECUTING", "PREPARED", "COMMITTING", "COMMITTED",
        }
        if operation["phase"] not in recoverable_phases or operation["minah_json"] is None:
            return {
                "operation_id": operation_id,
                "status": "UNKNOWN",
                "reason": "RECOVERY_REQUIRES_MANUAL_RECONCILIATION",
                "cee_phase": operation["phase"],
                "recovery_required": True,
            }

        # execute() reconstructs the pinned request and revalidates the current
        # profile approval before any protected transition that is not already durable.
        return self.execute(request_without_pins)

    def inspect(self, operation_id: str) -> dict[str, Any] | None:
        """Read a lifecycle snapshot without mutating or resuming the operation."""
        operation = self.cee.get(operation_id)
        if operation is None:
            return None
        events = self.cee.events(operation_id)
        request = operation["request_json"]
        request_without_pins = {
            key: value for key, value in request.items()
            if not key.startswith("_minah_")
        }
        request_fingerprint = _digest(request_without_pins)
        ivm_event = self.ivm.event(request["event_id"])
        if operation["phase"] == "FINALIZED" and isinstance(operation["result_json"], dict):
            result = operation["result_json"]
            response = self._response_result(operation_id, result, duplicate=False)
            return {
                "operation_id": operation_id,
                "phase": operation["phase"],
                "request_fingerprint": request_fingerprint,
                "resumable": False,
                "result": result,
                "cee_events": events,
                "cee_observations": self.cee.observations(operation_id),
                "component_trace": response["component_trace"],
                "ivm_event": ivm_event,
            }
        return {
            "operation_id": operation_id,
            "phase": operation["phase"],
            "request_fingerprint": request_fingerprint,
            "resumable": True,
            "minah_disposition": operation["minah_json"],
            "ivm_decision": operation["ivm_json"],
            "ivm_event": ivm_event,
            "cee_events": events,
            "cee_observations": self.cee.observations(operation_id),
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
        ivm = result.get("ivm_decision")

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
            if event["phase"] == "IDENTITY_VERIFIED":
                append("IDENTITY", "VERIFIED", {"mode": event["detail"].get("identity_mode"), "synthetic": True})
            if event["phase"] == "AUTHORITY_VERIFIED":
                append("AUTHORITY", "VERIFIED", {"mode": event["detail"].get("authority_mode"), "synthetic": True})
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
            if event["phase"] in {"COMMITTED", "FINALIZED"} and not ivm_inserted and isinstance(ivm, dict):
                append("IVM", str(ivm.get("status", "UNKNOWN")), {
                    "event_id": ivm.get("event_id"),
                    "from_state": ivm.get("from_state"),
                    "to_state": ivm.get("to_state"),
                    "history_id": ivm.get("history_id"),
                    "reason": ivm.get("reason"),
                    "duplicate": ivm.get("duplicate", False),
                })
                ivm_inserted = True
            append("CEE", event["phase"], {
                "phase": event["phase"],
                "event_status": event["status"],
                **event["detail"],
            })
        return {
            **result,
            "duplicate": duplicate,
            "cee_events": events,
            "cee_observations": self.cee.observations(operation_id),
            "component_trace": trace,
        }

    @staticmethod
    def _minah_contract_error(disposition: Any) -> str | None:
        if not isinstance(disposition, dict):
            return "OUTPUT_NOT_OBJECT"
        required = {"status", "missing_fields", "source_refs", "uncertainty", "may_authorise", "model_version"}
        if required - set(disposition):
            return "MISSING_REQUIRED_FIELDS"
        if disposition.get("status") not in {"NEEDS_INFORMATION", "ADVISORY_ONLY", "REJECTED", "UNKNOWN"}:
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
        allowed_extensions = {
            "_contract_envelope", "_institutional_runtime_binding",
            "_minah_deployment_profile",
        }
        unknown = sorted(set(request) - required - allowed_extensions)
        if unknown:
            raise ValueError("UNKNOWN_FIELDS:" + ",".join(unknown))
        if "_minah_deployment_profile" in request:
            profile = request["_minah_deployment_profile"]
            profile_fields = {
                "profile_version", "deployment_id", "owner_scope", "owner_ref",
                "use_case", "jurisdictions", "policy_pack_version",
                "source_pack_version", "model_ref", "deployment_mode",
                "may_authorise", "mandate_ref", "mandate_version",
                "interface_version", "authority_profile_version",
            }
            if (not isinstance(profile, dict) or not profile
                    or set(profile) - profile_fields
                    or any(not isinstance(key, str) for key in profile)):
                raise ValueError("INVALID_MINAH_DEPLOYMENT_PROFILE_SNAPSHOT")
            for field in (
                "profile_version", "deployment_id", "owner_ref",
                "policy_pack_version", "source_pack_version", "model_ref",
            ):
                if not isinstance(profile.get(field), str) or not profile[field].strip():
                    raise ValueError("INVALID_MINAH_DEPLOYMENT_PROFILE_SNAPSHOT:" + field)
            jurisdictions = profile.get("jurisdictions")
            if (not isinstance(jurisdictions, list) or not jurisdictions
                    or any(not isinstance(item, str) or not item.strip()
                           for item in jurisdictions)
                    or len(set(jurisdictions)) != len(jurisdictions)
                    or request["jurisdiction"] not in jurisdictions
                    or profile.get("may_authorise") is not False):
                raise ValueError("INVALID_MINAH_DEPLOYMENT_PROFILE_SNAPSHOT:scope")
        if "_institutional_runtime_binding" in request:
            pins = request["_institutional_runtime_binding"]
            pin_fields = {
                "tenant_id", "institution_id", "institution_version", "binding_id",
                "binding_version", "jurisdiction_ref", "jurisdiction_package_id",
                "jurisdiction_package_version", "minah_deployment_id",
                "identity_provider_id", "identity_provider_version",
                "minah_domain_version", "minah_runtime_version",
                "policy_pack_version", "source_pack_version",
                "authority_profile_version",
            }
            if (not isinstance(pins, dict) or set(pins) != pin_fields
                    or any(not isinstance(value, str) or not value.strip()
                           for value in pins.values())):
                raise ValueError("INVALID_INSTITUTIONAL_RUNTIME_BINDING_PINS")
            if pins["jurisdiction_ref"] != request["jurisdiction"]:
                raise ValueError("INSTITUTIONAL_RUNTIME_JURISDICTION_MISMATCH")
            if (pins["policy_pack_version"]
                    != request["rule_set"] + ":" + request["rule_version"]):
                raise ValueError("INSTITUTIONAL_RUNTIME_POLICY_VERSION_MISMATCH")
        if "_contract_envelope" in request:
            # Preserve the complete validated client envelope inside the durable
            # operation fingerprint so retries cannot alter omitted contract fields.
            envelope = verify_instruction_payload_digest(request["_contract_envelope"])
            bindings = (
                ("operation_id", request["operation_id"], envelope["operation_id"]),
                ("target_ref", request["process_id"], envelope["target_ref"]),
                ("principal_ref", request["actor_id"], envelope["principal_ref"]),
                ("action", request["event_type"], envelope["action"]),
                ("jurisdiction", request["jurisdiction"], envelope["jurisdiction"]),
                (
                    "rule_pack_version",
                    f"{request['rule_set']}:{request['rule_version']}",
                    envelope["rule_pack_version"],
                ),
            )
            for field, internal_value, envelope_value in bindings:
                if internal_value != envelope_value:
                    raise ValueError("CONTRACT_BINDING_MISMATCH:" + field)
            if not isinstance(envelope.get("evidence"), dict) or request["evidence"] != envelope["evidence"]:
                raise ValueError("CONTRACT_BINDING_MISMATCH:evidence")
            expected_event_id = "EV-" + hashlib.sha256(
                envelope["idempotency_key"].encode("utf-8")
            ).hexdigest()[:24]
            if request["event_id"] != expected_event_id:
                raise ValueError("CONTRACT_BINDING_MISMATCH:idempotency_key")
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
