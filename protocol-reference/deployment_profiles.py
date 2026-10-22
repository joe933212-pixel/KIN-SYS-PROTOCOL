# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Draft, machine-readable KIN-SYS deployment profiles."""
from __future__ import annotations
from copy import deepcopy
from typing import Any

DEPLOYMENT_PROFILE_CONTRACT_VERSION = "kin-sys-deployment-profile-0.1"
CORE_PROTOCOL_OBLIGATIONS = (
    "identity_authority_and_revocation", "versioned_rules_and_authority_context",
    "validated_state_transitions", "durable_or_explicitly_deferred_commitment",
    "replay_and_duplicate_effect_protection", "provenance_and_freshness",
    "auditable_failure_and_recovery", "declared_privacy_and_retention",
)
_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "id": "software_standard", "name": "Standard software deployment",
        "status": "DESIGN_PROFILE", "hardware_required": False,
        "connectivity": "Normally connected; dependencies may still fail",
        "power": "Supported host infrastructure; process and storage failures must be handled",
        "local_durable_storage_required": True,
        "offline_policy": "Each operation class must explicitly allow, defer, or prohibit offline execution",
        "optional_capabilities": ["HSM", "TEE", "zero_knowledge_proofs"],
        "additional_acceptance_checks": ["adapter and dependency failure handling", "restart and persistence behaviour", "key custody and rotation appropriate to the threat model"],
    },
    {
        "id": "constrained_connectivity", "name": "Constrained connectivity and power",
        "status": "DESIGN_PROFILE", "hardware_required": False,
        "connectivity": "Intermittent; partitions and delayed reconciliation are expected",
        "power": "Power interruption is expected; no continuous-power assumption",
        "local_durable_storage_required": True,
        "offline_policy": "Only bounded pre-authorised operations may proceed offline; others defer or reject; local acceptance is not remote finality",
        "optional_capabilities": ["HSM", "TEE", "zero_knowledge_proofs", "local_compute_substrate"],
        "additional_acceptance_checks": ["power-loss and restart tests on declared storage", "stale credential, authority, evidence, and rule-version handling", "replay-safe reconnect and conflict reconciliation", "explicit handling of work that cannot safely proceed offline"],
    },
    {
        "id": "hardware_enhanced_security", "name": "Optional hardware-enhanced security",
        "status": "DESIGN_PROFILE", "hardware_required": False,
        "connectivity": "Defined by the selected underlying profile",
        "power": "Defined by the selected underlying profile",
        "local_durable_storage_required": True,
        "offline_policy": "Inherits the selected underlying profile; hardware does not relax protocol authority or freshness rules",
        "optional_capabilities": ["HSM", "TEE", "secure_boot", "zero_knowledge_proofs"],
        "additional_acceptance_checks": ["document the exact hardware security boundary and supported operations", "test key provisioning, rotation, backup/recovery, compromise and replacement", "evaluate attestation and vendor/platform trust assumptions where used", "measure proof-generation and verification cost where proofs are used", "prove that loss of the hardware feature follows an explicit reject/defer/recovery path"],
    },
)

def deployment_profiles() -> dict[str, Any]:
    """Return detached profile data with common protocol obligations."""
    return {
        "contract_version": DEPLOYMENT_PROFILE_CONTRACT_VERSION,
        "core_protocol_obligations": list(CORE_PROTOCOL_OBLIGATIONS),
        "profiles": deepcopy(list(_PROFILES)),
        "limitations": [
            "Profiles are design targets, not implemented or certified deployment products.",
            "Hardware options do not replace protocol checks or prove external data truth.",
            "Actual guarantees depend on implementation, threat model, and acceptance evidence.",
        ],
    }

def validate_deployment_profiles(model: dict[str, Any] | None = None) -> bool:
    """Check profile shape and invariants; this is not a security certification."""
    model = deployment_profiles() if model is None else model
    if not isinstance(model, dict) or model.get("contract_version") != DEPLOYMENT_PROFILE_CONTRACT_VERSION:
        return False
    obligations, profiles = model.get("core_protocol_obligations"), model.get("profiles")
    if not isinstance(obligations, list) or not obligations or len(set(obligations)) != len(obligations):
        return False
    if not isinstance(profiles, list) or not profiles:
        return False
    seen: set[str] = set()
    for profile in profiles:
        if not isinstance(profile, dict):
            return False
        profile_id = profile.get("id")
        if not isinstance(profile_id, str) or not profile_id or profile_id in seen:
            return False
        seen.add(profile_id)
        if profile.get("hardware_required") is not False:
            return False
        for field in ("connectivity", "power", "offline_policy"):
            if not isinstance(profile.get(field), str) or not profile[field]:
                return False
        if not isinstance(profile.get("local_durable_storage_required"), bool):
            return False
        if not isinstance(profile.get("optional_capabilities"), list) or not isinstance(profile.get("additional_acceptance_checks"), list):
            return False
    return True
