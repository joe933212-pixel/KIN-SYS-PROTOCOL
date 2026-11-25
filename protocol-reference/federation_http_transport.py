# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Authenticated HTTP ingress for cross-installation federation messages.

This WSGI adapter is deliberately separate from the local synthetic prototype
HTTP service. TLS termination and peer-certificate validation belong to the
deployment edge. The injected peer_identity_resolver MUST obtain the peer
installation identity only from authenticated, trusted transport metadata
(e.g. a verified mTLS client certificate supplied by a tightly configured
reverse proxy). It must never trust caller-controlled HTTP headers.

The authenticated transport identity is compared with the signed envelope
issuer before the receiver is invoked. The receiver still performs its own
message signature, trust, destination, freshness, replay and local-authority
checks. Transport authentication is not execution authorisation.
"""
from __future__ import annotations

import json
import re
import uuid
from http import HTTPStatus
from typing import Any, Callable

MAX_FEDERATION_BODY_BYTES = 64 * 1024
FEDERATION_PATH = "/federation/v1/messages"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_INSTALLATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class FederationHttpError(ValueError):
    """An HTTP federation request failed a transport-boundary check."""


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _response(start_response: Callable, status: int, body: dict[str, Any],
              request_id: str) -> list[bytes]:
    payload = _json_bytes({"request_id": request_id, **body})
    start_response(
        f"{status} {HTTPStatus(status).phrase}",
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(payload))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Request-ID", request_id),
        ],
    )
    return [payload]


class FederationHttpIngressApplication:
    """Narrow WSGI endpoint for one configured receiving installation."""

    def __init__(
        self, *, receiver: Any,
        peer_identity_resolver: Callable[[dict[str, Any]], str],
        max_body_bytes: int = MAX_FEDERATION_BODY_BYTES,
    ):
        if not callable(getattr(receiver, "receive", None)):
            raise FederationHttpError("FEDERATION_RECEIVER_REQUIRED")
        if not callable(peer_identity_resolver):
            raise FederationHttpError("AUTHENTICATED_PEER_RESOLVER_REQUIRED")
        if type(max_body_bytes) is not int or not 1 <= max_body_bytes <= 1024 * 1024:
            raise FederationHttpError("FEDERATION_BODY_LIMIT_INVALID")
        self.receiver = receiver
        self.peer_identity_resolver = peer_identity_resolver
        self.max_body_bytes = max_body_bytes

    def __call__(self, environ: dict[str, Any], start_response: Callable):
        supplied_id = environ.get("HTTP_X_REQUEST_ID", "")
        request_id = (
            supplied_id if isinstance(supplied_id, str)
            and _REQUEST_ID.fullmatch(supplied_id) else str(uuid.uuid4())
        )
        if environ.get("REQUEST_METHOD", "").upper() != "POST":
            return _response(start_response, 405, {"error": "METHOD_NOT_ALLOWED"}, request_id)
        if environ.get("PATH_INFO") != FEDERATION_PATH:
            return _response(start_response, 404, {"error": "ROUTE_NOT_FOUND"}, request_id)

        content_type = environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return _response(start_response, 415, {"error": "JSON_CONTENT_TYPE_REQUIRED"}, request_id)

        # Require a bounded Content-Length. Chunked transfer is intentionally
        # rejected at this WSGI boundary unless the front server de-chunks and
        # supplies a trustworthy bounded length.
        raw_length = environ.get("CONTENT_LENGTH")
        if not isinstance(raw_length, str) or not raw_length.isdigit():
            return _response(start_response, 411, {"error": "CONTENT_LENGTH_REQUIRED"}, request_id)
        length = int(raw_length)
        if length < 1:
            return _response(start_response, 400, {"error": "EMPTY_REQUEST_BODY"}, request_id)
        if length > self.max_body_bytes:
            return _response(start_response, 413, {"error": "REQUEST_BODY_TOO_LARGE"}, request_id)
        stream = environ.get("wsgi.input")
        if not callable(getattr(stream, "read", None)):
            return _response(start_response, 400, {"error": "REQUEST_BODY_UNAVAILABLE"}, request_id)
        try:
            body = stream.read(length)
            if not isinstance(body, bytes) or len(body) != length:
                raise ValueError("truncated request body")
            envelope = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            return _response(start_response, 400, {"error": "INVALID_JSON_BODY"}, request_id)
        if not isinstance(envelope, dict) or set(envelope) != {"claims", "signature"}:
            return _response(start_response, 400, {"error": "FEDERATION_ENVELOPE_INVALID"}, request_id)
        claims = envelope.get("claims")
        issuer = claims.get("issuer_installation_id") if isinstance(claims, dict) else None
        if not isinstance(issuer, str) or not _INSTALLATION_ID.fullmatch(issuer):
            return _response(start_response, 400, {"error": "FEDERATION_ISSUER_INVALID"}, request_id)

        try:
            authenticated_peer = self.peer_identity_resolver(environ)
        except Exception:
            return _response(start_response, 401, {"error": "TRANSPORT_PEER_UNAUTHENTICATED"}, request_id)
        if (not isinstance(authenticated_peer, str)
                or not _INSTALLATION_ID.fullmatch(authenticated_peer)):
            return _response(start_response, 401, {"error": "TRANSPORT_PEER_UNAUTHENTICATED"}, request_id)
        if authenticated_peer != issuer:
            return _response(start_response, 403, {"error": "TRANSPORT_ISSUER_MISMATCH"}, request_id)

        try:
            result = self.receiver.receive(envelope)
        except Exception:
            # Do not leak trust-store, key-loader, execution or database detail
            # through a public federation endpoint.
            return _response(start_response, 400, {"error": "FEDERATION_MESSAGE_REJECTED"}, request_id)
        if not isinstance(result, dict):
            return _response(start_response, 502, {"error": "FEDERATION_RECEIVER_RESPONSE_INVALID"}, request_id)
        status = 200 if result.get("delivery_status") == "COMPLETED" else 202
        return _response(start_response, status, {"result": result}, request_id)
