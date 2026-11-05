# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Provider-neutral MINAH deployment-profile contract (draft v0.1).

A profile describes one institution-owned MINAH deployment. It does not deploy
an AI model, establish legal authority, or prove that a profile's references
are trustworthy. Profiles must be pinned to governed operations by the runtime.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

PROFILE_VERSION = "kin-sys-minah-profile-0.1"
SCOPES = {"NATIONAL_SOVEREIGN", "ENTERPRISE", "INSTITUTION"}
DEPLOYMENT_MODES = {"SELF_HOSTED", "ON_PREMISES", "CLOUD", "EDGE", "MANAGED"}
REQUIRED_FIELDS = {
    "profile_version",
    "deployment_id",
    "owner_scope",
    "owner_ref",
    "use_case",
    "jurisdictions",
    "policy_pack_version",
    "source_pack_version",
    "model_ref",
    "deployment_mode",
    "may_authorise",
}


class MinahProfileViolation(ValueError):
    """Raised when a MINAH deployment profile violates the draft contract."""


def validate_minah_profile(profile: Any) -> dict[str, Any]:
    """Validate profile shape and core gatekeeper invariants; return a safe copy."""
    if not isinstance(profile, dict):
        raise MinahProfileViolation("PROFILE_MUST_BE_OBJECT")

    missing = sorted(REQUIRED_FIELDS - set(profile))
    if missing:
        raise MinahProfileViolation("MISSING_REQUIRED_FIELDS:" + ",".join(missing))

    if profile["profile_version"] != PROFILE_VERSION:
        raise MinahProfileViolation("UNSUPPORTED_PROFILE_VERSION")

    for field in (
        "deployment_id",
        "owner_ref",
        "use_case",
        "policy_pack_version",
        "source_pack_version",
        "model_ref",
    ):
        value = profile.get(field)
        if not isinstance(value, str) or not value.strip():
            raise MinahProfileViolation("INVALID_" + field.upper())

    if not isinstance(profile["owner_scope"], str) or profile["owner_scope"] not in SCOPES:
        raise MinahProfileViolation("INVALID_OWNER_SCOPE")

    if not isinstance(profile["deployment_mode"], str) or profile["deployment_mode"] not in DEPLOYMENT_MODES:
        raise MinahProfileViolation("INVALID_DEPLOYMENT_MODE")

    jurisdictions = profile["jurisdictions"]
    if (
        not isinstance(jurisdictions, list)
        or not jurisdictions
        or not all(isinstance(item, str) and item.strip() for item in jurisdictions)
        or len(set(jurisdictions)) != len(jurisdictions)
    ):
        raise MinahProfileViolation("INVALID_JURISDICTIONS")

    if profile["may_authorise"] is not False:
        raise MinahProfileViolation("MINAH_MUST_NOT_GRANT_INSTITUTIONAL_AUTHORITY")

    return deepcopy(profile)
