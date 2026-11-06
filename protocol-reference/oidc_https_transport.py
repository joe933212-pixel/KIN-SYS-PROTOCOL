# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Bounded, receiver-pinned HTTPS JSON transport for institutional OIDC.

The transport resolves and validates every destination address, then connects to
that exact IP while retaining the DNS hostname for TLS SNI and certificate
verification. Redirects are never followed. Deployments should additionally
apply network-level egress controls and run with a bounded DNS resolver.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
from typing import Any, Callable
from urllib.parse import urlsplit


class OIDCTransportError(RuntimeError):
    """A safe, non-secret-bearing transport failure."""


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _validated_public_addresses(host: str, port: int, resolver: Callable[..., Any]) -> list[str]:
    try:
        literal = ipaddress.ip_address(host)
        addresses = [str(literal)]
    except ValueError:
        try:
            records = resolver(host, port, type=socket.SOCK_STREAM)
        except Exception as exc:
            raise OIDCTransportError("OIDC_DNS_RESOLUTION_FAILED") from exc
        addresses = []
        for record in records:
            try:
                addresses.append(str(ipaddress.ip_address(record[4][0].split("%", 1)[0])))
            except (ValueError, IndexError, TypeError) as exc:
                raise OIDCTransportError("OIDC_DNS_ANSWER_INVALID") from exc

    if not addresses:
        raise OIDCTransportError("OIDC_DNS_NO_ADDRESSES")
    # Fail closed if any answer is non-global; do not pick a public answer from
    # a mixed public/private set.
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if not parsed.is_global or parsed.is_multicast or parsed.is_unspecified:
            raise OIDCTransportError("OIDC_DESTINATION_IP_BLOCKED")
    return list(dict.fromkeys(addresses))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection that dials a previously validated IP, not DNS again."""

    def __init__(self, host: str, port: int, *, pinned_ip: str,
                 timeout: float, context: ssl.SSLContext):
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        if self._tunnel_host:
            raise OIDCTransportError("OIDC_PROXY_TUNNEL_NOT_ALLOWED")
        raw = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class OIDCHTTPSJSONTransport:
    """Callable fetch_json implementation accepted by OIDCDiscoveryJWKSLoader.

    Hostnames must match the institution's explicit exact-match allowlist.
    The default SSL context validates the certificate chain and hostname.
    The resolver and connection factory are injectable for deterministic tests.
    """

    def __init__(
        self, *, allowed_hosts: tuple[str, ...],
        connect_timeout_seconds: float = 3.0,
        read_timeout_seconds: float = 5.0,
        resolver: Callable[..., Any] = socket.getaddrinfo,
        connection_factory: Callable[..., Any] | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ):
        if (not isinstance(allowed_hosts, tuple) or not allowed_hosts
                or any(not isinstance(host, str) or not host
                       or host != host.lower() or host.endswith(".")
                       for host in allowed_hosts)
                or len(set(allowed_hosts)) != len(allowed_hosts)):
            raise OIDCTransportError("OIDC_TRANSPORT_ALLOWLIST_INVALID")
        if (isinstance(connect_timeout_seconds, bool)
                or not isinstance(connect_timeout_seconds, (int, float))
                or not 0.1 <= connect_timeout_seconds <= 30
                or isinstance(read_timeout_seconds, bool)
                or not isinstance(read_timeout_seconds, (int, float))
                or not 0.1 <= read_timeout_seconds <= 60):
            raise OIDCTransportError("OIDC_TRANSPORT_TIMEOUT_INVALID")
        if not callable(resolver):
            raise OIDCTransportError("OIDC_TRANSPORT_RESOLVER_INVALID")
        self._allowed_hosts = frozenset(allowed_hosts)
        self._connect_timeout = float(connect_timeout_seconds)
        self._read_timeout = float(read_timeout_seconds)
        self._resolver = resolver
        self._connection_factory = connection_factory or _PinnedHTTPSConnection
        self._ssl_context = ssl_context or ssl.create_default_context()

    def __call__(self, url: str, *, timeout_seconds: int, max_bytes: int) -> dict[str, Any]:
        if (not isinstance(url, str) or len(url) > 4096
                or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30
                or type(max_bytes) is not int or not 1024 <= max_bytes <= 5_000_000):
            raise OIDCTransportError("OIDC_TRANSPORT_REQUEST_INVALID")
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError as exc:
            raise OIDCTransportError("OIDC_PROVIDER_URL_INVALID") from exc
        host = (parts.hostname or "").lower()
        if (parts.scheme != "https" or not host or host not in self._allowed_hosts
                or parts.username is not None or parts.password is not None
                or parts.fragment or port not in (None, 443)
                or "\\" in url or any(ord(ch) < 0x20 for ch in url)):
            raise OIDCTransportError("OIDC_PROVIDER_URL_REJECTED")
        port = port or 443
        addresses = _validated_public_addresses(host, port, self._resolver)
        # The URL loader controls the request timeout; this transport never
        # exceeds its own stricter configured connect/read bounds.
        connect_timeout = min(self._connect_timeout, float(timeout_seconds))
        read_timeout = min(self._read_timeout, float(timeout_seconds))
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        last_error: Exception | None = None
        for address in addresses:
            connection = None
            try:
                connection = self._connection_factory(
                    host, port, pinned_ip=address,
                    timeout=connect_timeout, context=self._ssl_context,
                )
                connection.request(
                    "GET", path,
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                        "Host": host,
                        "User-Agent": "KIN-SYS-OIDC-Discovery/1.0",
                    },
                )
                if connection.sock is not None:
                    connection.sock.settimeout(read_timeout)
                response = connection.getresponse()
                if 300 <= response.status < 400:
                    raise OIDCTransportError("OIDC_REDIRECT_REJECTED")
                if response.status != 200:
                    raise OIDCTransportError("OIDC_PROVIDER_HTTP_STATUS")
                content_type = response.getheader("Content-Type", "")
                media_type = content_type.split(";", 1)[0].strip().lower()
                if media_type != "application/json" and not (
                    media_type.startswith("application/") and media_type.endswith("+json")
                ):
                    raise OIDCTransportError("OIDC_PROVIDER_CONTENT_TYPE_INVALID")
                content_length = response.getheader("Content-Length")
                if content_length is not None:
                    try:
                        if int(content_length) < 0 or int(content_length) > max_bytes:
                            raise OIDCTransportError("OIDC_PROVIDER_RESPONSE_TOO_LARGE")
                    except ValueError as exc:
                        raise OIDCTransportError("OIDC_PROVIDER_CONTENT_LENGTH_INVALID") from exc
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise OIDCTransportError("OIDC_PROVIDER_RESPONSE_TOO_LARGE")
                try:
                    document = json.loads(
                        body.decode("utf-8"),
                        object_pairs_hook=_unique_object,
                        parse_constant=_reject_constant,
                    )
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    raise OIDCTransportError("OIDC_PROVIDER_JSON_INVALID") from exc
                if not isinstance(document, dict):
                    raise OIDCTransportError("OIDC_PROVIDER_DOCUMENT_INVALID")
                return document
            except OIDCTransportError:
                raise
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                last_error = exc
            finally:
                if connection is not None:
                    connection.close()
        raise OIDCTransportError("OIDC_PROVIDER_CONNECTION_FAILED") from last_error
