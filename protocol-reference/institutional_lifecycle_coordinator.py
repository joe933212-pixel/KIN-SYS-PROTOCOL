# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Application coordinator for authorised institutional lifecycle transitions.

The caller supplies authenticated tenant context and a trusted actor assertion.
This reference module does not authenticate principals or cryptographically
verify evidence and authority.
"""
from __future__ import annotations

from typing import Any

from institutional_deployment_domain import (
    validate_activation, validate_binding, validate_institution,
    validate_jurisdiction_package, validate_minah_deployment,
)
from institutional_lifecycle_service import apply_transition
from institutional_authority import InstitutionalAuthorityResolver
from institutional_registry import InstitutionalRegistry

VALIDATORS = {
    "institution": validate_institution,
    "jurisdiction": validate_jurisdiction_package,
    "minah": validate_minah_deployment,
    "binding": validate_binding,
}
ID_FIELDS = {
    "institution": "institution_id",
    "jurisdiction": "package_id",
    "minah": "deployment_id",
    "binding": "binding_id",
}


class LifecycleCoordinatorError(ValueError):
    """An application-level lifecycle transition could not be completed."""


class InstitutionalLifecycleCoordinator:
    """Validate, propose and atomically persist a tenant-scoped transition."""

    def __init__(self, registry: InstitutionalRegistry, authority_resolver: InstitutionalAuthorityResolver):
        if not isinstance(authority_resolver, InstitutionalAuthorityResolver):
            raise LifecycleCoordinatorError("TRUSTED_AUTHORITY_RESOLVER_REQUIRED")
        self.registry = registry
        self.authority_resolver = authority_resolver

    def transition(
        self, *, tenant_id: str, resource_type: str, resource_id: str,
        expected_version: str, new_version: str, event_id: str,
        target_status: str, actor_assertion: dict[str, Any], evidence_ref: str,
        approval_ref: str | None = None,
        activation_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if resource_type not in VALIDATORS:
            raise LifecycleCoordinatorError("UNSUPPORTED_RESOURCE_TYPE")
        if not all(isinstance(v, str) and v.strip() for v in (
            tenant_id, resource_id, expected_version, new_version, event_id
        )):
            raise LifecycleCoordinatorError("TENANT_RESOURCE_AND_VERSION_CONTEXT_REQUIRED")

        current = self.registry.get_record(
            tenant_id=tenant_id, resource_type=resource_type,
            resource_id=resource_id, version=expected_version,
        )
        if current is None:
            raise LifecycleCoordinatorError("CURRENT_RECORD_VERSION_NOT_FOUND")
        record = current["payload"]
        if record.get(ID_FIELDS[resource_type]) != resource_id:
            raise LifecycleCoordinatorError("RECORD_IDENTITY_MISMATCH")
        if record.get("status") != current["status"]:
            raise LifecycleCoordinatorError("REGISTRY_RECORD_STATUS_MISMATCH")

        actor = self.authority_resolver.resolve(
            actor_assertion, tenant_id=tenant_id,
            resource_type=resource_type, resource_id=resource_id,
        )

        # Activation inputs are references only. Never trust caller-supplied
        # institution, jurisdiction or MINAH records: resolve the exact current
        # snapshots from this tenant's registry before evaluating the binding.
        if resource_type == "binding" and target_status == "ACTIVE":
            if not isinstance(activation_context, dict):
                raise LifecycleCoordinatorError("ACTIVATION_CONTEXT_REQUIRED")
            required = {"institution_version", "minah_version", "conformance_evidence_ref"}
            if set(activation_context) != required:
                raise LifecycleCoordinatorError("INVALID_ACTIVATION_CONTEXT")
            try:
                institution_record = self.registry.get_current_record(
                    tenant_id=tenant_id, resource_type="institution",
                    resource_id=record["owner_institution_id"],
                )
                jurisdiction_record = self.registry.get_current_record(
                    tenant_id=tenant_id, resource_type="jurisdiction",
                    resource_id=record["jurisdiction_package_id"],
                )
                minah_record = self.registry.get_current_record(
                    tenant_id=tenant_id, resource_type="minah",
                    resource_id=record["minah_deployment_id"],
                )
                if institution_record is None or institution_record["version"] != activation_context["institution_version"]:
                    raise LifecycleCoordinatorError("ACTIVATION_INSTITUTION_VERSION_NOT_CURRENT")
                if jurisdiction_record is None or jurisdiction_record["version"] != record["jurisdiction_package_version"]:
                    raise LifecycleCoordinatorError("ACTIVATION_JURISDICTION_VERSION_NOT_CURRENT")
                if minah_record is None or minah_record["version"] != activation_context["minah_version"]:
                    raise LifecycleCoordinatorError("ACTIVATION_MINAH_VERSION_NOT_CURRENT")
                for related in (institution_record, jurisdiction_record, minah_record):
                    if related["payload"].get("status") != related["status"]:
                        raise LifecycleCoordinatorError("ACTIVATION_REGISTRY_STATUS_MISMATCH")
                eligibility = validate_activation(
                    institution_record["payload"], jurisdiction_record["payload"],
                    minah_record["payload"], record,
                    conformance_evidence_ref=activation_context["conformance_evidence_ref"],
                )
            except LifecycleCoordinatorError:
                raise
            except (KeyError, TypeError, ValueError) as exc:
                raise LifecycleCoordinatorError("ACTIVATION_GATE_FAILED:" + str(exc)) from exc
            if eligibility.get("activation_eligible") is not True:
                raise LifecycleCoordinatorError("ACTIVATION_GATE_FAILED")

        proposed = apply_transition(
            resource_type, record, target_status, actor,
            evidence_ref=evidence_ref, approval_ref=approval_ref,
            expected_version=expected_version,
        )
        proposed["record"] = VALIDATORS[resource_type](proposed["record"])
        if proposed["event"].get("resource_id") != resource_id:
            raise LifecycleCoordinatorError("TRANSITION_RESOURCE_ID_MISMATCH")

        # The registry checks the expected current version and writes the new
        # immutable snapshot and audit event in one SQLite transaction.
        return self.registry.transition_record(
            tenant_id=tenant_id, resource_type=resource_type,
            resource_id=resource_id, expected_version=expected_version,
            new_version=new_version, event_id=event_id, transition=proposed,
        )
