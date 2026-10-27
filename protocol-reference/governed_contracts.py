# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Provider-neutral KIN-SYS instruction and decision contracts (draft v0.1).

These validators check structure and semantic consistency only. They do not
authenticate callers, prove evidence truth, validate legal authority, or provide
cryptographic/wire security. Enforcement belongs to the governed execution path.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

CONTRACT_VERSION = "kin-sys-governed-contract-0.1"
INSTRUCTION_REQUIRED = {
    "contract_version", "request_id", "operation_id", "idempotency_key",
    "principal_ref", "authority_ref", "action", "target_ref", "purpose",
    "jurisdiction", "submitted_at", "payload_digest", "rule_pack_version",
}
DECISION_REQUIRED = {
    "contract_version", "request_id", "operation_id", "protocol_decision",
    "reason_code", "user_message", "rule_pack_version", "minah_disposition_ref",
    "cee_execution_ref", "external_effect_status", "evidence_refs",
}
PROTOCOL_DECISIONS = {"PASSED", "REJECTED", "DEFERRED", "UNKNOWN"}
EFFECT_STATUSES = {"NOT_ATTEMPTED", "PENDING", "CONFIRMED", "UNKNOWN", "NOT_APPLICABLE"}


class GovernedContractViolation(ValueError):
    """Raised when an instruction or decision violates the shared contract."""


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_nonempty_strings(value: dict[str, Any], fields: set[str]) -> None:
    for field in fields:
        if not _nonempty_string(value.get(field)):
            raise GovernedContractViolation(f"MISSING_OR_INVALID_{field.upper()}")


def _validate_datetime(value: Any, field: str) -> None:
    if not _nonempty_string(value):
        raise GovernedContractViolation(f"MISSING_OR_INVALID_{field.upper()}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise GovernedContractViolation(f"INVALID_{field.upper()}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GovernedContractViolation(f"{field.upper()}_MUST_INCLUDE_TIMEZONE")


def validate_instruction_envelope(instruction: Any) -> dict[str, Any]:
    """Validate and return a detached, versioned instruction envelope."""
    if not isinstance(instruction, dict):
        raise GovernedContractViolation("INSTRUCTION_MUST_BE_OBJECT")
    missing = INSTRUCTION_REQUIRED - instruction.keys()
    if missing:
        raise GovernedContractViolation("MISSING_FIELDS:" + ",".join(sorted(missing)))
    if instruction["contract_version"] != CONTRACT_VERSION:
        raise GovernedContractViolation("CONTRACT_VERSION_MISMATCH")
    _require_nonempty_strings(instruction, INSTRUCTION_REQUIRED - {"contract_version", "submitted_at"})
    _validate_datetime(instruction["submitted_at"], "submitted_at")
    if len(instruction["idempotency_key"]) < 8:
        raise GovernedContractViolation("IDEMPOTENCY_KEY_TOO_SHORT")
    digest = instruction["payload_digest"]
    if not digest.startswith("sha256:"):
        raise GovernedContractViolation("PAYLOAD_DIGEST_MUST_DECLARE_ALGORITHM")
    if len(digest) != len("sha256:") + 64:
        raise GovernedContractViolation("INVALID_PAYLOAD_DIGEST_LENGTH")
    for field in ("evidence_refs", "constraints"):
        if field in instruction and not isinstance(instruction[field], (list, dict)):
            raise GovernedContractViolation(f"INVALID_{field.upper()}")
    return deepcopy(instruction)


def validate_decision_envelope(decision: Any) -> dict[str, Any]:
    """Validate decision semantics without treating a pass as a completed effect."""
    if not isinstance(decision, dict):
        raise GovernedContractViolation("DECISION_MUST_BE_OBJECT")
    missing = DECISION_REQUIRED - decision.keys()
    if missing:
        raise GovernedContractViolation("MISSING_FIELDS:" + ",".join(sorted(missing)))
    if decision["contract_version"] != CONTRACT_VERSION:
        raise GovernedContractViolation("CONTRACT_VERSION_MISMATCH")
    _require_nonempty_strings(decision, DECISION_REQUIRED - {"contract_version", "evidence_refs", "external_effect_status"})
    if decision["protocol_decision"] not in PROTOCOL_DECISIONS:
        raise GovernedContractViolation("INVALID_PROTOCOL_DECISION")
    if decision["external_effect_status"] not in EFFECT_STATUSES:
        raise GovernedContractViolation("INVALID_EXTERNAL_EFFECT_STATUS")
    if not isinstance(decision["evidence_refs"], list) or any(
        not _nonempty_string(ref) for ref in decision["evidence_refs"]
    ):
        raise GovernedContractViolation("INVALID_EVIDENCE_REFS")
    if decision["protocol_decision"] in {"REJECTED", "DEFERRED", "UNKNOWN"}:
        if decision["external_effect_status"] == "CONFIRMED":
            raise GovernedContractViolation("NON_PASSED_DECISION_CANNOT_CLAIM_CONFIRMED_EFFECT")
    if decision["external_effect_status"] == "CONFIRMED":
        if decision["protocol_decision"] != "PASSED":
            raise GovernedContractViolation("NON_PASSED_DECISION_CANNOT_CLAIM_CONFIRMED_EFFECT")
        if not _nonempty_string(decision.get("effect_confirmation_ref")):
            raise GovernedContractViolation("CONFIRMED_EFFECT_REQUIRES_CONFIRMATION_REF")
    return deepcopy(decision)


def assert_instruction_binding(instruction: dict[str, Any], decision: dict[str, Any]) -> None:
    """Reject a decision envelope not bound to the submitted instruction."""
    checked_instruction = validate_instruction_envelope(instruction)
    checked_decision = validate_decision_envelope(decision)
    for field in ("request_id", "operation_id", "rule_pack_version"):
        if checked_instruction[field] != checked_decision[field]:
            raise GovernedContractViolation(f"DECISION_{field.upper()}_MISMATCH")
