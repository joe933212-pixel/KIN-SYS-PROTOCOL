# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Authorised lifecycle transition policy for institutional deployment records.

Reference policy only. Actor assertions must come from a trusted
authentication/authority layer; this module does not authenticate identities,
verify signatures, or establish legal authority.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

ROLE_REQUIREMENTS = {
    ("institution", "VERIFIED"): "institution_verifier",
    ("jurisdiction", "SUBMITTED"): "jurisdiction_submitter",
    ("jurisdiction", "VALIDATED"): "jurisdiction_reviewer",
    ("jurisdiction", "APPROVED"): "jurisdiction_approver",
    ("jurisdiction", "ACTIVE"): "jurisdiction_activation_authority",
    ("jurisdiction", "RETIRED"): "jurisdiction_retirement_authority",
    ("minah", "CONNECTIVITY_VERIFIED"): "minah_connectivity_operator",
    ("minah", "CONFORMANCE_PASSED"): "minah_conformance_assessor",
    ("minah", "ACTIVE"): "minah_activation_authority",
    ("binding", "VALIDATED"): "binding_reviewer",
    ("binding", "APPROVED"): "binding_approver",
    ("binding", "ACTIVE"): "binding_activation_authority",
}
TRANSITIONS = {
    "institution": {
        "PENDING_VERIFICATION": {"VERIFIED", "SUSPENDED", "REVOKED"},
        "VERIFIED": {"SUSPENDED", "REVOKED"},
        "SUSPENDED": {"VERIFIED", "REVOKED"},
        "REVOKED": set(),
    },
    "jurisdiction": {
        "DRAFT": {"SUBMITTED", "RETIRED"},
        "SUBMITTED": {"VALIDATED", "RETIRED"},
        "VALIDATED": {"APPROVED", "RETIRED"},
        "APPROVED": {"ACTIVE", "RETIRED"},
        "ACTIVE": {"RETIRED"},
        "RETIRED": set(),
    },
    "minah": {
        "REGISTERED": {"CONNECTIVITY_VERIFIED", "SUSPENDED", "REVOKED"},
        "CONNECTIVITY_VERIFIED": {"CONFORMANCE_PASSED", "SUSPENDED", "REVOKED"},
        "CONFORMANCE_PASSED": {"ACTIVE", "SUSPENDED", "REVOKED"},
        "ACTIVE": {"SUSPENDED", "REVOKED"},
        "SUSPENDED": {"CONNECTIVITY_VERIFIED", "CONFORMANCE_PASSED", "REVOKED"},
        "REVOKED": set(),
    },
    "binding": {
        "DRAFT": {"VALIDATED", "SUSPENDED", "REVOKED"},
        "VALIDATED": {"APPROVED", "SUSPENDED", "REVOKED"},
        "APPROVED": {"ACTIVE", "SUSPENDED", "REVOKED"},
        "ACTIVE": {"SUSPENDED", "REVOKED"},
        "SUSPENDED": {"VALIDATED", "APPROVED", "REVOKED"},
        "REVOKED": set(),
    },
}
SUSPEND_ROLE = {
    "institution": "institution_suspension_authority",
    "jurisdiction": "jurisdiction_suspension_authority",
    "minah": "minah_suspension_authority",
    "binding": "binding_suspension_authority",
}
REVOKE_ROLE = {
    "institution": "institution_revocation_authority",
    "jurisdiction": "jurisdiction_revocation_authority",
    "minah": "minah_revocation_authority",
    "binding": "binding_revocation_authority",
}
ID_FIELDS = {
    "institution": "institution_id",
    "jurisdiction": "package_id",
    "minah": "deployment_id",
    "binding": "binding_id",
}


class LifecycleViolation(ValueError):
    """A lifecycle transition is invalid or lacks required authority/evidence."""


def apply_transition(
    resource_type: str,
    record: dict[str, Any],
    target_status: str,
    actor: dict[str, Any],
    *,
    evidence_ref: str | None,
    approval_ref: str | None = None,
    expected_version: str | None = None,
) -> dict[str, Any]:
    """Return a proposed immutable next record and audit event.

    Actor is a trusted-principal assertion supplied by an authentication
    adapter, not an untrusted request body. The caller persists the returned
    snapshot and event atomically and enforces optimistic version checks.
    """
    if resource_type not in TRANSITIONS:
        raise LifecycleViolation("UNSUPPORTED_RESOURCE_TYPE")
    if not isinstance(record, dict) or not isinstance(actor, dict):
        raise LifecycleViolation("RECORD_AND_ACTOR_MUST_BE_OBJECTS")
    current = record.get("status")
    if target_status not in TRANSITIONS[resource_type].get(current, set()):
        raise LifecycleViolation("TRANSITION_NOT_PERMITTED")
    required = ROLE_REQUIREMENTS.get((resource_type, target_status))
    if target_status == "SUSPENDED":
        required = SUSPEND_ROLE[resource_type]
    elif target_status == "REVOKED":
        required = REVOKE_ROLE[resource_type]
    if not actor.get("authenticated") or not actor.get("verified"):
        raise LifecycleViolation("VERIFIED_AUTHENTICATED_ACTOR_REQUIRED")
    actor_ref = actor.get("actor_ref")
    authority_ref = actor.get("authority_ref")
    roles = actor.get("roles")
    if not isinstance(actor_ref, str) or not actor_ref.strip():
        raise LifecycleViolation("ACTOR_REFERENCE_REQUIRED")
    if not isinstance(authority_ref, str) or not authority_ref.strip():
        raise LifecycleViolation("AUTHORITY_REFERENCE_REQUIRED")
    if not isinstance(roles, list) or required not in roles:
        raise LifecycleViolation("REQUIRED_AUTHORITY_ROLE_MISSING")
    owner = record.get("owner_institution_id") or record.get("institution_id")
    actor_institution = actor.get("institution_id")
    approval_transition = target_status in {"VERIFIED", "APPROVED", "ACTIVE", "RETIRED", "REVOKED"}
    if approval_transition and owner and actor_institution == owner:
        raise LifecycleViolation("SELF_APPROVAL_PROHIBITED")
    if not isinstance(evidence_ref, str) or not evidence_ref.strip():
        raise LifecycleViolation("TRANSITION_EVIDENCE_REQUIRED")
    if target_status in {"VERIFIED", "APPROVED", "ACTIVE"}:
        if not isinstance(approval_ref, str) or not approval_ref.strip():
            raise LifecycleViolation("APPROVAL_REFERENCE_REQUIRED")
    if expected_version is not None and (not isinstance(expected_version, str) or not expected_version.strip()):
        raise LifecycleViolation("EXPECTED_VERSION_REQUIRED")
    updated = deepcopy(record)
    updated["status"] = target_status
    if target_status == "VERIFIED":
        updated["verification_ref"] = approval_ref
    elif target_status in {"APPROVED", "ACTIVE"}:
        updated["approval_ref"] = approval_ref
    event = {
        "event_type": f"{resource_type.upper()}_{target_status}",
        "resource_type": resource_type,
        "resource_id": record.get(ID_FIELDS[resource_type]),
        "from_status": current,
        "to_status": target_status,
        "actor_ref": actor_ref,
        "authority_ref": authority_ref,
        "evidence_ref": evidence_ref,
        "approval_ref": approval_ref,
        "expected_version": expected_version,
    }
    return {"record": updated, "event": event}
