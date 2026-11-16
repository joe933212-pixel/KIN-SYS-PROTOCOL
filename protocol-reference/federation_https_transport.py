# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Bounded HTTPS sender transport for durable cross-installation federation.

Destination URLs are supplied by a receiver-installation registry controlled by
the sender institution, never by the instruction payload. DNS answers are
validated and the connection is pinned to the selected IP while TLS hostname
verification remains bound to the configured hostname. Redirects are rejected.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import time
from typing import Any, Callable
from urllib.parse import urlsplit

FEDERATION_PATH = "/federation/v1/messages"
MAX_RESPONSE_BYTES = 256 * 1024


class FederationHTTPTransportError(RuntimeError):
    """A bounded, non-secret-bearing federation transport failure."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON value")


def _public_addresses(host: str, port: int, resolver: Callable[..., Any]) -> list[str]:
    try:
        literal = ipaddress.ip_address(host)
        values = [str(literal)]
    except ValueError:
        try:
            records = resolver(host, port, type=socket.SOCK_STREAM)
        except Exception as exc:
            raise FederationHTTPTransportError("FEDERATION_DNS_RESOLUTION_FAILED") from exc
        values = []
        for record in records:
            try:
                values.append(str(ipaddress.ip_address(record[4][0].split("%", 1)[0])))
            except (ValueError, IndexError, TypeError) as exc:
                raise FederationHTTPTransportError("FEDERATION_DNS_ANSWER_INVALID") from exc
    values = list(dict.fromkeys(values))
    if not values or len(values) > 8:
        raise FederationHTTPTransportError("FEDERATION_DNS_ANSWER_SET_INVALID")
    for value in values:
        address = ipaddress.ip_address(value)
        if not address.is_global or address.is_multicast or address.is_unspecified:
            raise FederationHTTPTransportError("FEDERATION_DESTINATION_IP_BLOCKED")
    return values


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, *, pinned_ip: str,
                 timeout: float, context: ssl.SSLContext):
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._pinned_ip = pinned_ip

    def connect(self):
        if self._tunnel_host:
            raise FederationHTTPTransportError("FEDERATION_PROXY_TUNNEL_NOT_ALLOWED")
        raw = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class FederationHTTPSJSONTransport:
    """POST a signed envelope to the registry-pinned receiver HTTPS endpoint."""

    def __init__(
        self, *, destinations: dict[str, str],
        connect_timeout_seconds: float = 3.0,
        read_timeout_seconds: float = 5.0,
        total_timeout_seconds: float = 10.0,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        resolver: Callable[..., Any] = socket.getaddrinfo,
        connection_factory: Callable[..., Any] | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ):
        if (not isinstance(destinations, dict) or not destinations
                or any(not isinstance(k, str) or not k or not isinstance(v, str)
                       for k, v in destinations.items())):
            raise FederationHTTPTransportError("FEDERATION_DESTINATION_REGISTRY_INVALID")
        if (type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 1024 * 1024
                or not callable(resolver)):
            raise FederationHTTPTransportError("FEDERATION_TRANSPORT_CONFIGURATION_INVALID")
        for value in (connect_timeout_seconds, read_timeout_seconds, total_timeout_seconds):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or value <= 0 or value > 120):
                raise FederationHTTPTransportError("FEDERATION_TIMEOUT_INVALID")
        context = ssl_context or ssl.create_default_context()
        if (context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname
                or context.minimum_version < ssl.TLSVersion.TLSv1_2):
            raise FederationHTTPTransportError("FEDERATION_TLS_VERIFICATION_REQUIRED")
        self._destinations = dict(destinations)
        self._connect_timeout = float(connect_timeout_seconds)
        self._read_timeout = float(read_timeout_seconds)
        self._total_timeout = float(total_timeout_seconds)
        self._max_response_bytes = max_response_bytes
        self._resolver = resolver
        self._connection_factory = connection_factory or _PinnedHTTPSConnection
        self._ssl_context = context

    def post(self, receiver_installation_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
        if (not isinstance(receiver_installation_id, str)
                or receiver_installation_id not in self._destinations
                or not isinstance(envelope, dict)
                or set(envelope) != {"claims", "signature"}
                or not isinstance(envelope.get("claims"), dict)
                or envelope["claims"].get("receiver_installation_id") != receiver_installation_id):
            raise FederationHTTPTransportError("FEDERATION_DESTINATION_OR_ENVELOPE_INVALID")
        try:
            body = json.dumps(
                envelope, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise FederationHTTPTransportError("FEDERATION_ENVELOPE_NOT_SERIALISABLE") from exc
        if len(body) > 64 * 1024:
            raise FederationHTTPTransportError("FEDERATION_REQUEST_TOO_LARGE")

        url = self._destinations[receiver_installation_id]
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError as exc:
            raise FederationHTTPTransportError("FEDERATION_DESTINATION_URL_INVALID") from exc
        host = (parts.hostname or "").lower()
        if (parts.scheme != "https" or not host or parts.username is not None
                or parts.password is not None or parts.fragment
                or port not in (None, 443) or parts.path not in ("", FEDERATION_PATH)
                or parts.query or "\\" in url
                or any(ord(ch) < 0x20 for ch in url)):
            raise FederationHTTPTransportError("FEDERATION_DESTINATION_URL_REJECTED")
        deadline = time.monotonic() + self._total_timeout
        addresses = _public_addresses(host, port or 443, self._resolver)
        last_error = None
        for address in addresses:
            connection = None
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FederationHTTPTransportError("FEDERATION_REQUEST_DEADLINE_EXCEEDED")
                connection = self._connection_factory(
                    host, port or 443, pinned_ip=address,
                    timeout=min(self._connect_timeout, remaining),
                    context=self._ssl_context,
                )
                connection.request(
                    "POST", FEDERATION_PATH, body=body,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Content-Length": str(len(body)),
                        "Cache-Control": "no-store",
                    },
                )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FederationHTTPTransportError("FEDERATION_REQUEST_DEADLINE_EXCEEDED")
                if getattr(connection, "sock", None) is not None:
                    connection.sock.settimeout(min(self._read_timeout, remaining))
                response = connection.getresponse()
                if response.status not in (200, 202):
                    # Do not follow redirects. Surface a stable code only.
                    raise FederationHTTPTransportError(
                        "FEDERATION_REMOTE_HTTP_" + str(response.status))
                raw = response.read(self._max_response_bytes + 1)
                if len(raw) > self._max_response_bytes:
                    raise FederationHTTPTransportError("FEDERATION_RESPONSE_TOO_LARGE")
                value = json.loads(
                    raw.decode("utf-8"), object_pairs_hook=_unique_object,
                    parse_constant=_reject_constant,
                )
                if (not isinstance(value, dict) or not isinstance(value.get("result"), dict)
                        or not isinstance(value.get("request_id"), str)):
                    raise FederationHTTPTransportError("FEDERATION_RESPONSE_INVALID")
                result = value["result"]
                if response.status == 200 and result.get("delivery_status") != "COMPLETED":
                    raise FederationHTTPTransportError("FEDERATION_COMPLETION_RESPONSE_INVALID")
                return {"http_status": response.status, **result}
            except FederationHTTPTransportError:
                raise
            except (OSError, TimeoutError, ssl.SSLError, http.client.HTTPException,
                    UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                # Try another already-validated address within the shared budget.
            finally:
                if connection is not None:
                    connection.close()
        if last_error is not None:
            raise FederationHTTPTransportError("FEDERATION_HTTPS_REQUEST_FAILED") from last_error
        raise FederationHTTPTransportError("FEDERATION_HTTPS_REQUEST_FAILED")


class FederationOutboxDeliveryWorker:
    """Deliver one claimed outbox message and persist only verified receipts.

    The outbox owns lease state and cryptographic acknowledgement verification.
    This worker never trusts an unsigned HTTP response as a completed decision.
    """

    def __init__(self, *, outbox: Any, transport: FederationHTTPSJSONTransport):
        if not callable(getattr(outbox, "acknowledge", None)) or not callable(
            getattr(outbox, "mark_retryable", None)
        ):
            raise FederationHTTPTransportError("FEDERATION_OUTBOX_CONTRACT_INVALID")
        if not callable(getattr(transport, "post", None)):
            raise FederationHTTPTransportError("FEDERATION_TRANSPORT_CONTRACT_INVALID")
        self.outbox = outbox
        self.transport = transport

    def deliver_claim(self, claim: dict[str, Any]) -> dict[str, Any]:
        required = ("sender_tenant_id", "sender_installation_id", "message_id",
                    "receiver_installation_id", "envelope", "lease_owner")
        if not isinstance(claim, dict) or any(not claim.get(k) for k in required):
            raise FederationHTTPTransportError("FEDERATION_OUTBOX_CLAIM_INVALID")
        scope = {
            "sender_tenant_id": claim["sender_tenant_id"],
            "sender_installation_id": claim["sender_installation_id"],
            "message_id": claim["message_id"],
            "worker_id": claim["lease_owner"],
        }
        try:
            response = self.transport.post(claim["receiver_installation_id"], claim["envelope"])
            receipt = response.get("receiver_acknowledgement")
            if response.get("delivery_status") == "COMPLETED" and isinstance(receipt, dict):
                stored = self.outbox.acknowledge(
                    sender_tenant_id=scope["sender_tenant_id"],
                    sender_installation_id=scope["sender_installation_id"],
                    message_id=scope["message_id"], receipt=receipt,
                )
                return {"status": "ACKNOWLEDGED", "outbox": stored}
            self.outbox.mark_retryable(
                **scope, error_code="RECEIVER_ACKNOWLEDGEMENT_PENDING")
            return {"status": "RETRYABLE", "reason": "RECEIVER_ACKNOWLEDGEMENT_PENDING"}
        except Exception as exc:
            # Never persist transport text, response bodies or secrets as an
            # outbox error. The bounded code is stable for operations.
            code = str(exc)
            if not code.startswith("FEDERATION_") or len(code) > 120:
                code = "FEDERATION_TRANSPORT_FAILED"
            try:
                self.outbox.mark_retryable(**scope, error_code=code)
            except Exception:
                pass
            return {"status": "RETRYABLE", "reason": code}
