# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Authenticated lifecycle administration with a trusted evidence-verification boundary.

The injected verifier must resolve evidence references against an authoritative
institutional evidence service. A non-empty reference alone is never accepted
as proof. Verification receipts are reduced to stable identifiers and digests
and persisted with the lifecycle event in the same registry transaction.
"""
from __future__ import annotations

import re
from typing import Any

from institutional_authority import InstitutionalAuthorityResolver
from institutional_lifecycle_coordinator import (
    InstitutionalLifecycleCoordinator, LifecycleCoordinatorError,
)
from institutional_registry import InstitutionalRegistry

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


class InstitutionalLifecycleAdministrationError(ValueError):
    """An authenticated institutional lifecycle operation was rejected."""


class InstitutionalLifecycleAdministration:
    """Verify evidence, resolve authority and coordinate atomic lifecycle changes."""

    def __init__(self, registry: InstitutionalRegistry,
                 authority_resolver: InstitutionalAuthorityResolver,
                 evidence_verifier: Any):
        if not isinstance(registry, InstitutionalRegistry):
            raise InstitutionalLifecycleAdministrationError("INSTITUTIONAL_REGISTRY_REQUIRED")
        if not isinstance(authority_resolver, InstitutionalAuthorityResolver):
            raise InstitutionalLifecycleAdministrationError("TRUSTED_AUTHORITY_RESOLVER_REQUIRED")
        if not callable(getattr(evidence_verifier, "verify", None)):
            raise InstitutionalLifecycleAdministrationError("TRUSTED_EVIDENCE_VERIFIER_REQUIRED")
        self.registry = registry
        self.coordinator = InstitutionalLifecycleCoordinator(registry, authority_resolver)
        self.evidence_verifier = evidence_verifier

    def _verify(self, evidence_ref: str, *, tenant_id: str, resource_type: str,
                resource_id: str, purpose: str, expected_version: str) -> dict[str, str]:
        if not isinstance(evidence_ref, str) or not evidence_ref.strip():
            raise InstitutionalLifecycleAdministrationError("TRANSITION_EVIDENCE_REQUIRED")
        context = {
            "tenant_id": tenant_id, "resource_type": resource_type,
            "resource_id": resource_id, "purpose": purpose,
            "expected_version": expected_version,
        }
        try:
            result = self.evidence_verifier.verify(evidence_ref, context=context)
        except Exception as exc:
            raise InstitutionalLifecycleAdministrationError("EVIDENCE_VERIFICATION_FAILED") from exc
        if not isinstance(result, dict) or result.get("verified") is not True:
            raise InstitutionalLifecycleAdministrationError("EVIDENCE_NOT_VERIFIED")
        if result.get("evidence_ref") != evidence_ref:
            raise InstitutionalLifecycleAdministrationError("EVIDENCE_REFERENCE_MISMATCH")
        verification_ref = result.get("verification_ref")
        digest = result.get("evidence_digest")
        if not isinstance(verification_ref, str) or not verification_ref.strip():
            raise InstitutionalLifecycleAdministrationError("EVIDENCE_VERIFICATION_RECEIPT_INVALID")
        if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
            raise InstitutionalLifecycleAdministrationError("EVIDENCE_DIGEST_INVALID")
        return {"verification_ref": verification_ref, "evidence_digest": digest}

    def transition(self, *, tenant_id: str, resource_type: str,
                   resource_id: str, expected_version: str, new_version: str,
                   event_id: str, target_status: str,
                   actor_assertion: dict[str, Any], evidence_ref: str,
                   approval_ref: str | None = None,
                   activation_context: dict[str, Any] | None = None) -> dict[str, Any]:
        base_context = {
            "tenant_id": tenant_id, "resource_type": resource_type,
            "resource_id": resource_id, "expected_version": expected_version,
        }
        evidence_receipt = self._verify(
            evidence_ref, **base_context, purpose="LIFECYCLE_TRANSITION")
        approval_receipt = None
        if approval_ref is not None:
            approval_receipt = self._verify(
                approval_ref, **base_context, purpose="LIFECYCLE_APPROVAL")
        conformance_receipt = None
        if resource_type == "binding" and target_status == "ACTIVE":
            if not isinstance(activation_context, dict):
                raise InstitutionalLifecycleAdministrationError("ACTIVATION_CONTEXT_REQUIRED")
            conformance_ref = activation_context.get("conformance_evidence_ref")
            conformance_receipt = self._verify(
                conformance_ref, **base_context, purpose="MINAH_CONFORMANCE")
        verification_bundle = {"evidence": evidence_receipt}
        if approval_receipt is not None:
            verification_bundle["approval"] = approval_receipt
        return self.coordinator.transition(
            tenant_id=tenant_id, resource_type=resource_type,
            resource_id=resource_id, expected_version=expected_version,
            new_version=new_version, event_id=event_id,
            target_status=target_status, actor_assertion=actor_assertion,
            evidence_ref=evidence_ref, approval_ref=approval_ref,
            activation_context=activation_context,
            evidence_verification=verification_bundle,
            conformance_evidence_verification=conformance_receipt,
        )
