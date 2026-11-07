# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Fail-closed runtime resolution for an institution's active deployment binding.

This resolver does not execute MINAH or instructions. It assembles the exact,
tenant-scoped deployment context that a governed runtime must consume before
MINAH -> Identity -> Authority -> CEE -> Final Decision.
"""
from __future__ import annotations

from typing import Any

from institutional_deployment_domain import (
    DomainModelViolation, validate_binding, validate_institution,
    validate_jurisdiction_package, validate_minah_deployment,
)
from institutional_registry import InstitutionalRegistry
from institutional_identity_provider_registry import InstitutionalIdentityProviderRegistry


class InstitutionalRuntimeBindingError(ValueError):
    """No executable institutional binding can be safely resolved."""


class InstitutionalRuntimeBindingResolver:
    """Resolve one current binding and all exact dependencies for one tenant."""

    def __init__(self, *, registry: InstitutionalRegistry,
                 minah_deployment_administration: Any,
                 identity_provider_registry: InstitutionalIdentityProviderRegistry):
        if not isinstance(registry, InstitutionalRegistry):
            raise InstitutionalRuntimeBindingError("INSTITUTIONAL_REGISTRY_REQUIRED")
        if not callable(getattr(minah_deployment_administration, "resolve_active", None)):
            raise InstitutionalRuntimeBindingError("ACTIVE_MINAH_RESOLVER_REQUIRED")
        if not isinstance(identity_provider_registry, InstitutionalIdentityProviderRegistry):
            raise InstitutionalRuntimeBindingError("INSTITUTIONAL_IDENTITY_PROVIDER_REGISTRY_REQUIRED")
        self.registry = registry
        self.minah = minah_deployment_administration
        self.identity_providers = identity_provider_registry

    def _record(self, tenant_id: str, resource_type: str,
                resource_id: str) -> dict[str, Any]:
        record = self.registry.get_current_record(
            tenant_id=tenant_id, resource_type=resource_type,
            resource_id=resource_id)
        if record is None:
            raise InstitutionalRuntimeBindingError(
                "RUNTIME_DEPENDENCY_NOT_FOUND:" + resource_type)
        return record

    def resolve(self, *, tenant_id: str, institution_id: str,
                binding_id: str) -> dict[str, Any]:
        if not all(isinstance(x, str) and x.strip()
                   for x in (tenant_id, institution_id, binding_id)):
            raise InstitutionalRuntimeBindingError("RUNTIME_TENANT_AND_RESOURCE_CONTEXT_REQUIRED")
        try:
            institution_record = self._record(tenant_id, "institution", institution_id)
            binding_record = self._record(tenant_id, "binding", binding_id)
            binding = validate_binding(binding_record["payload"])
            institution = validate_institution(institution_record["payload"])
            if institution_record["status"] != "VERIFIED" or institution["status"] != "VERIFIED":
                raise InstitutionalRuntimeBindingError("RUNTIME_INSTITUTION_NOT_VERIFIED")
            if binding_record["status"] != "ACTIVE" or binding["status"] != "ACTIVE":
                raise InstitutionalRuntimeBindingError("RUNTIME_BINDING_NOT_ACTIVE")
            if binding["owner_institution_id"] != institution_id:
                raise InstitutionalRuntimeBindingError("RUNTIME_BINDING_OWNER_MISMATCH")

            jurisdiction_record = self._record(
                tenant_id, "jurisdiction", binding["jurisdiction_package_id"])
            jurisdiction = validate_jurisdiction_package(jurisdiction_record["payload"])
            if (jurisdiction_record["status"] != "ACTIVE"
                    or jurisdiction["status"] != "ACTIVE"):
                raise InstitutionalRuntimeBindingError("RUNTIME_JURISDICTION_NOT_ACTIVE")
            if jurisdiction_record["version"] != binding["jurisdiction_package_version"]:
                raise InstitutionalRuntimeBindingError("RUNTIME_JURISDICTION_VERSION_STALE")
            if jurisdiction["owner_institution_id"] != institution_id:
                raise InstitutionalRuntimeBindingError("RUNTIME_JURISDICTION_OWNER_MISMATCH")
            if jurisdiction["jurisdiction_ref"] != institution["jurisdiction_ref"]:
                raise InstitutionalRuntimeBindingError("RUNTIME_JURISDICTION_SCOPE_MISMATCH")

            try:
                identity_provider = self.identity_providers.resolve_active_provider(
                    tenant_id, binding["identity_provider_id"])
            except Exception as exc:
                raise InstitutionalRuntimeBindingError(
                    "RUNTIME_IDENTITY_PROVIDER_UNAVAILABLE") from exc
            if not isinstance(identity_provider, dict):
                raise InstitutionalRuntimeBindingError(
                    "RUNTIME_IDENTITY_PROVIDER_NOT_ACTIVE")
            if (identity_provider.get("tenant_id") != tenant_id
                    or identity_provider.get("provider_id") != binding["identity_provider_id"]):
                raise InstitutionalRuntimeBindingError(
                    "RUNTIME_IDENTITY_PROVIDER_TENANT_MISMATCH")
            if identity_provider.get("version") != binding["identity_provider_version"]:
                raise InstitutionalRuntimeBindingError(
                    "RUNTIME_IDENTITY_PROVIDER_VERSION_STALE")

            minah_record = self._record(
                tenant_id, "minah", binding["minah_deployment_id"])
            minah_domain = validate_minah_deployment(minah_record["payload"])
            if minah_record["status"] != "ACTIVE" or minah_domain["status"] != "ACTIVE":
                raise InstitutionalRuntimeBindingError("RUNTIME_MINAH_DOMAIN_NOT_ACTIVE")
            if minah_domain["owner_institution_id"] != institution_id:
                raise InstitutionalRuntimeBindingError("RUNTIME_MINAH_OWNER_MISMATCH")

            active_minah = self.minah.resolve_active(
                tenant_id=tenant_id, deployment_id=binding["minah_deployment_id"])
            if not isinstance(active_minah, dict):
                raise InstitutionalRuntimeBindingError("RUNTIME_MINAH_DEPLOYMENT_UNAVAILABLE")
            config = active_minah.get("config")
            if not isinstance(config, dict):
                raise InstitutionalRuntimeBindingError("RUNTIME_MINAH_CONFIGURATION_INVALID")
            if active_minah.get("status") != "ACTIVE":
                raise InstitutionalRuntimeBindingError("RUNTIME_MINAH_DEPLOYMENT_NOT_ACTIVE")
            pins = {
                "model_ref": minah_domain["model_ref"],
                "interface_version": minah_domain["interface_version"],
                "jurisdiction_ref": jurisdiction["jurisdiction_ref"],
                "jurisdiction_package_id": jurisdiction["package_id"],
                "jurisdiction_package_version": jurisdiction["version"],
                "policy_pack_version": binding["policy_pack_version"],
                "source_pack_version": binding["source_pack_version"],
                "authority_profile_version": binding["authority_profile_version"],
            }
            for field, expected in pins.items():
                if config.get(field) != expected:
                    raise InstitutionalRuntimeBindingError(
                        "RUNTIME_CONFIGURATION_PIN_MISMATCH:" + field)
            if config.get("deployment_id") != binding["minah_deployment_id"]:
                raise InstitutionalRuntimeBindingError("RUNTIME_MINAH_DEPLOYMENT_ID_MISMATCH")
            if config.get("owner_ref") != tenant_id:
                raise InstitutionalRuntimeBindingError("RUNTIME_MINAH_TENANT_MISMATCH")
            return {
                "tenant_id": tenant_id,
                "institution_id": institution_id,
                "institution_version": institution_record["version"],
                "binding_id": binding_id,
                "binding_version": binding_record["version"],
                "jurisdiction_package_id": jurisdiction["package_id"],
                "jurisdiction_package_version": jurisdiction["version"],
                "minah_deployment_id": binding["minah_deployment_id"],
                "identity_provider_id": identity_provider["provider_id"],
                "identity_provider_version": identity_provider["version"],
                "identity_provider_config": dict(identity_provider),
                "minah_domain_version": minah_record["version"],
                "minah_runtime_version": active_minah.get("version"),
                "minah_config": dict(config),
                "policy_pack_version": binding["policy_pack_version"],
                "source_pack_version": binding["source_pack_version"],
                "authority_profile_version": binding["authority_profile_version"],
                "runtime_admission": "ELIGIBLE_FOR_GOVERNED_EXECUTION",
                "execution_order": ["MINAH", "IDENTITY", "AUTHORITY", "CEE", "FINAL_DECISION"],
            }
        except DomainModelViolation as exc:
            raise InstitutionalRuntimeBindingError("RUNTIME_DOMAIN_RECORD_INVALID") from exc
