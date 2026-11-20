# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""HTTP transport boundary for receiver-side cross-installation instructions.

The receiver remains responsible for signature, issuer trust, recipient binding,
expiry, replay protection and independent local execution. This module only
transports a bounded JSON envelope; it does not grant authority or execute a
request itself. The hosting server must bind to loopback for the Windows demo
or be placed behind an authenticated TLS deployment edge.
"""
from __future__ import annotations

import json
from typing import Any, Callable

MAX_FEDERATED_BODY_BYTES = 1_048_576
FEDERATED_ROUTE = "/federation/instructions"


class FederationHttpError(ValueError):
    """A malformed or unsupported federated HTTP request."""


def _response(start_response: Callable, status: int, payload: dict[str, Any]):
    body = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    phrase = {
        200: "OK", 400: "Bad Request", 404: "Not Found",
        405: "Method Not Allowed", 413: "Payload Too Large",
        415: "Unsupported Media Type", 422: "Unprocessable Entity",
        409: "Conflict", 500: "Internal Server Error",
    }.get(status, "Error")
    start_response(str(status) + " " + phrase, [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
    ])
    return [body]


class InstitutionalFederationHttpApplication:
    """Expose one independently configured receiver over a narrow HTTP route."""

    def __init__(self, receiver: Any):
        if not callable(getattr(receiver, "receive", None)):
            raise FederationHttpError("INSTITUTIONAL_FEDERATED_RECEIVER_REQUIRED")
        self.receiver = receiver

    def __call__(self, environ: dict[str, Any], start_response: Callable):
        if environ.get("PATH_INFO") != FEDERATED_ROUTE:
            return _response(start_response, 404, {
                "error": {"code": "ROUTE_NOT_FOUND"},
            })
        if environ.get("REQUEST_METHOD", "GET").upper() != "POST":
            return _response(start_response, 405, {
                "error": {"code": "METHOD_NOT_ALLOWED"},
            })
        content_type = environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return _response(start_response, 415, {
                "error": {"code": "UNSUPPORTED_MEDIA_TYPE"},
            })
        try:
            length = int(environ.get("CONTENT_LENGTH", ""))
        except (TypeError, ValueError):
            return _response(start_response, 400, {
                "error": {"code": "INVALID_CONTENT_LENGTH"},
            })
        if length <= 0:
            return _response(start_response, 400, {
                "error": {"code": "EMPTY_REQUEST_BODY"},
            })
        if length > MAX_FEDERATED_BODY_BYTES:
            return _response(start_response, 413, {
                "error": {"code": "REQUEST_TOO_LARGE"},
            })
        stream = environ.get("wsgi.input")
        raw = stream.read(length) if stream is not None else b""
        if len(raw) != length:
            return _response(start_response, 400, {
                "error": {"code": "INCOMPLETE_REQUEST_BODY"},
            })
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _response(start_response, 400, {
                "error": {"code": "INVALID_JSON"},
            })
        if not isinstance(envelope, dict):
            return _response(start_response, 422, {
                "error": {"code": "JSON_OBJECT_REQUIRED"},
            })
        try:
            result = self.receiver.receive(envelope)
        except Exception as exc:
            # Do not leak cryptographic, trust-store or runtime internals.
            code = str(exc).split(":", 1)[0].strip()
            if not code or len(code) > 96 or not all(
                char.isupper() or char.isdigit() or char == "_" for char in code
            ):
                code = "FEDERATED_INSTRUCTION_REJECTED"
            return _response(start_response, 409, {
                "error": {"code": code, "message": "The receiving institution rejected the instruction."},
                "production_ready": False,
            })
        if not isinstance(result, dict):
            return _response(start_response, 500, {
                "error": {"code": "INVALID_RECEIVER_RESULT"},
            })
        return _response(start_response, 200, {
            "status": result.get("receiver_decision", {}).get("protocol_decision", "UNKNOWN")
                if isinstance(result.get("receiver_decision"), dict) else "UNKNOWN",
            "result": result,
            "production_ready": False,
            "transport": "LOCAL_REFERENCE_HTTP",
        })


def create_federation_http_app(receiver: Any) -> InstitutionalFederationHttpApplication:
    return InstitutionalFederationHttpApplication(receiver)
