# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Receiver-pinned OIDC discovery and JWKS cache.

Network transport is injected deliberately. A production fetch_json implementation
must enforce TLS certificate validation, no redirects, bounded connect/read timeouts,
response-size limits, DNS/IP egress policy and JSON content-type validation.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from institutional_identity import IdentityResolutionError


_PRIVATE_JWK_FIELDS = frozenset({"d", "p", "q", "dp", "dq", "qi", "oth", "k"})


def _https_url_host(url: Any, code: str) -> str:
    if not isinstance(url, str) or len(url) > 4096:
        raise IdentityResolutionError(code)
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise IdentityResolutionError(code) from exc
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment
            or port not in (None, 443) or "\\" in url
            or any(ord(ch) < 0x20 for ch in url)):
        raise IdentityResolutionError(code)
    return parts.hostname.lower()


class OIDCDiscoveryJWKSLoader:
    """Fetch and cache discovery metadata/JWKS for one institution-pinned issuer.

    fetch_json(url, timeout_seconds=..., max_bytes=...) must return a decoded JSON
    object and must not follow redirects. URL and host policy is enforced here;
    transport-level TLS, DNS/IP egress and response framing are enforced by the
    injected transport and deployment network policy.
    """

    def __init__(
        self, *, issuer: str, allowed_hosts: tuple[str, ...],
        fetch_json: Callable[..., dict[str, Any]],
        allowed_algorithms: tuple[str, ...] = ("RS256", "ES256", "EdDSA"),
        cache_ttl_seconds: int = 300, timeout_seconds: int = 5,
        max_response_bytes: int = 1_048_576, max_jwks_keys: int = 100,
        clock: Callable[[], float] = time.time,
    ):
        issuer_host = _https_url_host(issuer, "OIDC_ISSUER_CONFIGURATION_INVALID")
        if not callable(fetch_json) or not callable(clock):
            raise IdentityResolutionError("OIDC_DISCOVERY_CONFIGURATION_INVALID")
        if (not isinstance(allowed_hosts, tuple) or not allowed_hosts
                or any(not isinstance(host, str) or not host
                       or host != host.lower() for host in allowed_hosts)):
            raise IdentityResolutionError("OIDC_HOST_ALLOWLIST_INVALID")
        hosts = frozenset(allowed_hosts)
        if issuer_host not in hosts:
            raise IdentityResolutionError("OIDC_ISSUER_HOST_NOT_ALLOWLISTED")
        if (not isinstance(allowed_algorithms, tuple) or not allowed_algorithms
                or any(alg not in {"RS256", "RS384", "RS512", "ES256", "ES384", "EdDSA"}
                       for alg in allowed_algorithms)
                or len(set(allowed_algorithms)) != len(allowed_algorithms)):
            raise IdentityResolutionError("OIDC_ALGORITHM_POLICY_INVALID")
        if type(cache_ttl_seconds) is not int or not 1 <= cache_ttl_seconds <= 3600:
            raise IdentityResolutionError("OIDC_DISCOVERY_CACHE_POLICY_INVALID")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
            raise IdentityResolutionError("OIDC_DISCOVERY_TIMEOUT_INVALID")
        if type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 5_000_000:
            raise IdentityResolutionError("OIDC_DISCOVERY_RESPONSE_LIMIT_INVALID")
        if type(max_jwks_keys) is not int or not 1 <= max_jwks_keys <= 500:
            raise IdentityResolutionError("OIDC_JWKS_KEY_LIMIT_INVALID")
        self.issuer = issuer
        self.discovery_url = issuer.rstrip("/") + "/.well-known/openid-configuration"
        self.allowed_hosts = hosts
        self.allowed_algorithms = frozenset(allowed_algorithms)
        self._fetch_json = fetch_json
        self._ttl = cache_ttl_seconds
        self._timeout = timeout_seconds
        self._max_bytes = max_response_bytes
        self._max_keys = max_jwks_keys
        self._clock = clock
        self._lock = threading.RLock()
        self._expires_at = 0.0
        self._jwks: dict[str, Any] | None = None

    def _fetch(self, url: str) -> dict[str, Any]:
        host = _https_url_host(url, "OIDC_PROVIDER_URL_INVALID")
        if host not in self.allowed_hosts:
            raise IdentityResolutionError("OIDC_PROVIDER_HOST_NOT_ALLOWLISTED")
        try:
            value = self._fetch_json(
                url, timeout_seconds=self._timeout, max_bytes=self._max_bytes
            )
        except Exception as exc:
            raise IdentityResolutionError("OIDC_PROVIDER_DOCUMENT_UNAVAILABLE") from exc
        if not isinstance(value, dict):
            raise IdentityResolutionError("OIDC_PROVIDER_DOCUMENT_INVALID")
        return value

    def _load(self) -> dict[str, Any]:
        metadata = self._fetch(self.discovery_url)
        if metadata.get("issuer") != self.issuer:
            raise IdentityResolutionError("OIDC_DISCOVERY_ISSUER_MISMATCH")
        jwks_uri = metadata.get("jwks_uri")
        _https_url_host(jwks_uri, "OIDC_JWKS_URI_INVALID")
        advertised = metadata.get("id_token_signing_alg_values_supported")
        if (not isinstance(advertised, list) or not advertised
                or any(not isinstance(x, str) for x in advertised)
                or not (self.allowed_algorithms & set(advertised))):
            raise IdentityResolutionError("OIDC_DISCOVERY_ALGORITHM_UNSUPPORTED")
        jwks = self._fetch(jwks_uri)
        keys = jwks.get("keys")
        if not isinstance(keys, list) or not keys or len(keys) > self._max_keys:
            raise IdentityResolutionError("OIDC_JWKS_DOCUMENT_INVALID")
        seen: set[str] = set()
        for key in keys:
            if not isinstance(key, dict):
                raise IdentityResolutionError("OIDC_JWKS_DOCUMENT_INVALID")
            kid = key.get("kid")
            if not isinstance(kid, str) or not kid or len(kid) > 256 or kid in seen:
                raise IdentityResolutionError("OIDC_JWKS_KEY_ID_INVALID")
            seen.add(kid)
            if _PRIVATE_JWK_FIELDS.intersection(key):
                raise IdentityResolutionError("OIDC_PRIVATE_JWK_MATERIAL_REJECTED")
        return jwks

    def __call__(self, *, force_refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            if not force_refresh and self._jwks is not None and now < self._expires_at:
                return self._jwks
            jwks = self._load()
            self._jwks = jwks
            self._expires_at = now + self._ttl
            return jwks

    def invalidate(self) -> None:
        """Drop cached metadata/JWKS after an administrative trust-policy change."""
        with self._lock:
            self._jwks = None
            self._expires_at = 0.0
