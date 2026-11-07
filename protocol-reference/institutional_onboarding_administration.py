# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Authenticated application facade for institutional domain onboarding.

Authentication and actor assertions are injected from the trusted session
boundary. This facade enforces tenant scope, registration roles, initial
lifecycle states, domain validation and atomic record/audit creation.
It does not authenticate people. Registration evidence must be resolved by an injected
trusted verifier; a caller-supplied non-empty reference alone is never proof.
"""
from __future__ import annotations

import re
from typing import Any

from institutional_deployment_domain import (
    validate_binding, validate_institution, validate_jurisdiction_package,
    validate_minah_deployment,
)
from institutional_registry import InstitutionalRegistry, RegistryError

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
INITIAL_STATES = {
    "institution": "PENDING_VERIFICATION",
    "jurisdiction": "DRAFT",
    "minah": "REGISTERED",
    "binding": "DRAFT",
}
REGISTRAR_ROLES = {
    "institution": "institution_registrar",
    "jurisdiction": "jurisdiction_registrar",
    "minah": "minah_deployment_registrar",
    "binding": "binding_registrar",
}


class InstitutionalOnboardingError(ValueError):
    """A tenant-scoped institutional onboarding operation was rejected."""


class InstitutionalOnboardingAdministration:
    """Register and retrieve immutable institutional domain records."""

    _DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")

    def __init__(self, registry: InstitutionalRegistry, authority_resolver: Any,
                 evidence_verifier: Any):
        if not isinstance(registry, InstitutionalRegistry):
            raise InstitutionalOnboardingError("INSTITUTIONAL_REGISTRY_REQUIRED")
        if not callable(getattr(authority_resolver, "resolve", None)):
            raise InstitutionalOnboardingError("TRUSTED_AUTHORITY_RESOLVER_REQUIRED")
        if not callable(getattr(evidence_verifier, "verify", None)):
            raise InstitutionalOnboardingError("TRUSTED_EVIDENCE_VERIFIER_REQUIRED")
        self.registry = registry
        self.authority_resolver = authority_resolver
        self.evidence_verifier = evidence_verifier

    def _verify_registration_evidence(self, evidence_ref: str, *,
                                      tenant_id: str, resource_type: str,
                                      resource_id: str, version: str) -> dict[str, str]:
        context = {
            "tenant_id": tenant_id, "resource_type": resource_type,
            "resource_id": resource_id, "purpose": "INSTITUTIONAL_REGISTRATION",
            "expected_version": version,
        }
        try:
            result = self.evidence_verifier.verify(evidence_ref, context=context)
        except Exception as exc:
            raise InstitutionalOnboardingError("REGISTRATION_EVIDENCE_VERIFICATION_FAILED") from exc
        if not isinstance(result, dict) or result.get("verified") is not True:
            raise InstitutionalOnboardingError("REGISTRATION_EVIDENCE_NOT_VERIFIED")
        if result.get("evidence_ref") != evidence_ref:
            raise InstitutionalOnboardingError("REGISTRATION_EVIDENCE_REFERENCE_MISMATCH")
        verification_ref = result.get("verification_ref")
        digest = result.get("evidence_digest")
        if not isinstance(verification_ref, str) or not verification_ref.strip():
            raise InstitutionalOnboardingError("REGISTRATION_EVIDENCE_RECEIPT_INVALID")
        if not isinstance(digest, str) or not self._DIGEST_RE.fullmatch(digest):
            raise InstitutionalOnboardingError("REGISTRATION_EVIDENCE_DIGEST_INVALID")
        return {"verification_ref": verification_ref, "evidence_digest": digest}

    def register(self, *, tenant_id: str, resource_type: str,
                 version: str, event_id: str, record: dict[str, Any],
                 actor_assertion: dict[str, Any], evidence_ref: str) -> dict[str, Any]:
        if resource_type not in VALIDATORS:
            raise InstitutionalOnboardingError("UNSUPPORTED_RESOURCE_TYPE")
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise InstitutionalOnboardingError("TENANT_CONTEXT_REQUIRED")
        if not isinstance(version, str) or not version.strip():
            raise InstitutionalOnboardingError("INITIAL_VERSION_REQUIRED")
        if not isinstance(event_id, str) or not event_id.strip():
            raise InstitutionalOnboardingError("REGISTRATION_EVENT_ID_REQUIRED")
        if not isinstance(evidence_ref, str) or not evidence_ref.strip():
            raise InstitutionalOnboardingError("REGISTRATION_EVIDENCE_REQUIRED")
        try:
            clean = VALIDATORS[resource_type](record)
        except (TypeError, ValueError) as exc:
            raise InstitutionalOnboardingError("DOMAIN_RECORD_INVALID:" + str(exc)) from exc
        resource_id = clean.get(ID_FIELDS[resource_type])
        if not isinstance(resource_id, str) or not resource_id.strip():
            raise InstitutionalOnboardingError("RESOURCE_ID_REQUIRED")
        if clean.get("status") != INITIAL_STATES[resource_type]:
            raise InstitutionalOnboardingError("INVALID_INITIAL_LIFECYCLE_STATE")
        owner_institution_id = (
            clean.get("institution_id") if resource_type == "institution"
            else clean.get("owner_institution_id")
        )
        if not isinstance(owner_institution_id, str) or not owner_institution_id.strip():
            raise InstitutionalOnboardingError("RESOURCE_OWNER_INSTITUTION_REQUIRED")
        if resource_type != "institution":
            owner = self.registry.get_current_record(
                tenant_id=tenant_id, resource_type="institution",
                resource_id=owner_institution_id,
            )
            if owner is None:
                raise InstitutionalOnboardingError("RESOURCE_OWNER_INSTITUTION_NOT_REGISTERED")
            if owner["payload"].get("institution_id") != owner_institution_id:
                raise InstitutionalOnboardingError("RESOURCE_OWNER_INSTITUTION_MISMATCH")
            if owner["status"] in {"SUSPENDED", "REVOKED"}:
                raise InstitutionalOnboardingError("RESOURCE_OWNER_INSTITUTION_NOT_ELIGIBLE")
        try:
            actor = self.authority_resolver.resolve(
                actor_assertion, tenant_id=tenant_id,
                resource_type=resource_type, resource_id=resource_id,
            )
        except Exception as exc:
            # Do not disclose resolver internals, grant data or cryptographic details.
            raise InstitutionalOnboardingError("REGISTRATION_AUTHORITY_REJECTED") from exc
        if not isinstance(actor, dict) or actor.get("authenticated") is not True or actor.get("verified") is not True:
            raise InstitutionalOnboardingError("VERIFIED_REGISTRATION_ACTOR_REQUIRED")
        if actor.get("institution_id") != owner_institution_id:
            raise InstitutionalOnboardingError("REGISTRATION_ACTOR_INSTITUTION_MISMATCH")
        if REGISTRAR_ROLES[resource_type] not in actor.get("roles", []):
            raise InstitutionalOnboardingError("REGISTRATION_ROLE_REQUIRED")
        evidence_receipt = self._verify_registration_evidence(
            evidence_ref, tenant_id=tenant_id, resource_type=resource_type,
            resource_id=resource_id, version=version,
        )
        result = self.registry.create_record_with_event(
            tenant_id=tenant_id, resource_type=resource_type,
            resource_id=resource_id, version=version,
            status=clean["status"], payload=clean, event_id=event_id,
            event_type=resource_type.upper() + "_REGISTERED",
            actor_ref=actor["actor_ref"], evidence_ref=evidence_ref,
            event_payload={"registration_status": clean["status"],
                           "authority_ref": actor["authority_ref"],
                           "grant_ref": actor["grant_ref"],
                           "evidence_verification": evidence_receipt},
        )
        return {"resource_type": resource_type, "resource_id": resource_id,
                "version": version, "status": clean["status"],
                "payload_digest": result["payload_digest"],
                "created": result["created"], "event_id": event_id}

    def get(self, *, tenant_id: str, resource_type: str,
            resource_id: str) -> dict[str, Any] | None:
        if resource_type not in VALIDATORS:
            raise InstitutionalOnboardingError("UNSUPPORTED_RESOURCE_TYPE")
        return self.registry.get_current_record(
            tenant_id=tenant_id, resource_type=resource_type,
            resource_id=resource_id,
        )

    def list(self, *, tenant_id: str, resource_type: str,
             resource_id: str | None = None) -> list[dict[str, Any]]:
        if resource_type not in VALIDATORS:
            raise InstitutionalOnboardingError("UNSUPPORTED_RESOURCE_TYPE")
        return self.registry.list_records(
            tenant_id=tenant_id, resource_type=resource_type,
            resource_id=resource_id,
        )

    def events(self, *, tenant_id: str, resource_type: str,
               resource_id: str) -> list[dict[str, Any]]:
        if resource_type not in VALIDATORS:
            raise InstitutionalOnboardingError("UNSUPPORTED_RESOURCE_TYPE")
        return self.registry.list_events(
            tenant_id=tenant_id, resource_type=resource_type,
            resource_id=resource_id,
        )
