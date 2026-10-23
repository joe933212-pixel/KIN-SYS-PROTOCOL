# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Injectable authority-gate contract for the MINAH -> CEE protocol boundary.

The default adapter is intentionally synthetic. It is useful for local tests,
but its actor fields are request claims and MUST NOT be treated as identity
proof or used for production authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class AuthorityGateDecision:
    """Server-side gate result consumed before CEE admission."""

    outcome: str
    reason: str | None
    adapter: str
    assurance: str
    details: dict[str, Any]


class AuthorityGateAdapter(Protocol):
    """Interface for a trusted authority/identity policy adapter."""

    def evaluate(self, request: dict[str, Any]) -> AuthorityGateDecision:
        """Return ALLOW, REJECT, or DEFER; unknown outcomes fail closed."""


class SyntheticDemoAuthorityAdapter:
    """Local-only policy adapter; it does not authenticate request actors."""

    name = "SYNTHETIC_DEMO_POLICY_NOT_IDENTITY_VERIFICATION"

    def __init__(self, *, authority_active: bool = True):
        self.authority_active = authority_active

    def evaluate(self, request: dict[str, Any]) -> AuthorityGateDecision:
        actor = request.get("actor_id", request.get("execution_actor"))
        if actor != "AUTHORITY-001":
            return AuthorityGateDecision(
                "REJECT", "AUTHORITY_MISMATCH", self.name,
                "UNVERIFIED_REQUEST_CLAIM",
                {"actor_claim_present": isinstance(actor, str) and bool(actor)},
            )
        if not self.authority_active:
            return AuthorityGateDecision(
                "REJECT", "AUTHORITY_REVOKED", self.name,
                "SYNTHETIC_CONFIGURED_STATUS",
                {"authority_active": False},
            )
        rule_set = request.get("rule_set")
        rule_version = request.get("rule_version")
        if rule_set is not None and (rule_set != "KIN-SYS-DEMO-RULES" or rule_version != "1.0"):
            return AuthorityGateDecision(
                "REJECT", "UNSUPPORTED_RULE_VERSION", self.name,
                "SYNTHETIC_RULE_CONFIGURATION",
                {"rule_set": rule_set, "rule_version": rule_version},
            )
        evidence = request.get("evidence")
        if isinstance(evidence, dict) and evidence.get("commodity") == "SYNTHETIC_PROHIBITED_COMMODITY":
            return AuthorityGateDecision(
                "REJECT", "RULE_REJECTED", self.name,
                "SYNTHETIC_RULE_CONFIGURATION",
                {"rule_eligibility": "REJECTED"},
            )
        return AuthorityGateDecision(
            "ALLOW", None, self.name, "SYNTHETIC_ONLY",
            {"identity_verified": False, "authority_scope_verified": False},
        )


class UnavailableAuthorityAdapter:
    """Fail-closed placeholder for environments without an approved adapter."""

    name = "AUTHORITY_ADAPTER_UNAVAILABLE"

    def evaluate(self, request: dict[str, Any]) -> AuthorityGateDecision:
        return AuthorityGateDecision(
            "DEFER", "AUTHORITY_VERIFICATION_UNAVAILABLE", self.name,
            "NOT_ESTABLISHED",
            {"identity_verified": False, "authority_scope_verified": False},
        )
