# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""OIDC ID-token adapter with receiver-pinned issuer and injected JWKS source.

The adapter never discovers or fetches a URL supplied by a credential. Provider
metadata/JWKS retrieval, TLS, cache lifetime and key-rotation policy are supplied
by the institution-controlled jwks_loader. Raw tokens and claims are never logged.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Callable

from institutional_identity import IdentityResolutionError, VerifiedIdentityAssertion


class OIDCIdentityProviderAdapter:
    """Validate an OIDC ID token for one receiver-configured issuer/client.

    jwks_loader(force_refresh: bool) must return the configured issuer's JWKS
    document (a mapping with a 'keys' list). It must enforce HTTPS, pinned issuer
    metadata, bounded timeouts, safe redirects, cache policy and response limits.
    """

    ALLOWED_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "ES256", "ES384", "EdDSA"})

    def __init__(
        self, *, issuer: str, client_id: str,
        jwks_loader: Callable[..., dict[str, Any]],
        allowed_algorithms: tuple[str, ...] = ("RS256", "ES256", "EdDSA"),
        clock: Callable[[], float] = time.time,
        leeway_seconds: int = 0,
        max_token_ttl_seconds: int = 600,
    ):
        if not isinstance(issuer, str) or not issuer.startswith("https://") or len(issuer) > 2048:
            raise IdentityResolutionError("OIDC_ISSUER_CONFIGURATION_INVALID")
        if not isinstance(client_id, str) or not client_id or len(client_id) > 512:
            raise IdentityResolutionError("OIDC_CLIENT_CONFIGURATION_INVALID")
        if not callable(jwks_loader) or not callable(clock):
            raise IdentityResolutionError("OIDC_ADAPTER_CONFIGURATION_INCOMPLETE")
        if (not isinstance(allowed_algorithms, tuple) or not allowed_algorithms
                or any(a not in self.ALLOWED_ALGORITHMS for a in allowed_algorithms)
                or len(set(allowed_algorithms)) != len(allowed_algorithms)):
            raise IdentityResolutionError("OIDC_ALGORITHM_POLICY_INVALID")
        if type(leeway_seconds) is not int or not 0 <= leeway_seconds <= 60:
            raise IdentityResolutionError("OIDC_LEEWAY_INVALID")
        if type(max_token_ttl_seconds) is not int or not 1 <= max_token_ttl_seconds <= 3600:
            raise IdentityResolutionError("OIDC_TOKEN_TTL_POLICY_INVALID")
        self.issuer = issuer
        self.client_id = client_id
        self._jwks_loader = jwks_loader
        self._algorithms = allowed_algorithms
        self._clock = clock
        self._leeway = leeway_seconds
        self._max_ttl = max_token_ttl_seconds

    def _load_signing_key(self, kid: str, alg: str):
        try:
            import jwt
        except ImportError as exc:
            raise IdentityResolutionError("OIDC_CRYPTO_DEPENDENCY_MISSING") from exc

        # One forced refresh is allowed for an unknown kid to support rotation.
        for force_refresh in (False, True):
            try:
                document = self._jwks_loader(force_refresh=force_refresh)
            except Exception as exc:
                raise IdentityResolutionError("OIDC_JWKS_UNAVAILABLE") from exc
            if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
                raise IdentityResolutionError("OIDC_JWKS_INVALID")
            matches = [item for item in document["keys"]
                       if isinstance(item, dict) and item.get("kid") == kid]
            if len(matches) > 1:
                raise IdentityResolutionError("OIDC_JWKS_DUPLICATE_KEY_ID")
            if not matches:
                continue
            jwk = matches[0]
            if jwk.get("use", "sig") != "sig" or jwk.get("alg", alg) != alg:
                raise IdentityResolutionError("OIDC_SIGNING_KEY_POLICY_MISMATCH")
            try:
                return jwt.PyJWK.from_dict(jwk, algorithm=alg).key
            except Exception as exc:
                raise IdentityResolutionError("OIDC_SIGNING_KEY_INVALID") from exc
        raise IdentityResolutionError("OIDC_SIGNING_KEY_NOT_FOUND")

    def verify_credential(
        self, credential: Any, *, tenant_id: str, expected_audience: str,
        expected_nonce: str,
    ) -> VerifiedIdentityAssertion:
        if not isinstance(credential, str) or not credential or len(credential) > 65536:
            raise IdentityResolutionError("OIDC_ID_TOKEN_INVALID")
        try:
            import jwt
        except ImportError as exc:
            raise IdentityResolutionError("OIDC_CRYPTO_DEPENDENCY_MISSING") from exc
        try:
            header = jwt.get_unverified_header(credential)
        except Exception as exc:
            raise IdentityResolutionError("OIDC_ID_TOKEN_INVALID") from exc

        alg, kid = header.get("alg"), header.get("kid")
        if alg not in self._algorithms or not isinstance(kid, str) or not kid or len(kid) > 256:
            raise IdentityResolutionError("OIDC_SIGNING_HEADER_REJECTED")
        key = self._load_signing_key(kid, alg)
        try:
            claims = jwt.decode(
                credential, key=key, algorithms=[alg], issuer=self.issuer,
                audience=self.client_id, leeway=self._leeway,
                options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce",
                                     "auth_time", "acr", "amr"],
                         "verify_signature": True, "verify_exp": True,
                         "verify_iat": True, "verify_nbf": True,
                         "verify_iss": True, "verify_aud": True},
            )
        except Exception as exc:
            raise IdentityResolutionError("OIDC_ID_TOKEN_REJECTED") from exc

        # OIDC requires azp when multiple audiences are present; when supplied,
        # it must identify this client even for a single audience.
        audiences = claims.get("aud")
        if isinstance(audiences, list) and len(audiences) > 1 and claims.get("azp") != self.client_id:
            raise IdentityResolutionError("OIDC_AUTHORIZED_PARTY_MISMATCH")
        if "azp" in claims and claims["azp"] != self.client_id:
            raise IdentityResolutionError("OIDC_AUTHORIZED_PARTY_MISMATCH")
        now = int(self._clock())
        issued_at, expires_at, auth_time = (
            claims.get("iat"), claims.get("exp"), claims.get("auth_time")
        )
        if any(type(value) is not int for value in (issued_at, expires_at, auth_time)):
            raise IdentityResolutionError("OIDC_TIME_CLAIMS_INVALID")
        if (issued_at > now + self._leeway or expires_at <= issued_at
                or expires_at - issued_at > self._max_ttl or auth_time > now + self._leeway):
            raise IdentityResolutionError("OIDC_TIME_CLAIMS_INVALID")
        subject = claims.get("sub")
        acr = claims.get("acr")
        amr = claims.get("amr")
        nonce = claims.get("nonce")
        if (not isinstance(subject, str) or not subject or len(subject) > 2048
                or any(ord(ch) < 0x20 for ch in subject)
                or not isinstance(acr, str) or not acr or len(acr) > 2048
                or not isinstance(amr, list) or not amr
                or any(not isinstance(x, str) or not x or len(x) > 64 for x in amr)
                or len(set(amr)) != len(amr)
                or nonce != expected_nonce):
            raise IdentityResolutionError("OIDC_REQUIRED_CLAIMS_INVALID")

        return VerifiedIdentityAssertion(
            issuer=self.issuer, subject=subject, tenant_id=tenant_id,
            audience=expected_audience, issued_at=issued_at,
            expires_at=expires_at, auth_time=auth_time, acr=acr,
            amr=tuple(amr), nonce=nonce,
            credential_ref="sha256:" + hashlib.sha256(credential.encode("utf-8")).hexdigest(),
        )
