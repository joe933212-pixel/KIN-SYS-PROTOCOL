# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Read-only, allow-listed recovery evidence bundles for the local SQLite reference pipeline.

These bundles are diagnostic records, not authenticated or tamper-proof evidence.
They deliberately omit raw requests, identity documents, credentials and database dumps.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = "kin-sys-recovery-evidence-0.1"

_EVENT_DETAIL_FIELDS = {
    "request_digest", "reason", "ivm_not_invoked", "minah_is_advisory",
    "may_authorise", "minah_deployment_id", "minah_profile_version",
    "identity_mode", "authority_mode", "verified", "admission",
    "minah_gate_passed", "identity_gate_passed", "authority_gate_passed",
    "jurisdiction", "rule_set", "rule_version", "evidence_digest",
    "executor", "ivm_preconditions_pinned", "process_id", "commit_boundary",
    "authority_revalidation",
}
_OBSERVATION_DETAIL_FIELDS = {
    "event_id", "ivm_event_outcome", "reason", "principal_ref", "action",
    "target_ref", "jurisdiction", "rule_set", "rule_version", "mode",
    "evidence_reference_supplied", "metadata",
}
_METADATA_FIELDS = {
    "evidence_ref", "verification_ref", "verification_id", "identity_provider_ref",
    "subject_ref", "principal_ref", "mandate_ref", "authority_ref", "policy_version",
    "mandate_version", "scope_ref", "issuer_ref", "verified_at", "valid_until",
    "assurance_level",
}
_REQUEST_REFERENCE_FIELDS = {
    "jurisdiction", "rule_set", "rule_version", "event_id", "process_id",
}
_IVM_EVENT_FIELDS = {
    "event_id", "process_id", "request_digest", "actor_id", "event_type",
    "outcome", "reason",
}
_PROCESS_FIELDS = {
    "process_id", "state", "state_version", "authority_id", "authority_active",
    "rule_set", "rule_version", "eligible",
}
_HISTORY_FIELDS = {
    "history_id", "event_id", "process_id", "from_state", "to_state",
    "authority_id", "rule_set", "rule_version",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pick(value: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key in sorted(allowed):
        item = value.get(key)
        if isinstance(item, (str, bool, int)) or item is None:
            result[key] = item
        elif key == "metadata" and isinstance(item, dict):
            result[key] = _pick(item, _METADATA_FIELDS)
    return result


def _integrity(connection: Any) -> dict[str, Any]:
    try:
        rows = connection.execute("PRAGMA integrity_check").fetchall()
        ok = len(rows) == 1 and rows[0][0] == "ok"
        return {"status": "OK" if ok else "FAILED", "result_count": len(rows)}
    except Exception:
        return {"status": "UNAVAILABLE", "result_count": None}


def build_recovery_evidence_bundle(pipeline: Any, operation_id: str) -> dict[str, Any]:
    """Capture a bounded diagnostic snapshot without invoking recovery or writing stores."""
    captured_at = datetime.now(timezone.utc).isoformat()
    base: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "operation_id": operation_id,
        "captured_at": captured_at,
        "capture_status": "INCOMPLETE",
        "stores": {
            "cee": {"identifier": "pipeline.cee.db", "integrity": _integrity(pipeline.cee.db)},
            "ivm": {"identifier": "pipeline.ivm.db", "integrity": _integrity(pipeline.ivm.db)},
        },
        "operation": None,
        "cee": {"events": [], "observations": []},
        "ivm": {"event": None, "process": None, "transition_history": []},
        "consistency": {"status": "NOT_EVALUATED", "conflict_code": None},
        "limitations": [
            "Local SQLite reference-store diagnostic only.",
            "No database file digest or backup provenance is available from this connection interface.",
            "SQLite integrity_check is structural validation, not tamper detection or authenticity proof.",
            "No trusted timestamp, signature, immutable external storage or production audit guarantee.",
        ],
    }
    try:
        operation = pipeline.cee.get(operation_id)
        if operation is None:
            base["consistency"] = {"status": "OPERATION_NOT_FOUND", "conflict_code": "OPERATION_NOT_FOUND"}
            return _finalize(base)
        request = operation.get("request_json") or {}
        base["operation"] = {
            "phase": operation.get("phase"),
            "outcome": operation.get("outcome"),
            "request_digest": operation.get("request_digest"),
            "request_references": _pick(request, _REQUEST_REFERENCE_FIELDS),
            "minah_disposition": _pick(
                operation.get("minah_json"),
                {"status", "uncertainty", "may_authorise", "model_version", "reason", "contract_error"},
            ),
            "ivm_decision": _pick(
                operation.get("ivm_json"),
                {"status", "event_id", "process_id", "from_state", "to_state", "history_id", "reason", "duplicate"},
            ),
            "result_summary": _pick(
                operation.get("result_json"),
                {"status", "reason", "funds_moved", "operation_id"},
            ),
        }
        base["cee"]["events"] = [
            {"phase": event.get("phase"), "status": event.get("status"),
             "detail": _pick(event.get("detail"), _EVENT_DETAIL_FIELDS)}
            for event in pipeline.cee.events(operation_id)
        ]
        base["cee"]["observations"] = [
            {"kind": item.get("kind"), "status": item.get("status"),
             "created_at": item.get("created_at"),
             "detail": _pick(item.get("detail"), _OBSERVATION_DETAIL_FIELDS)}
            for item in pipeline.cee.observations(operation_id)
        ]
        refs = base["operation"]["request_references"]
        event_id, process_id = refs.get("event_id"), refs.get("process_id")
        if isinstance(event_id, str):
            base["ivm"]["event"] = _pick(pipeline.ivm.event(event_id), _IVM_EVENT_FIELDS) or None
        if isinstance(process_id, str):
            try:
                base["ivm"]["process"] = _pick(pipeline.ivm.process(process_id), _PROCESS_FIELDS)
            except Exception:
                base["ivm"]["process"] = None
            try:
                base["ivm"]["transition_history"] = [
                    _pick(item, _HISTORY_FIELDS) for item in pipeline.ivm.history(process_id)
                    if item.get("event_id") == event_id
                ]
            except Exception:
                base["ivm"]["transition_history"] = []
        try:
            pipeline._assert_cross_store_consistent(operation)
            base["consistency"] = {"status": "CONSISTENT", "conflict_code": None}
        except Exception as exc:
            code = str(exc).split(":", 1)[0][:160]
            base["consistency"] = {"status": "CONFLICT", "conflict_code": code}
        integrity = [base["stores"][key]["integrity"]["status"] for key in ("cee", "ivm")]
        if "UNAVAILABLE" in integrity:
            base["capture_status"] = "STORE_UNAVAILABLE"
        elif "FAILED" in integrity:
            base["capture_status"] = "STORE_INTEGRITY_FAILED"
        else:
            base["capture_status"] = "COMPLETE"
    except Exception:
        base["capture_status"] = "STORE_UNAVAILABLE"
    return _finalize(base)


def _finalize(bundle: dict[str, Any]) -> dict[str, Any]:
    """Digest the canonical evidence payload without the variable capture timestamp."""
    digest_payload = {key: value for key, value in bundle.items() if key != "captured_at"}
    bundle["bundle_digest"] = _sha256(_canonical(digest_payload))
    return bundle
