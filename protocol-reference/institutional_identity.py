# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Institution-controlled identity-provider adapter and principal resolution.

Protocol-specific cryptographic validation (OIDC/JWT, SAML, mTLS, passkeys or
an institutional gateway) belongs in a vetted provider adapter. This module
accepts only the adapter's typed verified result, applies receiver-side issuer,
audience, assurance and tenant policy, and maps the external subject to an
internal actor. Identity authentication never grants an authority role.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Any, Callable, Protocol, runtime_checkable


class IdentityResolutionError(ValueError):
    """An identity credential or institutional identity policy was rejected."""


IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")
ALLOWED_AMR = {"pwd", "otp", "hwk", "swk", "mfa", "fido", "x509", "sms", "face", "iris"}


@dataclass(frozen=True)
class VerifiedIdentityAssertion:
    """Claims emitted only after a configured provider adapter verifies evidence.

    Adapters must validate the native protocol signature/MAC, issuer, audience,
    nonce, token status/revocation and protocol-specific semantics before
    constructing this value. The resolver independently applies local policy.
    """
    issuer: str
    subject: str
    tenant_id: str
    audience: str
    issued_at: int
    expires_at: int
    auth_time: int
    acr: str
    amr: tuple[str, ...]
    nonce: str
    credential_ref: str


@runtime_checkable
class IdentityProviderAdapter(Protocol):
    """Trusted adapter for one configured institutional identity provider."""

    def verify_credential(
        self, credential: Any, *, tenant_id: str, provider_id: str,
        expected_audience: str, expected_nonce: str,
    ) -> VerifiedIdentityAssertion:
        """Verify native credential and return typed, verified identity claims."""
        ...


class InstitutionalIdentityResolver:
    """Resolve an authenticated external subject to a tenant-local actor.

    issuer_policy_loader returns receiver-governed policy:
      {status, audiences, allowed_acr, required_amr, valid_from, valid_until}
    subject_binding_loader returns the current tenant-local binding:
      {actor_ref, status, binding_version}

    Neither external roles nor provider group claims are imported as KIN-SYS
    authority. Delegated roles/scopes are resolved separately from grants.
    """
    def __init__(
        self, *,
        adapter_loader: Callable[[str, str], IdentityProviderAdapter | None],
        issuer_policy_loader: Callable[[str, str], dict[str, Any] | None],
        subject_binding_loader: Callable[[str, str, str], dict[str, Any] | None],
        consume_nonce: Callable[..., bool],
        clock: Callable[[], float] = time.time,
        max_credential_ttl_seconds: int = 600,
        max_authentication_age_seconds: int = 300,
    ):
        if not all(callable(x) for x in (
            adapter_loader, issuer_policy_loader, subject_binding_loader, consume_nonce
        )):
            raise IdentityResolutionError("IDENTITY_CONFIGURATION_INCOMPLETE")
        if (type(max_credential_ttl_seconds) is not int or not 1 <= max_credential_ttl_seconds <= 3600
                or type(max_authentication_age_seconds) is not int
                or not 1 <= max_authentication_age_seconds <= 3600):
            raise IdentityResolutionError("INVALID_IDENTITY_TIME_POLICY")
        self._adapter_loader = adapter_loader
        self._issuer_policy_loader = issuer_policy_loader
        self._subject_binding_loader = subject_binding_loader
        self._consume_nonce = consume_nonce
        self._clock = clock
        self._max_ttl = max_credential_ttl_seconds
        self._max_auth_age = max_authentication_age_seconds

    @staticmethod
    def _identifier(value: Any, code: str) -> str:
        if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
            raise IdentityResolutionError(code)
        return value

    def authenticate(
        self, credential: Any, *, tenant_id: str, expected_audience: str,
        expected_nonce: str,
    ) -> dict[str, Any]:
        tenant_id = self._identifier(tenant_id, "TENANT_CONTEXT_REQUIRED")
        provider_id = self._identifier(provider_id, "IDENTITY_PROVIDER_ID_REQUIRED")
        expected_audience = self._identifier(expected_audience, "IDENTITY_AUDIENCE_REQUIRED")
        expected_nonce = self._identifier(expected_nonce, "IDENTITY_NONCE_REQUIRED")
        if credential is None:
            raise IdentityResolutionError("IDENTITY_CREDENTIAL_REQUIRED")

        # Provider selection must come from receiver-controlled configuration,
        # not from an untrusted issuer field in the credential.
        adapter = self._adapter_loader(tenant_id, provider_id)
        if not isinstance(adapter, IdentityProviderAdapter):
            raise IdentityResolutionError("IDENTITY_PROVIDER_NOT_CONFIGURED")
        try:
            assertion = adapter.verify_credential(
                credential, tenant_id=tenant_id,
                expected_audience=expected_audience, expected_nonce=expected_nonce,
            )
        except IdentityResolutionError:
            raise
        except Exception as exc:
            raise IdentityResolutionError("IDENTITY_CREDENTIAL_VERIFICATION_FAILED") from exc
        if not isinstance(assertion, VerifiedIdentityAssertion):
            raise IdentityResolutionError("UNVERIFIED_IDENTITY_ADAPTER_RESULT")

        issuer = self._identifier(assertion.issuer, "IDENTITY_ISSUER_INVALID")
        subject = self._identifier(assertion.subject, "IDENTITY_SUBJECT_INVALID")
        if assertion.tenant_id != tenant_id:
            raise IdentityResolutionError("IDENTITY_TENANT_MISMATCH")
        if assertion.audience != expected_audience:
            raise IdentityResolutionError("IDENTITY_AUDIENCE_MISMATCH")
        if assertion.nonce != expected_nonce:
            raise IdentityResolutionError("IDENTITY_NONCE_MISMATCH")
        self._identifier(assertion.credential_ref, "IDENTITY_CREDENTIAL_REFERENCE_INVALID")
        self._identifier(assertion.acr, "IDENTITY_ASSURANCE_LEVEL_INVALID")

        now = int(self._clock())
        for field in ("issued_at", "expires_at", "auth_time"):
            if type(getattr(assertion, field)) is not int:
                raise IdentityResolutionError("IDENTITY_TIME_CLAIMS_INVALID")
        if (assertion.issued_at > now or assertion.expires_at <= now
                or assertion.expires_at <= assertion.issued_at
                or assertion.expires_at - assertion.issued_at > self._max_ttl
                or assertion.auth_time > now
                or now - assertion.auth_time > self._max_auth_age
                or assertion.auth_time < assertion.issued_at - self._max_ttl):
            raise IdentityResolutionError("IDENTITY_CREDENTIAL_EXPIRED_OR_STALE")

        policy = self._issuer_policy_loader(tenant_id, issuer)
        if not isinstance(policy, dict) or policy.get("status") != "ACTIVE":
            raise IdentityResolutionError("IDENTITY_ISSUER_NOT_TRUSTED")
        if policy.get("issuer") != issuer:
            raise IdentityResolutionError("IDENTITY_ISSUER_POLICY_MISMATCH")
        if (type(policy.get("valid_from")) is not int
                or type(policy.get("valid_until")) is not int
                or not policy["valid_from"] <= now < policy["valid_until"]):
            raise IdentityResolutionError("IDENTITY_ISSUER_POLICY_OUTSIDE_VALIDITY")
        if expected_audience not in policy.get("audiences", []):
            raise IdentityResolutionError("IDENTITY_AUDIENCE_NOT_ALLOWED")
        if assertion.acr not in policy.get("allowed_acr", []):
            raise IdentityResolutionError("IDENTITY_ASSURANCE_INSUFFICIENT")
        if not isinstance(assertion.amr, tuple) or not assertion.amr:
            raise IdentityResolutionError("IDENTITY_AUTHENTICATION_METHOD_REQUIRED")
        if any(method not in ALLOWED_AMR for method in assertion.amr):
            raise IdentityResolutionError("IDENTITY_AUTHENTICATION_METHOD_INVALID")
        required_amr = policy.get("required_amr", [])
        if not isinstance(required_amr, list) or not set(required_amr).issubset(set(assertion.amr)):
            raise IdentityResolutionError("IDENTITY_AUTHENTICATION_METHOD_INSUFFICIENT")

        binding = self._subject_binding_loader(tenant_id, issuer, subject)
        if not isinstance(binding, dict) or binding.get("status") != "ACTIVE":
            raise IdentityResolutionError("IDENTITY_SUBJECT_NOT_BOUND")
        actor_ref = self._identifier(binding.get("actor_ref"), "IDENTITY_ACTOR_BINDING_INVALID")
        version = self._identifier(binding.get("binding_version"), "IDENTITY_BINDING_VERSION_INVALID")

        if self._consume_nonce(tenant_id, "identity:" + expected_nonce,
                               expires_at=assertion.expires_at) is not True:
            raise IdentityResolutionError("IDENTITY_ASSERTION_REPLAY_DETECTED")

        # Authentication establishes who is present, not what they may do.
        return {
            "authenticated": True,
            "verified": True,
            "tenant_id": tenant_id,
            "actor_ref": actor_ref,
            "identity_issuer": issuer,
            "identity_subject": subject,
            "identity_binding_version": version,
            "identity_assurance_level": assertion.acr,
            "authentication_methods": list(assertion.amr),
            "authenticated_at": assertion.auth_time,
            "credential_ref": assertion.credential_ref,
            "roles": [],
            "authority_grant_required": True,
        }
