# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Fail-closed verification contract for governed MINAH profile approvals.

This module does not implement cryptographic signature verification or a
revocation registry. Deployments must inject trusted implementations for both.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable


APPROVAL_VERSION = "kin-sys-minah-approval-0.1"
APPROVAL_FIELDS = {
    "approval_version",
    "approval_id",
    "signer_ref",
    "algorithm",
    "profile_digest",
    "approved_at",
    "expires_at",
    "signature",
}


class MinahApprovalViolation(ValueError):
    """Raised when an approval is absent, invalid, expired, or revoked."""


def canonical_profile_bytes(profile: dict[str, Any]) -> bytes:
    """Return deterministic UTF-8 JSON bytes for a profile digest/signature."""
    if not isinstance(profile, dict):
        raise MinahApprovalViolation("PROFILE_MUST_BE_OBJECT")
    try:
        return json.dumps(
            profile, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MinahApprovalViolation("PROFILE_NOT_CANONICALISABLE") from exc


def profile_digest(profile: dict[str, Any]) -> str:
    """Compute the SHA-256 digest of the exact canonical profile bytes."""
    return "sha256:" + hashlib.sha256(canonical_profile_bytes(profile)).hexdigest()


def _utc_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise MinahApprovalViolation("INVALID_" + field.upper())
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MinahApprovalViolation("INVALID_" + field.upper()) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MinahApprovalViolation("TIMESTAMP_MUST_INCLUDE_TIMEZONE:" + field.upper())
    return parsed.astimezone(timezone.utc)


def verify_minah_profile_approval(
    profile: dict[str, Any],
    approval: Any,
    *,
    signature_verifier: Callable[[str, str, bytes, str], bool],
    revocation_checker: Callable[[str], bool],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify an approval envelope; fail closed on every missing/uncertain check.

    signature_verifier(signer_ref, algorithm, signed_payload, signature) must
    validate the signature against a trusted key registry and return True only
    for a cryptographically valid signature. revocation_checker(approval_id)
    must return True only when the approval is known to be revoked. Exceptions
    from either callback fail closed. The signature covers every approval field
    except 'signature', including the profile digest and expiry.
    """
    if not isinstance(approval, dict):
        raise MinahApprovalViolation("APPROVAL_MUST_BE_OBJECT")
    missing = sorted(APPROVAL_FIELDS - set(approval))
    if missing:
        raise MinahApprovalViolation("MISSING_APPROVAL_FIELDS:" + ",".join(missing))
    if approval.get("approval_version") != APPROVAL_VERSION:
        raise MinahApprovalViolation("UNSUPPORTED_APPROVAL_VERSION")

    for field in ("approval_id", "signer_ref", "algorithm", "signature"):
        value = approval.get(field)
        if not isinstance(value, str) or not value.strip():
            raise MinahApprovalViolation("INVALID_" + field.upper())

    actual_digest = profile_digest(profile)
    if approval.get("profile_digest") != actual_digest:
        raise MinahApprovalViolation("PROFILE_DIGEST_MISMATCH")

    approved_at = _utc_timestamp(approval.get("approved_at"), "approved_at")
    expires_at = _utc_timestamp(approval.get("expires_at"), "expires_at")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise MinahApprovalViolation("CURRENT_TIME_MUST_INCLUDE_TIMEZONE")
    current = current.astimezone(timezone.utc)
    if expires_at <= approved_at:
        raise MinahApprovalViolation("APPROVAL_EXPIRY_NOT_AFTER_APPROVAL")
    if approved_at > current:
        raise MinahApprovalViolation("APPROVAL_NOT_YET_VALID")
    if expires_at <= current:
        raise MinahApprovalViolation("APPROVAL_EXPIRED")

    signed_fields = {key: value for key, value in approval.items() if key != "signature"}
    signed_payload = json.dumps(
        signed_fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    try:
        signature_valid = signature_verifier(
            approval["signer_ref"], approval["algorithm"],
            signed_payload, approval["signature"],
        )
    except Exception as exc:
        raise MinahApprovalViolation("SIGNATURE_VERIFICATION_UNAVAILABLE") from exc
    if signature_valid is not True:
        raise MinahApprovalViolation("SIGNATURE_INVALID_OR_UNTRUSTED")

    try:
        revoked = revocation_checker(approval["approval_id"])
    except Exception as exc:
        raise MinahApprovalViolation("REVOCATION_CHECK_UNAVAILABLE") from exc
    if revoked is not False:
        raise MinahApprovalViolation("APPROVAL_REVOKED_OR_REVOCATION_UNKNOWN")

    return {
        "verified": True,
        "approval_id": approval["approval_id"],
        "signer_ref": approval["signer_ref"],
        "profile_digest": actual_digest,
        "approved_at": approved_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "signature_verified": True,
        "revocation_checked": True,
        "status": "APPROVED",
    }
