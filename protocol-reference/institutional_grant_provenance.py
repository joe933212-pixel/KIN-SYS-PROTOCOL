# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Issuer-signed authority-grant provenance and receiver-side acceptance.

The issuer signature proves integrity and origin under a receiver-configured
trust key. It does not prove that the issuer is legally competent or that the
approval evidence is legally sufficient. A receiver must separately approve
acceptance through its own authenticated authority policy.
"""
from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, Callable

from institutional_authority import AuthorityResolutionError, _verify_ed25519

GRANT_FIELDS = {
    "grant_id", "tenant_id", "status", "actor_ref", "institution_id",
    "authority_ref", "roles", "resource_types", "resource_ids",
    "valid_from", "valid_until", "version", "issuer_institution_id",
    "approval_ref",
}
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
ENVELOPE_FIELDS = {"claims", "signature"}


class GrantProvenanceError(ValueError):
    """A grant's issuer provenance or receiver acceptance is invalid."""


def canonical_grant_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise GrantProvenanceError("GRANT_NOT_CANONICALISABLE") from exc


def _validate_grant(grant: Any) -> None:
    if not isinstance(grant, dict) or set(grant) != GRANT_FIELDS:
        raise GrantProvenanceError("AUTHORITY_GRANT_FIELDS_INVALID")
    for field in ("grant_id", "tenant_id", "actor_ref", "institution_id",
                  "authority_ref", "version", "issuer_institution_id", "approval_ref"):
        if not isinstance(grant[field], str) or not IDENTIFIER.fullmatch(grant[field]):
            raise GrantProvenanceError("INVALID_GRANT_" + field.upper())
    if grant["status"] not in {"PENDING", "ACTIVE", "SUSPENDED", "REVOKED"}:
        raise GrantProvenanceError("INVALID_GRANT_STATUS")
    if type(grant["valid_from"]) is not int or type(grant["valid_until"]) is not int:
        raise GrantProvenanceError("INVALID_GRANT_VALIDITY")
    if grant["valid_until"] <= grant["valid_from"]:
        raise GrantProvenanceError("INVALID_GRANT_VALIDITY")
    for field in ("roles", "resource_types", "resource_ids"):
        values = grant[field]
        if (not isinstance(values, list) or not values
                or any(not isinstance(item, str) or not item.strip() for item in values)):
            raise GrantProvenanceError("INVALID_GRANT_SCOPE")


def sign_authority_grant(grant: dict[str, Any], *, key_id: str,
                         private_key: Any) -> dict[str, Any]:
    """Create an issuer-signed immutable grant envelope using Ed25519.

    The issuer must sign a PENDING grant. Only the receiving institution may
    promote it to ACTIVE after local acceptance; status changes require a new
    issuer-signed version and a fresh receiver acceptance.
    """
    _validate_grant(grant)
    if grant["status"] != "PENDING":
        raise GrantProvenanceError("ISSUED_GRANT_MUST_BE_PENDING")
    if not isinstance(key_id, str) or not IDENTIFIER.fullmatch(key_id):
        raise GrantProvenanceError("INVALID_SIGNING_KEY_ID")
    claims = {"grant": grant, "key_id": key_id,
              "issuer_institution_id": grant["issuer_institution_id"]}
    try:
        signature = private_key.sign(canonical_grant_bytes(claims))
    except Exception as exc:
        raise GrantProvenanceError("GRANT_SIGNING_FAILED") from exc
    encoded = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return {"claims": claims, "signature": encoded}


def verify_authority_grant(envelope: dict[str, Any], *, tenant_id: str,
                           trust_key_loader: Callable[[str, str], dict[str, Any] | None],
                           public_key_loader: Callable[[str], bytes],
                           clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """Verify issuer signature and receiver trust before exposing grant claims."""
    if not isinstance(envelope, dict) or set(envelope) != ENVELOPE_FIELDS:
        raise GrantProvenanceError("INVALID_GRANT_ENVELOPE")
    claims, signature = envelope["claims"], envelope["signature"]
    if (not isinstance(claims, dict)
            or set(claims) != {"grant", "key_id", "issuer_institution_id"}
            or not isinstance(signature, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{86}", signature)):
        raise GrantProvenanceError("INVALID_GRANT_ENVELOPE")
    grant = claims["grant"]
    _validate_grant(grant)
    if grant["status"] != "PENDING":
        raise GrantProvenanceError("ISSUED_GRANT_MUST_BE_PENDING")
    if grant["tenant_id"] != tenant_id:
        raise GrantProvenanceError("GRANT_TENANT_MISMATCH")
    issuer, key_id = claims["issuer_institution_id"], claims["key_id"]
    if issuer != grant["issuer_institution_id"]:
        raise GrantProvenanceError("GRANT_ISSUER_MISMATCH")
    if not isinstance(key_id, str) or not IDENTIFIER.fullmatch(key_id):
        raise GrantProvenanceError("INVALID_SIGNING_KEY_ID")
    now = int(clock())
    if not grant["valid_from"] <= now < grant["valid_until"]:
        raise GrantProvenanceError("GRANT_OUTSIDE_VALIDITY")
    record = trust_key_loader(tenant_id, key_id)
    if not isinstance(record, dict):
        raise GrantProvenanceError("UNTRUSTED_GRANT_ISSUER_KEY")
    if (record.get("tenant_id") != tenant_id or record.get("key_id") != key_id
            or record.get("issuer_institution_id") != issuer):
        raise GrantProvenanceError("GRANT_TRUST_KEY_ISSUER_MISMATCH")
    if record.get("algorithm") != "Ed25519" or record.get("status") != "ACTIVE":
        raise GrantProvenanceError("GRANT_TRUST_KEY_NOT_ACTIVE")
    for start_field, end_field, error in (
        ("issuer_valid_from", "issuer_valid_until", "GRANT_ISSUER_TRUST_OUTSIDE_VALIDITY"),
        ("valid_from", "valid_until", "GRANT_TRUST_KEY_OUTSIDE_VALIDITY"),
    ):
        start, end = record.get(start_field), record.get(end_field)
        if type(start) is not int or type(end) is not int or not start <= now < end:
            raise GrantProvenanceError(error)
    ref = record.get("public_key_ref")
    if not isinstance(ref, str) or not ref.strip():
        raise GrantProvenanceError("GRANT_PUBLIC_KEY_REFERENCE_INVALID")
    public_key = public_key_loader(ref)
    if not isinstance(public_key, bytes) or len(public_key) != 32:
        raise GrantProvenanceError("GRANT_PUBLIC_KEY_INVALID")
    try:
        _verify_ed25519(public_key, canonical_grant_bytes(claims), signature)
    except AuthorityResolutionError as exc:
        raise GrantProvenanceError(str(exc)) from exc
    return dict(grant)


def accept_authority_grant(envelope: dict[str, Any], *, tenant_id: str,
                           receiver_institution_id: str,
                           trust_key_loader: Callable[[str, str], dict[str, Any] | None],
                           public_key_loader: Callable[[str], bytes],
                           verify_receiver_approval: Callable[[dict[str, Any], dict[str, Any]], bool],
                           receiver_approval: dict[str, Any],
                           clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """Verify issuer provenance, then require independent receiver approval.

    verify_receiver_approval must authenticate the receiver's actor and enforce
    its local authority policy for this exact grant digest. This module never
    treats the issuer's signature as the receiver's consent.
    """
    if not isinstance(receiver_institution_id, str) or not IDENTIFIER.fullmatch(receiver_institution_id):
        raise GrantProvenanceError("INVALID_RECEIVER_INSTITUTION")
    grant = verify_authority_grant(
        envelope, tenant_id=tenant_id, trust_key_loader=trust_key_loader,
        public_key_loader=public_key_loader, clock=clock)
    if grant["institution_id"] != receiver_institution_id:
        raise GrantProvenanceError("GRANT_RECEIVER_INSTITUTION_MISMATCH")
    if not callable(verify_receiver_approval):
        raise GrantProvenanceError("RECEIVER_APPROVAL_VERIFIER_REQUIRED")
    try:
        approved = verify_receiver_approval(grant, receiver_approval)
    except Exception as exc:
        raise GrantProvenanceError("RECEIVER_APPROVAL_VERIFICATION_FAILED") from exc
    if approved is not True:
        raise GrantProvenanceError("RECEIVER_APPROVAL_REQUIRED")
    accepted = dict(grant)
    accepted["status"] = "ACTIVE"
    return {
        "grant": accepted,
        "issuer_envelope": envelope,
        "acceptance": {
            "receiver_institution_id": receiver_institution_id,
            "grant_id": grant["grant_id"],
            "grant_version": grant["version"],
            "grant_digest": __import__("hashlib").sha256(
                canonical_grant_bytes(grant)).hexdigest(),
            "receiver_approval": receiver_approval,
        },
    }
