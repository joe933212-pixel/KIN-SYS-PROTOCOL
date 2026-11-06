# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Resolve signed actor assertions against institutionally delegated authority.

Legacy HMAC assertions remain available for local/internal reference use.
Federated assertions use Ed25519 and an explicit issuer trust-key loader.
Neither path authenticates a human or independently establishes legal authority.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import sqlite3
import threading
import time
from typing import Any, Callable


class AuthorityResolutionError(ValueError):
    """A signed actor assertion cannot be trusted for the requested action."""


HMAC_CLAIM_FIELDS = {
    "key_id", "tenant_id", "actor_ref", "grant_id", "grant_version",
    "issued_at", "expires_at", "nonce",
}
FEDERATED_CLAIM_FIELDS = HMAC_CLAIM_FIELDS | {"issuer_institution_id"}
GRANT_FIELDS = {
    "grant_id", "tenant_id", "status", "actor_ref", "institution_id",
    "authority_ref", "roles", "resource_types", "resource_ids",
    "valid_from", "valid_until", "version", "issuer_institution_id",
    "approval_ref",
}
MAX_ASSERTION_TTL_SECONDS = 300


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AuthorityResolutionError("ASSERTION_NOT_CANONICALISABLE") from exc


def sign_actor_claims(claims: dict[str, Any], key: bytes) -> str:
    """Local/internal HMAC-SHA256 signing helper; not for cross-institution trust."""
    if not isinstance(key, bytes) or len(key) < 32:
        raise AuthorityResolutionError("HMAC_KEY_MUST_BE_AT_LEAST_32_BYTES")
    return hmac.new(key, canonical_json(claims), hashlib.sha256).hexdigest()


def sign_federated_actor_claims(claims: dict[str, Any], private_key: Any) -> str:
    """Sign federated claims using a cryptography Ed25519 private-key object."""
    try:
        signature = private_key.sign(canonical_json(claims))
    except Exception as exc:
        raise AuthorityResolutionError("ED25519_SIGNING_FAILED") from exc
    return base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")


def _verify_ed25519(public_key_bytes: bytes, message: bytes, signature: str) -> None:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        raw_signature = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        if len(raw_signature) != 64:
            raise ValueError("signature length")
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(raw_signature, message)
    except ImportError as exc:
        raise AuthorityResolutionError("ED25519_DEPENDENCY_MISSING") from exc
    except InvalidSignature as exc:
        raise AuthorityResolutionError("ASSERTION_SIGNATURE_INVALID") from exc
    except Exception as exc:
        raise AuthorityResolutionError("ASSERTION_SIGNATURE_INVALID") from exc


class InMemoryNonceGuard:
    """Single-process replay guard for tests and local reference use only."""
    def __init__(self):
        self._seen: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    def consume(self, tenant_id: str, nonce: str, expires_at: int | None = None) -> bool:
        with self._lock:
            key = (tenant_id, nonce)
            if key in self._seen:
                return False
            self._seen.add(key)
            return True


class SQLiteNonceGuard:
    """Atomic nonce-consumption adapter for local/multi-process SQLite use.

    A unique (tenant, nonce) key is enforced by SQLite, including across
    separate processes sharing this database file. This is not a distributed
    replay service for multiple hosts; production deployments should provide
    the same consume(tenant_id, nonce, expires_at) contract over shared storage.
    """
    def __init__(self, database: str, *, clock: Callable[[], float] = time.time):
        self._db = sqlite3.connect(database, timeout=10.0, isolation_level=None)
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS consumed_assertion_nonces (
              tenant_id TEXT NOT NULL, nonce TEXT NOT NULL,
              expires_at INTEGER NOT NULL, consumed_at INTEGER NOT NULL,
              PRIMARY KEY (tenant_id, nonce)
            )
        """)
        self._clock = clock
        self._lock = threading.Lock()

    def consume(self, tenant_id: str, nonce: str, expires_at: int | None = None) -> bool:
        if (not isinstance(tenant_id, str) or not tenant_id.strip()
                or not isinstance(nonce, str) or not nonce.strip()):
            return False
        expiry = expires_at if type(expires_at) is int else int(self._clock()) + MAX_ASSERTION_TTL_SECONDS
        now = int(self._clock())
        if expiry <= now:
            return False
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    "DELETE FROM consumed_assertion_nonces WHERE expires_at<=?", (now,))
                try:
                    self._db.execute(
                        "INSERT INTO consumed_assertion_nonces VALUES(?,?,?,?)",
                        (tenant_id, nonce, expiry, now))
                except sqlite3.IntegrityError:
                    self._db.execute("ROLLBACK")
                    return False
                self._db.execute("COMMIT")
                return True
            except Exception:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

    def close(self) -> None:
        self._db.close()


class InstitutionalAuthorityResolver:
    """Verify HMAC-local or Ed25519-federated assertions and delegated grants.

    trust_key_loader(tenant_id, key_id) returns a currently trusted key record
    containing issuer_institution_id, algorithm, status, valid_from,
    valid_until and public_key_ref. public_key_loader(ref) returns raw 32-byte
    Ed25519 public key bytes. Both loaders must enforce the receiving
    institution's trust policy and resolve references from trusted custody.
    """
    def __init__(
        self, *,
        grant_loader: Callable[[str, str], dict[str, Any] | None],
        consume_nonce: Callable[[str, str], bool],
        verification_keys: dict[str, bytes] | None = None,
        trust_key_loader: Callable[[str, str], dict[str, Any] | None] | None = None,
        public_key_loader: Callable[[str], bytes] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        if not callable(grant_loader) or not callable(consume_nonce):
            raise AuthorityResolutionError("TRUST_CONFIGURATION_INCOMPLETE")
        keys = verification_keys or {}
        for key_id, key in keys.items():
            if not isinstance(key_id, str) or not key_id.strip():
                raise AuthorityResolutionError("INVALID_KEY_ID")
            if not isinstance(key, bytes) or len(key) < 32:
                raise AuthorityResolutionError("HMAC_KEY_MUST_BE_AT_LEAST_32_BYTES")
        if bool(trust_key_loader) != bool(public_key_loader):
            raise AuthorityResolutionError("FEDERATED_TRUST_CONFIGURATION_INCOMPLETE")
        if not keys and not trust_key_loader:
            raise AuthorityResolutionError("TRUST_CONFIGURATION_INCOMPLETE")
        self._keys = dict(keys)
        self._trust_key_loader = trust_key_loader
        self._public_key_loader = public_key_loader
        self._grant_loader = grant_loader
        self._consume_nonce = consume_nonce
        self._clock = clock

    def _verify_assertion_signature(self, claims: dict[str, Any], signature: str,
                                    tenant_id: str, now: int) -> str:
        key_id = claims.get("key_id")
        if not isinstance(key_id, str) or not key_id.strip():
            raise AuthorityResolutionError("UNTRUSTED_SIGNING_KEY")
        if set(claims) == HMAC_CLAIM_FIELDS:
            if not re.fullmatch(r"[0-9a-f]{64}", signature):
                raise AuthorityResolutionError("INVALID_ASSERTION_SIGNATURE_FORMAT")
            key = self._keys.get(key_id)
            if key is None:
                raise AuthorityResolutionError("UNTRUSTED_SIGNING_KEY")
            expected = hmac.new(key, canonical_json(claims), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise AuthorityResolutionError("ASSERTION_SIGNATURE_INVALID")
            return "HMAC-SHA256"

        if set(claims) != FEDERATED_CLAIM_FIELDS:
            raise AuthorityResolutionError("INVALID_ASSERTION_CLAIMS")
        if self._trust_key_loader is None or self._public_key_loader is None:
            raise AuthorityResolutionError("FEDERATED_TRUST_CONFIGURATION_INCOMPLETE")
        issuer = claims.get("issuer_institution_id")
        if not isinstance(issuer, str) or not issuer.strip():
            raise AuthorityResolutionError("ASSERTION_ISSUER_REQUIRED")
        record = self._trust_key_loader(tenant_id, key_id)
        if not isinstance(record, dict):
            raise AuthorityResolutionError("UNTRUSTED_SIGNING_KEY")
        if (record.get("key_id") != key_id or record.get("tenant_id") != tenant_id
                or record.get("issuer_institution_id") != issuer):
            raise AuthorityResolutionError("TRUST_KEY_ISSUER_MISMATCH")
        if record.get("algorithm") != "Ed25519":
            raise AuthorityResolutionError("UNSUPPORTED_ASSERTION_ALGORITHM")
        if record.get("status") != "ACTIVE":
            raise AuthorityResolutionError("TRUST_KEY_NOT_ACTIVE")
        if (type(record.get("issuer_valid_from")) is not int
                or type(record.get("issuer_valid_until")) is not int
                or not record["issuer_valid_from"] <= now < record["issuer_valid_until"]):
            raise AuthorityResolutionError("ISSUER_TRUST_OUTSIDE_VALIDITY")
        if (type(record.get("valid_from")) is not int
                or type(record.get("valid_until")) is not int
                or not record["valid_from"] <= now < record["valid_until"]):
            raise AuthorityResolutionError("TRUST_KEY_OUTSIDE_VALIDITY")
        ref = record.get("public_key_ref")
        if not isinstance(ref, str) or not ref.strip():
            raise AuthorityResolutionError("TRUST_KEY_REFERENCE_INVALID")
        public_key = self._public_key_loader(ref)
        if not isinstance(public_key, bytes) or len(public_key) != 32:
            raise AuthorityResolutionError("TRUST_PUBLIC_KEY_INVALID")
        if not isinstance(signature, str) or not re.fullmatch(r"[A-Za-z0-9_-]{86}", signature):
            raise AuthorityResolutionError("INVALID_ASSERTION_SIGNATURE_FORMAT")
        _verify_ed25519(public_key, canonical_json(claims), signature)
        return "Ed25519"

    def resolve(self, assertion: dict[str, Any], *, tenant_id: str,
                resource_type: str, resource_id: str) -> dict[str, Any]:
        if not isinstance(assertion, dict) or set(assertion) != {"claims", "signature"}:
            raise AuthorityResolutionError("INVALID_ASSERTION_ENVELOPE")
        claims, signature = assertion["claims"], assertion["signature"]
        if not isinstance(claims, dict) or not isinstance(signature, str):
            raise AuthorityResolutionError("INVALID_ASSERTION_ENVELOPE")
        now = int(self._clock())
        algorithm = self._verify_assertion_signature(claims, signature, tenant_id, now)

        issued, expires = claims.get("issued_at"), claims.get("expires_at")
        if type(issued) is not int or type(expires) is not int:
            raise AuthorityResolutionError("INVALID_ASSERTION_TIME")
        if issued > now or expires <= now or expires <= issued:
            raise AuthorityResolutionError("ASSERTION_EXPIRED_OR_NOT_YET_VALID")
        if expires - issued > MAX_ASSERTION_TTL_SECONDS:
            raise AuthorityResolutionError("ASSERTION_TTL_EXCEEDED")
        if claims.get("tenant_id") != tenant_id:
            raise AuthorityResolutionError("ASSERTION_TENANT_MISMATCH")
        for field in ("actor_ref", "grant_id", "grant_version", "nonce"):
            value = claims.get(field)
            if not isinstance(value, str) or not value.strip():
                raise AuthorityResolutionError("INVALID_ASSERTION_" + field.upper())
        if not re.fullmatch(r"[A-Za-z0-9._:-]{16,128}", claims["nonce"]):
            raise AuthorityResolutionError("INVALID_ASSERTION_NONCE")

        grant = self._grant_loader(tenant_id, claims["grant_id"])
        if not isinstance(grant, dict) or set(grant) != GRANT_FIELDS:
            raise AuthorityResolutionError("AUTHORITY_GRANT_NOT_FOUND_OR_MALFORMED")
        if (grant["tenant_id"] != tenant_id or grant["grant_id"] != claims["grant_id"]
                or grant["version"] != claims["grant_version"]):
            raise AuthorityResolutionError("AUTHORITY_GRANT_SCOPE_MISMATCH")
        if algorithm == "Ed25519" and grant["issuer_institution_id"] != claims["issuer_institution_id"]:
            raise AuthorityResolutionError("GRANT_ISSUER_MISMATCH")
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
                not isinstance(v, str) or not v.strip() for v in grant[field]):
                raise AuthorityResolutionError("AUTHORITY_GRANT_SCOPE_INVALID")
        if resource_type not in grant["resource_types"]:
            raise AuthorityResolutionError("AUTHORITY_RESOURCE_TYPE_OUT_OF_SCOPE")
        if "*" not in grant["resource_ids"] and resource_id not in grant["resource_ids"]:
            raise AuthorityResolutionError("AUTHORITY_RESOURCE_ID_OUT_OF_SCOPE")
        nonce_consumed = self._consume_nonce(
            tenant_id, claims["nonce"], expires_at=expires)
        if nonce_consumed is not True:
            raise AuthorityResolutionError("ASSERTION_REPLAY_DETECTED")
        return {
            "actor_ref": grant["actor_ref"], "institution_id": grant["institution_id"],
            "authenticated": True, "verified": True, "authority_ref": grant["authority_ref"],
            "roles": list(grant["roles"]),
            "grant_ref": grant["grant_id"] + "@" + grant["version"],
            "grant_issuer_institution_id": grant["issuer_institution_id"],
            "grant_approval_ref": grant["approval_ref"],
            "assertion_algorithm": algorithm,
            "assertion_issuer_institution_id": claims.get("issuer_institution_id"),
        }
