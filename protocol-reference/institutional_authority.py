# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Resolve signed actor assertions against institutionally delegated authority.

HMAC keys and grant lookup must be supplied by trusted infrastructure. This
module verifies assertions; it does not authenticate a human or establish the
legal validity of a grant. Use a shared, atomic nonce store in deployments.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import time
from typing import Any, Callable


class AuthorityResolutionError(ValueError):
    """A signed actor assertion cannot be trusted for the requested action."""


CLAIM_FIELDS = {
    "key_id", "tenant_id", "actor_ref", "grant_id",
    "issued_at", "expires_at", "nonce",
}
GRANT_FIELDS = {
    "grant_id", "tenant_id", "status", "actor_ref", "institution_id",
    "authority_ref", "roles", "resource_types", "resource_ids",
    "valid_from", "valid_until", "version", "issuer_institution_id",
    "approval_ref",
}
MAX_ASSERTION_TTL_SECONDS = 300


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AuthorityResolutionError("ASSERTION_NOT_CANONICALISABLE") from exc


def sign_actor_claims(claims: dict[str, Any], key: bytes) -> str:
    """Test/issuer helper: return a lowercase HMAC-SHA256 hex signature."""
    if not isinstance(key, bytes) or len(key) < 32:
        raise AuthorityResolutionError("HMAC_KEY_MUST_BE_AT_LEAST_32_BYTES")
    return hmac.new(key, canonical_json(claims), hashlib.sha256).hexdigest()


class InMemoryNonceGuard:
    """Single-process replay guard for tests and local reference use only."""

    def __init__(self):
        self._seen: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    def consume(self, tenant_id: str, nonce: str) -> bool:
        with self._lock:
            key = (tenant_id, nonce)
            if key in self._seen:
                return False
            self._seen.add(key)
            return True


class InstitutionalAuthorityResolver:
    """Verify a signed actor assertion and resolve its active delegated grant.

    grant_loader(tenant_id, grant_id) must retrieve an authoritative,
    tenant-scoped grant snapshot. consume_nonce must atomically reject reuse.
    Keys should be injected from a secret manager and rotated by key_id.
    """

    def __init__(
        self,
        *,
        verification_keys: dict[str, bytes],
        grant_loader: Callable[[str, str], dict[str, Any] | None],
        consume_nonce: Callable[[str, str], bool],
        clock: Callable[[], float] = time.time,
    ):
        if not verification_keys or not callable(grant_loader) or not callable(consume_nonce):
            raise AuthorityResolutionError("TRUST_CONFIGURATION_INCOMPLETE")
        for key_id, key in verification_keys.items():
            if not isinstance(key_id, str) or not key_id.strip():
                raise AuthorityResolutionError("INVALID_KEY_ID")
            if not isinstance(key, bytes) or len(key) < 32:
                raise AuthorityResolutionError("HMAC_KEY_MUST_BE_AT_LEAST_32_BYTES")
        self._keys = dict(verification_keys)
        self._grant_loader = grant_loader
        self._consume_nonce = consume_nonce
        self._clock = clock

    def resolve(
        self, assertion: dict[str, Any], *, tenant_id: str,
        resource_type: str, resource_id: str,
    ) -> dict[str, Any]:
        if not isinstance(assertion, dict) or set(assertion) != {"claims", "signature"}:
            raise AuthorityResolutionError("INVALID_ASSERTION_ENVELOPE")
        claims, signature = assertion["claims"], assertion["signature"]
        if not isinstance(claims, dict) or set(claims) != CLAIM_FIELDS:
            raise AuthorityResolutionError("INVALID_ASSERTION_CLAIMS")
        if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
            raise AuthorityResolutionError("INVALID_ASSERTION_SIGNATURE_FORMAT")
        key_id = claims.get("key_id")
        key = self._keys.get(key_id) if isinstance(key_id, str) else None
        if key is None:
            raise AuthorityResolutionError("UNTRUSTED_SIGNING_KEY")
        expected = hmac.new(key, canonical_json(claims), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise AuthorityResolutionError("ASSERTION_SIGNATURE_INVALID")

        now = int(self._clock())
        issued, expires = claims.get("issued_at"), claims.get("expires_at")
        if type(issued) is not int or type(expires) is not int:
            raise AuthorityResolutionError("INVALID_ASSERTION_TIME")
        if issued > now or expires <= now or expires <= issued:
            raise AuthorityResolutionError("ASSERTION_EXPIRED_OR_NOT_YET_VALID")
        if expires - issued > MAX_ASSERTION_TTL_SECONDS:
            raise AuthorityResolutionError("ASSERTION_TTL_EXCEEDED")
        if claims.get("tenant_id") != tenant_id:
            raise AuthorityResolutionError("ASSERTION_TENANT_MISMATCH")
        for field in ("actor_ref", "grant_id", "nonce"):
            value = claims.get(field)
            if not isinstance(value, str) or not value.strip():
                raise AuthorityResolutionError("INVALID_ASSERTION_" + field.upper())
        if not re.fullmatch(r"[A-Za-z0-9._:-]{16,128}", claims["nonce"]):
            raise AuthorityResolutionError("INVALID_ASSERTION_NONCE")

        grant = self._grant_loader(tenant_id, claims["grant_id"])
        if not isinstance(grant, dict) or set(grant) != GRANT_FIELDS:
            raise AuthorityResolutionError("AUTHORITY_GRANT_NOT_FOUND_OR_MALFORMED")
        if grant["tenant_id"] != tenant_id or grant["grant_id"] != claims["grant_id"]:
            raise AuthorityResolutionError("AUTHORITY_GRANT_SCOPE_MISMATCH")
        if grant["status"] != "ACTIVE":
            raise AuthorityResolutionError("AUTHORITY_GRANT_NOT_ACTIVE")
        if grant["actor_ref"] != claims["actor_ref"]:
            raise AuthorityResolutionError("ACTOR_NOT_BOUND_TO_AUTHORITY_GRANT")
        if not all(isinstance(grant.get(k), str) and grant[k].strip()
                   for k in ("institution_id", "authority_ref", "issuer_institution_id",
                             "approval_ref", "version")):
            raise AuthorityResolutionError("AUTHORITY_GRANT_FIELDS_INVALID")
        if type(grant["valid_from"]) is not int or type(grant["valid_until"]) is not int:
            raise AuthorityResolutionError("AUTHORITY_GRANT_TIME_INVALID")
        if not grant["valid_from"] <= now < grant["valid_until"]:
            raise AuthorityResolutionError("AUTHORITY_GRANT_OUTSIDE_VALIDITY")
        for field in ("roles", "resource_types", "resource_ids"):
            if not isinstance(grant[field], list) or not grant[field] or any(
                not isinstance(v, str) or not v.strip() for v in grant[field]
            ):
                raise AuthorityResolutionError("AUTHORITY_GRANT_SCOPE_INVALID")
        if resource_type not in grant["resource_types"]:
            raise AuthorityResolutionError("AUTHORITY_RESOURCE_TYPE_OUT_OF_SCOPE")
        if "*" not in grant["resource_ids"] and resource_id not in grant["resource_ids"]:
            raise AuthorityResolutionError("AUTHORITY_RESOURCE_ID_OUT_OF_SCOPE")
        if not self._consume_nonce(tenant_id, claims["nonce"]):
            raise AuthorityResolutionError("ASSERTION_REPLAY_DETECTED")

        return {
            "actor_ref": grant["actor_ref"],
            "institution_id": grant["institution_id"],
            "authenticated": True,
            "verified": True,
            "authority_ref": grant["authority_ref"],
            "roles": list(grant["roles"]),
            "grant_ref": grant["grant_id"] + "@" + grant["version"],
            "grant_issuer_institution_id": grant["issuer_institution_id"],
            "grant_approval_ref": grant["approval_ref"],
        }
