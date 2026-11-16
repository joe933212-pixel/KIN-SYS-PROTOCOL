# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Receiver-signed acknowledgement receipts for federated instruction delivery.

The receipt binds a receiver's durable decision to the exact signed instruction
message. It authenticates the receiver's attestation; it does not transfer
decision authority or make delivery exactly-once.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import time
from typing import Any, Callable

from institutional_authority import AuthorityResolutionError, _verify_ed25519

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SIGNATURE = re.compile(r"^[A-Za-z0-9_-]{86}$")
PROTOCOL = "kin-sys-federation-acknowledgement-0.1"
CLAIM_FIELDS = {
    "protocol", "message_id", "operation_id", "idempotency_key",
    "sender_installation_id", "sender_institution_id",
    "receiver_installation_id", "receiver_institution_id",
    "original_envelope_digest", "response_digest", "accepted_at", "key_id",
}
ENVELOPE_FIELDS = {"claims", "response", "signature"}
RESPONSE_FIELDS = {
    "message_id", "operation_id", "origin_installation_id",
    "origin_institution_id", "receiver_installation_id",
    "receiver_institution_id", "message_verified", "delivery_status",
    "receiver_decision", "receiver_runtime_pins", "receiver_result",
}


class FederationAcknowledgementError(ValueError):
    """A receiver acknowledgement is malformed, untrusted or misbound."""


def canonical_acknowledgement_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FederationAcknowledgementError(
            "ACKNOWLEDGEMENT_NOT_CANONICALISABLE"
        ) from exc


def _identifier(value: Any, code: str) -> None:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise FederationAcknowledgementError(code)


def _digest(value: Any, code: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise FederationAcknowledgementError(code)


def _message_claims(original_envelope: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(original_envelope, dict) or set(original_envelope) != {"claims", "signature"}:
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_ORIGINAL_ENVELOPE_INVALID")
    claims = original_envelope["claims"]
    if not isinstance(claims, dict):
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_ORIGINAL_ENVELOPE_INVALID")
    required = {
        "message_id", "operation_id", "idempotency_key",
        "issuer_installation_id", "issuer_institution_id",
        "receiver_installation_id", "receiver_institution_id",
    }
    if not required.issubset(claims):
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_ORIGINAL_ENVELOPE_INVALID")
    for field in required:
        _identifier(claims[field], "ACKNOWLEDGEMENT_ORIGINAL_" + field.upper() + "_INVALID")
    return claims


def _stable_response(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict) or not RESPONSE_FIELDS.issubset(response):
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_RESPONSE_INVALID")
    if response.get("message_verified") is not True or response.get("delivery_status") != "COMPLETED":
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_NOT_COMPLETED")
    decision = response.get("receiver_decision")
    if (not isinstance(decision, dict)
            or decision.get("operation_id") != response.get("operation_id")
            or not isinstance(decision.get("request_id"), str)
            or not decision.get("request_id")
            or not isinstance(decision.get("cee_execution_ref"), str)
            or not decision.get("cee_execution_ref")):
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_DECISION_INVALID")
    pins = response.get("receiver_runtime_pins")
    if (not isinstance(pins, dict)
            or pins.get("institution_id") != response.get("receiver_institution_id")
            or not isinstance(pins.get("tenant_id"), str) or not pins["tenant_id"]
            or not isinstance(pins.get("binding_id"), str) or not pins["binding_id"]):
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_RUNTIME_PINS_INVALID")
    if not isinstance(response.get("receiver_result"), dict):
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_RESULT_INVALID")
    # Exclude transport/attempt metadata such as duplicate_delivery and the
    # inbox insertion result. These may legitimately differ on a redelivery.
    return {field: response[field] for field in sorted(RESPONSE_FIELDS)}


def sign_receiver_acknowledgement(
    *, original_envelope: dict[str, Any], response: dict[str, Any],
    key_id: str, accepted_at: int, private_key: Any,
) -> dict[str, Any]:
    """Sign a stable completed receiver response using the receiver's key."""
    original = _message_claims(original_envelope)
    _identifier(key_id, "ACKNOWLEDGEMENT_KEY_ID_INVALID")
    if type(accepted_at) is not int or accepted_at < 0:
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_TIME_INVALID")
    stable = _stable_response(response)
    expected = {
        "message_id": original["message_id"],
        "operation_id": original["operation_id"],
        "origin_installation_id": original["issuer_installation_id"],
        "origin_institution_id": original["issuer_institution_id"],
        "receiver_installation_id": original["receiver_installation_id"],
        "receiver_institution_id": original["receiver_institution_id"],
    }
    if any(stable.get(k) != v for k, v in expected.items()):
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_RESPONSE_SCOPE_MISMATCH")
    original_instruction = original.get("instruction")
    if (not isinstance(original_instruction, dict)
            or stable["receiver_decision"].get("request_id")
                != original_instruction.get("request_id")):
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_DECISION_REQUEST_MISMATCH")
    claims = {
        "protocol": PROTOCOL,
        "message_id": original["message_id"],
        "operation_id": original["operation_id"],
        "idempotency_key": original["idempotency_key"],
        "sender_installation_id": original["issuer_installation_id"],
        "sender_institution_id": original["issuer_institution_id"],
        "receiver_installation_id": original["receiver_installation_id"],
        "receiver_institution_id": original["receiver_institution_id"],
        "original_envelope_digest": hashlib.sha256(
            canonical_acknowledgement_bytes(original_envelope)
        ).hexdigest(),
        "response_digest": hashlib.sha256(
            canonical_acknowledgement_bytes(stable)
        ).hexdigest(),
        "accepted_at": accepted_at,
        "key_id": key_id,
    }
    try:
        signature = private_key.sign(canonical_acknowledgement_bytes(claims))
    except Exception as exc:
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_SIGNING_FAILED") from exc
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_SIGNATURE_INVALID")
    return {
        "claims": claims,
        "response": stable,
        "signature": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
    }


def verify_receiver_acknowledgement(
    receipt: dict[str, Any], *, original_envelope: dict[str, Any],
    sender_tenant_id: str, sender_installation_id: str,
    sender_institution_id: str,
    receiver_trust_key_loader: Callable[[str, str, str], dict[str, Any] | None],
    receiver_public_key_loader: Callable[[str], bytes],
    clock: Callable[[], float] = time.time, clock_skew_seconds: int = 30,
) -> dict[str, Any]:
    """Verify receiver signature, trust, exact message binding and response digest.

    The trust-key loader must use sender-local trust configuration and bind the
    key to the expected receiver installation/institution. It must not derive
    trust solely from the acknowledgement being verified.
    """
    if not isinstance(receipt, dict) or set(receipt) != ENVELOPE_FIELDS:
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_ENVELOPE_INVALID")
    claims, response, signature = receipt["claims"], receipt["response"], receipt["signature"]
    if (not isinstance(claims, dict) or set(claims) != CLAIM_FIELDS
            or not isinstance(signature, str) or not SIGNATURE.fullmatch(signature)):
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_ENVELOPE_INVALID")
    original = _message_claims(original_envelope)
    for field, value in (
        ("sender_installation_id", sender_installation_id),
        ("sender_institution_id", sender_institution_id),
        ("receiver_installation_id", original["receiver_installation_id"]),
        ("receiver_institution_id", original["receiver_institution_id"]),
        ("message_id", original["message_id"]),
        ("operation_id", original["operation_id"]),
        ("idempotency_key", original["idempotency_key"]),
    ):
        _identifier(value, "ACKNOWLEDGEMENT_EXPECTED_" + field.upper() + "_INVALID")
        if claims.get(field) != value:
            raise FederationAcknowledgementError("ACKNOWLEDGEMENT_SCOPE_MISMATCH")
    if (original["issuer_installation_id"] != sender_installation_id
            or original["issuer_institution_id"] != sender_institution_id):
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_SENDER_MISMATCH")
    if claims.get("protocol") != PROTOCOL:
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_PROTOCOL_UNSUPPORTED")
    expected_envelope_digest = hashlib.sha256(
        canonical_acknowledgement_bytes(original_envelope)
    ).hexdigest()
    _digest(claims.get("original_envelope_digest"), "ACKNOWLEDGEMENT_ORIGINAL_DIGEST_INVALID")
    if claims["original_envelope_digest"] != expected_envelope_digest:
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_ORIGINAL_MESSAGE_MISMATCH")
    stable = _stable_response(response)
    expected_response_digest = hashlib.sha256(
        canonical_acknowledgement_bytes(stable)
    ).hexdigest()
    _digest(claims.get("response_digest"), "ACKNOWLEDGEMENT_RESPONSE_DIGEST_INVALID")
    if claims["response_digest"] != expected_response_digest:
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_RESPONSE_DIGEST_MISMATCH")
    expected_response = {
        "message_id": original["message_id"],
        "operation_id": original["operation_id"],
        "origin_installation_id": sender_installation_id,
        "origin_institution_id": sender_institution_id,
        "receiver_installation_id": original["receiver_installation_id"],
        "receiver_institution_id": original["receiver_institution_id"],
    }
    if any(stable.get(k) != v for k, v in expected_response.items()):
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_RESPONSE_SCOPE_MISMATCH")
    original_instruction = original.get("instruction")
    if (not isinstance(original_instruction, dict)
            or stable["receiver_decision"].get("request_id")
                != original_instruction.get("request_id")):
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_DECISION_REQUEST_MISMATCH")
    accepted_at = claims.get("accepted_at")
    if type(accepted_at) is not int or accepted_at < 0:
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_TIME_INVALID")
    if (not callable(clock) or type(clock_skew_seconds) is not int
            or clock_skew_seconds < 0):
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_CLOCK_CONFIGURATION_INVALID")
    _identifier(sender_tenant_id, "ACKNOWLEDGEMENT_SENDER_TENANT_INVALID")
    now = clock()
    if (isinstance(now, bool) or not isinstance(now, (int, float))
            or not math.isfinite(now) or now < 0
            or accepted_at > int(now) + clock_skew_seconds):
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_TIME_INVALID")
    key_id = claims.get("key_id")
    _identifier(key_id, "ACKNOWLEDGEMENT_KEY_ID_INVALID")
    if not callable(receiver_trust_key_loader) or not callable(receiver_public_key_loader):
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_TRUST_CONFIGURATION_INVALID")
    try:
        record = receiver_trust_key_loader(
            sender_tenant_id, original["receiver_installation_id"], key_id
        )
    except Exception as exc:
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_TRUST_LOOKUP_FAILED") from exc
    if not isinstance(record, dict):
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_RECEIVER_NOT_TRUSTED")
    if (record.get("tenant_id") != sender_tenant_id
            or record.get("installation_id") != original["receiver_installation_id"]
            or record.get("institution_id") != original["receiver_institution_id"]
            or record.get("key_id") != key_id
            or record.get("algorithm") != "Ed25519"
            or record.get("status") != "ACTIVE"):
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_RECEIVER_KEY_NOT_ACTIVE")
    key_from, key_until = record.get("valid_from"), record.get("valid_until")
    if (type(key_from) is not int or type(key_until) is not int
            or not key_from <= accepted_at < key_until):
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_KEY_OUTSIDE_VALIDITY")
    public_key_ref = record.get("public_key_ref")
    if not isinstance(public_key_ref, str) or not public_key_ref.strip():
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_PUBLIC_KEY_REFERENCE_INVALID")
    try:
        public_key = receiver_public_key_loader(public_key_ref)
        if not isinstance(public_key, bytes) or len(public_key) != 32:
            raise ValueError("invalid Ed25519 public key")
        _verify_ed25519(public_key, canonical_acknowledgement_bytes(claims), signature)
    except AuthorityResolutionError as exc:
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_SIGNATURE_INVALID") from exc
    except FederationAcknowledgementError:
        raise
    except Exception as exc:
        raise FederationAcknowledgementError("ACKNOWLEDGEMENT_SIGNATURE_INVALID") from exc
    return {"claims": dict(claims), "response": stable, "verified": True}
