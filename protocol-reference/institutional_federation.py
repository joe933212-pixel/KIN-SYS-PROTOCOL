# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Signed cross-installation instruction handoff primitives.

This module verifies message origin, receiver addressing, freshness, payload
integrity and replay protection. Verification is not institutional approval:
the receiving installation must still execute the instruction through its own
MINAH -> CEE -> Final Decision pipeline and its own identity/authority policy.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from governed_contracts import verify_instruction_payload_digest

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SIGNATURE = re.compile(r"^[A-Za-z0-9_-]{86}$")
CLAIM_FIELDS = {
    "protocol", "message_id", "operation_id", "idempotency_key", "key_id",
    "issuer_installation_id", "issuer_institution_id",
    "receiver_installation_id", "receiver_institution_id",
    "issued_at", "expires_at", "nonce", "instruction",
}
ENVELOPE_FIELDS = {"claims", "signature"}
PROTOCOL = "kin-sys-cross-installation-instruction-0.1"


class FederationMessageError(ValueError):
    """A cross-installation instruction handoff is invalid or untrusted."""


def canonical_message_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FederationMessageError("MESSAGE_NOT_CANONICALISABLE") from exc


def _identifier(value: Any, code: str) -> None:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise FederationMessageError(code)


def _validate_claims(claims: Any) -> None:
    if not isinstance(claims, dict) or set(claims) != CLAIM_FIELDS:
        raise FederationMessageError("FEDERATION_CLAIMS_INVALID")
    for field in (
        "message_id", "operation_id", "idempotency_key", "key_id",
        "issuer_installation_id", "issuer_institution_id",
        "receiver_installation_id", "receiver_institution_id", "nonce",
    ):
        _identifier(claims[field], "INVALID_FEDERATION_" + field.upper())
    if claims["protocol"] != PROTOCOL:
        raise FederationMessageError("FEDERATION_PROTOCOL_UNSUPPORTED")
    if (type(claims["issued_at"]) is not int
            or type(claims["expires_at"]) is not int
            or claims["expires_at"] <= claims["issued_at"]):
        raise FederationMessageError("FEDERATION_VALIDITY_INVALID")
    instruction = claims["instruction"]
    if not isinstance(instruction, dict):
        raise FederationMessageError("FEDERATION_INSTRUCTION_INVALID")
    try:
        checked = verify_instruction_payload_digest(instruction)
    except Exception as exc:
        raise FederationMessageError("FEDERATION_INSTRUCTION_DIGEST_INVALID") from exc
    if checked.get("operation_id") != claims["operation_id"]:
        raise FederationMessageError("FEDERATION_OPERATION_ID_MISMATCH")
    if checked.get("idempotency_key") != claims["idempotency_key"]:
        raise FederationMessageError("FEDERATION_IDEMPOTENCY_KEY_MISMATCH")


def sign_cross_installation_instruction(
    *, instruction: dict[str, Any], message_id: str, key_id: str,
    issuer_installation_id: str, issuer_institution_id: str,
    receiver_installation_id: str, receiver_institution_id: str,
    nonce: str, issued_at: int, expires_at: int, private_key: Any,
) -> dict[str, Any]:
    """Sign a destination-addressed instruction using an Ed25519 issuer key."""
    try:
        checked = verify_instruction_payload_digest(instruction)
    except Exception as exc:
        raise FederationMessageError("FEDERATION_INSTRUCTION_DIGEST_INVALID") from exc
    claims = {
        "protocol": PROTOCOL,
        "message_id": message_id,
        "operation_id": checked["operation_id"],
        "idempotency_key": checked["idempotency_key"],
        "key_id": key_id,
        "issuer_installation_id": issuer_installation_id,
        "issuer_institution_id": issuer_institution_id,
        "receiver_installation_id": receiver_installation_id,
        "receiver_institution_id": receiver_institution_id,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "nonce": nonce,
        "instruction": checked,
    }
    _validate_claims(claims)
    try:
        signature = private_key.sign(canonical_message_bytes(claims))
    except Exception as exc:
        raise FederationMessageError("FEDERATION_SIGNING_FAILED") from exc
    return {
        "claims": claims,
        "signature": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
    }


def verify_cross_installation_instruction(
    envelope: dict[str, Any], *,
    receiver_tenant_id: str,
    receiver_installation_id: str,
    receiver_institution_id: str,
    trust_key_loader: Callable[[str, str], dict[str, Any] | None],
    public_key_loader: Callable[[str], bytes],
    consume_nonce: Callable[[str, str, str, int], bool],
    clock: Callable[[], float] = time.time,
    max_message_lifetime_seconds: int = 300,
    clock_skew_seconds: int = 30,
) -> dict[str, Any]:
    """Verify an incoming handoff and atomically consume its replay nonce.

    trust_key_loader is receiver-local and must return a key only under an
    active trust relationship. consume_nonce must be a durable atomic
    insert-if-absent operation scoped by receiver tenant, issuer installation
    and nonce. This function does not authorise or execute the instruction.
    """
    _identifier(receiver_tenant_id, "RECEIVER_TENANT_REQUIRED")
    _identifier(receiver_installation_id, "RECEIVER_INSTALLATION_REQUIRED")
    _identifier(receiver_institution_id, "RECEIVER_INSTITUTION_REQUIRED")
    if (not callable(trust_key_loader) or not callable(public_key_loader)
            or not callable(consume_nonce) or not callable(clock)
            or type(max_message_lifetime_seconds) is not int
            or max_message_lifetime_seconds < 1
            or type(clock_skew_seconds) is not int or clock_skew_seconds < 0):
        raise FederationMessageError("FEDERATION_VERIFIER_CONFIGURATION_INVALID")
    if not isinstance(envelope, dict) or set(envelope) != ENVELOPE_FIELDS:
        raise FederationMessageError("FEDERATION_ENVELOPE_INVALID")
    claims, signature = envelope["claims"], envelope["signature"]
    _validate_claims(claims)
    if not isinstance(signature, str) or not SIGNATURE.fullmatch(signature):
        raise FederationMessageError("FEDERATION_SIGNATURE_INVALID")
    if (claims["receiver_installation_id"] != receiver_installation_id
            or claims["receiver_institution_id"] != receiver_institution_id):
        raise FederationMessageError("FEDERATION_RECIPIENT_MISMATCH")

    now = int(clock())
    if (claims["issued_at"] > now + clock_skew_seconds
            or claims["expires_at"] <= now
            or claims["expires_at"] - claims["issued_at"] > max_message_lifetime_seconds):
        raise FederationMessageError("FEDERATION_MESSAGE_EXPIRED_OR_OUTSIDE_WINDOW")

    try:
        record = trust_key_loader(receiver_tenant_id, claims["key_id"])
    except Exception as exc:
        raise FederationMessageError("FEDERATION_TRUST_LOOKUP_FAILED") from exc
    if not isinstance(record, dict):
        raise FederationMessageError("FEDERATION_ISSUER_NOT_TRUSTED")
    if (record.get("tenant_id") != receiver_tenant_id
            or record.get("key_id") != claims["key_id"]
            or record.get("issuer_institution_id") != claims["issuer_institution_id"]
            or record.get("algorithm") != "Ed25519"
            or record.get("status") != "ACTIVE"):
        raise FederationMessageError("FEDERATION_TRUST_KEY_MISMATCH_OR_INACTIVE")
    for start, end in (
        (record.get("issuer_valid_from"), record.get("issuer_valid_until")),
        (record.get("valid_from"), record.get("valid_until")),
    ):
        if type(start) is not int or type(end) is not int or not start <= now < end:
            raise FederationMessageError("FEDERATION_TRUST_OUTSIDE_VALIDITY")
    key_ref = record.get("public_key_ref")
    if not isinstance(key_ref, str) or not key_ref.strip():
        raise FederationMessageError("FEDERATION_PUBLIC_KEY_REFERENCE_INVALID")
    try:
        public_bytes = public_key_loader(key_ref)
        if not isinstance(public_bytes, bytes) or len(public_bytes) != 32:
            raise ValueError("invalid Ed25519 public key length")
        raw_signature = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(
            raw_signature, canonical_message_bytes(claims))
    except InvalidSignature as exc:
        raise FederationMessageError("FEDERATION_SIGNATURE_VERIFICATION_FAILED") from exc
    except FederationMessageError:
        raise
    except Exception as exc:
        raise FederationMessageError("FEDERATION_SIGNATURE_VERIFICATION_FAILED") from exc

    # Consume only after all cryptographic, routing, trust and freshness checks.
    # The callback must be atomic across every receiver worker.
    try:
        consumed = consume_nonce(
            receiver_tenant_id, claims["issuer_installation_id"],
            claims["nonce"], claims["expires_at"])
    except Exception as exc:
        raise FederationMessageError("FEDERATION_REPLAY_GUARD_UNAVAILABLE") from exc
    if consumed is not True:
        raise FederationMessageError("FEDERATION_MESSAGE_REPLAYED")

    return {
        "verified": True,
        "message_id": claims["message_id"],
        "operation_id": claims["operation_id"],
        "issuer_installation_id": claims["issuer_installation_id"],
        "issuer_institution_id": claims["issuer_institution_id"],
        "receiver_installation_id": claims["receiver_installation_id"],
        "receiver_institution_id": claims["receiver_institution_id"],
        "instruction": claims["instruction"],
        "instruction_digest": hashlib.sha256(
            canonical_message_bytes(claims["instruction"])).hexdigest(),
        "authority_inherited": False,
        "execution_authorised": False,
    }


def sqlite_nonce_consumer(guard: Any) -> Callable[[str, str, str, int], bool]:
    """Adapt SQLiteNonceGuard to federation's installation-scoped replay key.

    The supplied guard must expose its existing atomic consume(tenant, nonce,
    expires_at) method. This is suitable only where all receiver workers share
    the same SQLite file; it is not a multi-host distributed replay service.
    """
    consume = getattr(guard, "consume", None)
    if not callable(consume):
        raise FederationMessageError("FEDERATION_REPLAY_GUARD_INVALID")

    def scoped(receiver_tenant_id: str, issuer_installation_id: str,
               nonce: str, expires_at: int) -> bool:
        scope = hashlib.sha256(
            (issuer_installation_id + "\\0" + nonce).encode("utf-8")
        ).hexdigest()
        return consume(receiver_tenant_id, "federation:" + scope, expires_at)

    return scoped
