# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Authority-gated application service for institutional OIDC provider lifecycle.

The raw registry is a persistence component, not an administrative API. This
service resolves a signed authority assertion before every mutation and derives
the audit actor from the verified grant. Callers must derive tenant context
from an authenticated institutional session, never from an untrusted body.
"""
from __future__ import annotations

from typing import Any

from institutional_identity_provider_registry import (
    InstitutionalIdentityProviderRegistry,
)


class IdentityProviderAdministrationError(ValueError):
    """A provider administration request failed authority or lifecycle policy."""


ROLE_BY_ACTION = {
    "REGISTER": "identity_provider_registrar",
    "REVISE": "identity_provider_configurator",
    "ACTIVE": "identity_provider_approver",
    "SUSPENDED": "identity_provider_suspension_authority",
    "REVOKED": "identity_provider_revocation_authority",
}


class InstitutionalIdentityProviderAdministration:
    """Gate every provider registry mutation with a tenant-scoped authority grant."""

    def __init__(self, *, registry: InstitutionalIdentityProviderRegistry,
                 authority_resolver: Any):
        if not isinstance(registry, InstitutionalIdentityProviderRegistry):
            raise IdentityProviderAdministrationError("PROVIDER_REGISTRY_REQUIRED")
        if not callable(getattr(authority_resolver, "resolve", None)):
            raise IdentityProviderAdministrationError("AUTHORITY_RESOLVER_REQUIRED")
        self._registry = registry
        self._authority = authority_resolver

    def _authorise(self, assertion: dict[str, Any], *, tenant_id: str,
                   provider_id: str, action: str) -> str:
        if action not in ROLE_BY_ACTION:
            raise IdentityProviderAdministrationError("PROVIDER_ADMIN_ACTION_UNSUPPORTED")
        try:
            principal = self._authority.resolve(
                assertion, tenant_id=tenant_id,
                resource_type="identity_provider", resource_id=provider_id,
            )
        except Exception as exc:
            raise IdentityProviderAdministrationError(
                "PROVIDER_ADMIN_AUTHORITY_REJECTED"
            ) from exc
        if not isinstance(principal, dict) or principal.get("authenticated") is not True:
            raise IdentityProviderAdministrationError("PROVIDER_ADMIN_PRINCIPAL_INVALID")
        roles = principal.get("roles")
        required = ROLE_BY_ACTION[action]
        if not isinstance(roles, list) or required not in roles:
            raise IdentityProviderAdministrationError("PROVIDER_ADMIN_ROLE_INSUFFICIENT")
        actor_ref = principal.get("actor_ref")
        if not isinstance(actor_ref, str) or not actor_ref.strip():
            raise IdentityProviderAdministrationError("PROVIDER_ADMIN_ACTOR_INVALID")
        return actor_ref

    def register_provider(self, *, tenant_id: str, config: dict[str, Any],
                          actor_assertion: dict[str, Any], event_id: str,
                          evidence_ref: str) -> dict[str, Any]:
        if not isinstance(config, dict) or config.get("tenant_id") != tenant_id:
            raise IdentityProviderAdministrationError("PROVIDER_TENANT_CONTEXT_MISMATCH")
        provider_id = config.get("provider_id")
        actor_ref = self._authorise(
            actor_assertion, tenant_id=tenant_id,
            provider_id=provider_id, action="REGISTER",
        )
        return self._registry.register_provider(
            tenant_id=tenant_id, config=config, event_id=event_id,
            actor_ref=actor_ref, evidence_ref=evidence_ref,
        )

    def revise_provider_config(self, *, tenant_id: str, provider_id: str,
                               expected_version: str, config: dict[str, Any],
                               actor_assertion: dict[str, Any], event_id: str,
                               evidence_ref: str) -> dict[str, Any]:
        actor_ref = self._authorise(
            actor_assertion, tenant_id=tenant_id,
            provider_id=provider_id, action="REVISE",
        )
        return self._registry.revise_provider_config(
            tenant_id=tenant_id, provider_id=provider_id,
            expected_version=expected_version, config=config,
            event_id=event_id, actor_ref=actor_ref, evidence_ref=evidence_ref,
        )

    def transition_provider(self, *, tenant_id: str, provider_id: str,
                            expected_version: str, new_version: str,
                            new_status: str, actor_assertion: dict[str, Any],
                            event_id: str, evidence_ref: str) -> dict[str, Any]:
        actor_ref = self._authorise(
            actor_assertion, tenant_id=tenant_id,
            provider_id=provider_id, action=new_status,
        )
        return self._registry.transition_provider(
            tenant_id=tenant_id, provider_id=provider_id,
            expected_version=expected_version, new_version=new_version,
            new_status=new_status, event_id=event_id,
            actor_ref=actor_ref, evidence_ref=evidence_ref,
        )
