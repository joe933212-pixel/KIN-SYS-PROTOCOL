# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Integrated KIN-SYS demonstrator slice using synthetic data only.

This module wires existing institutional-stage research code to deliberately
simulated identity, MINAH AI, statutory-obligation, payment, and edge adapters.
It is an executable research demonstration, not a production integration.
"""
from __future__ import annotations

from typing import Any

from workflows.institutional_workflow import InstitutionalWorkflow
from payment_lifecycle import PaymentLifecycle


DEMO_RULE_VERSION = "UG-SYNTHETIC-1.0"


class SyntheticIdentityGate:
    """Test-only identity and authority adapter; no real identity data is used."""

    def __init__(self, principals: dict[str, set[str]] | None = None):
        self.principals = principals or {
            "EXPORTER-TEST": {"SUBMIT_CONSIGNMENT"},
            "ORIGIN-A": {"DECIDE_ORIGIN"},
            "DESTINATION-B": {"DECIDE_DESTINATION"},
        }

    def permits(self, principal: str, action: str) -> bool:
        return action in self.principals.get(principal, set())


class DeterministicMinahAdapter:
    """Replaceable, deterministic stand-in; not a language model or legal oracle."""

    version = "minah-mock-0.1"

    def review(self, evidence: dict[str, Any]) -> dict[str, Any]:
        missing = sorted({"consignment_id", "commodity", "origin", "destination"} - set(evidence))
        return {
            "status": "NEEDS_INFORMATION" if missing else "ADVISORY_ONLY",
            "missing_fields": missing,
            "source_refs": list(evidence.get("rule_refs", [])),
            "uncertainty": "HIGH" if missing or evidence.get("rule_conflict") else "LOW",
            "may_authorise": False,
            "model_version": self.version,
        }


class SyntheticTaxRules:
    """Fictional rules for demonstrating calculation provenance, not real tax law."""

    version = DEMO_RULE_VERSION
    rate_basis_points = 500  # fictional 5%; integer arithmetic avoids float drift

    def assess(self, declared_value_ugx: int, *, exempt: bool = False) -> dict[str, Any]:
        if not isinstance(declared_value_ugx, int) or declared_value_ugx < 0:
            raise ValueError("INVALID_DECLARED_VALUE")
        amount = 0 if exempt else (declared_value_ugx * self.rate_basis_points + 5000) // 10000
        return {
            "rule_version": self.version,
            "currency": "UGX",
            "declared_value_ugx": declared_value_ugx,
            "rate_basis_points": 0 if exempt else self.rate_basis_points,
            "exempt": exempt,
            "assessed_amount_ugx": amount,
            "status": "EXEMPT" if exempt else "ASSESSED",
            "legal_status": "SYNTHETIC_DEMO_RULE_NOT_LAW",
        }


class SimulatedPaymentProvider:
    """Fake provider: never moves funds and never claims live settlement."""

    def submit(self, payment_id: str, amount_ugx: int, *, outcome: str = "CONFIRMED") -> dict[str, Any]:
        if not payment_id or amount_ugx < 0:
            raise ValueError("INVALID_PAYMENT_REQUEST")
        if outcome not in {"CONFIRMED", "DECLINED", "TIMEOUT_UNKNOWN"}:
            raise ValueError("UNSUPPORTED_SIMULATED_PAYMENT_OUTCOME")
        status = {
            "CONFIRMED": "SIMULATED_CONFIRMED",
            "DECLINED": "SIMULATED_DECLINED",
            "TIMEOUT_UNKNOWN": "UNKNOWN_PENDING_RECONCILIATION",
        }[outcome]
        return {
            "payment_id": payment_id,
            "amount_ugx": amount_ugx,
            "status": status,
            "funds_moved": False,
            "provider": "SIMULATED_ONLY",
        }


class IntegratedDemonstrator:
    """Compose synthetic adapters and the current two-institution workflow slice."""

    def __init__(self, database: str = ":memory:"):
        self.workflow = InstitutionalWorkflow(database)
        self.identity = SyntheticIdentityGate()
        self.minah = DeterministicMinahAdapter()
        self.tax_rules = SyntheticTaxRules()
        self.payment = SimulatedPaymentProvider()
        self.payment_lifecycle = PaymentLifecycle()

    def close(self) -> None:
        self.workflow.close()
        self.payment_lifecycle.close()

    def run_case(
        self,
        *,
        consignment_id: str = "DEMO-CONSIGNMENT-001",
        origin_decision: str = "APPROVE",
        destination_decision: str = "APPROVE",
        declared_value_ugx: int = 1_000_000,
        payment_outcome: str = "CONFIRMED",
        minah_evidence: dict[str, Any] | None = None,
        origin_actor: str = "ORIGIN-A",
        destination_actor: str = "DESTINATION-B",
        edge_connected: bool = True,
        exempt: bool = False,
        revoke_origin_before_decision: bool = False,
        origin_rule_version_override: str | None = None,
    ) -> dict[str, Any]:
        if not self.identity.permits("EXPORTER-TEST", "SUBMIT_CONSIGNMENT"):
            raise PermissionError("EXPORTER_NOT_AUTHORISED")
        evidence = minah_evidence or {
            "consignment_id": consignment_id,
            "commodity": "SYNTHETIC_MAIZE",
            "origin": "UGANDA-DEMO",
            "destination": "BORDER-DEMO",
            "rule_refs": ["SYNTHETIC-RULE-PACK-1"],
        }
        advisory = self.minah.review(evidence)
        self.workflow.create_case(
            consignment_id,
            origin_authority="ORIGIN-A",
            destination_authority="DESTINATION-B",
            origin_rule_version=origin_rule_version_override or DEMO_RULE_VERSION,
            destination_rule_version=DEMO_RULE_VERSION,
        )
        if not edge_connected:
            # This first profile declares institutional decisions online-required.
            return {
                "case_id": consignment_id,
                "profile": "SIMULATED_EDGE_DISCONNECTED",
                "status": "PENDING",
                "reason": "INSTITUTIONAL_DECISIONS_REQUIRE_CONNECTIVITY_IN_THIS_PROFILE",
                "minah_advisory": advisory,
                "identity_mode": "SYNTHETIC_TEST_IDENTITIES",
                "payment": None,
                "history": [],
                "claims": ["NO_PHYSICAL_NEXUS_POD", "NO_LIVE_IDENTITY", "NO_LIVE_PAYMENT"],
            }

        if revoke_origin_before_decision:
            self.workflow.set_authority_active(consignment_id, "ORIGIN", False)

        origin_result = self.workflow.decide(
            event_id=f"{consignment_id}:ORIGIN:1",
            consignment_id=consignment_id,
            stage="ORIGIN",
            actor_id=origin_actor,
            decision=origin_decision,
            rule_version=DEMO_RULE_VERSION,
        )
        origin = self.workflow.stage(consignment_id, "ORIGIN")
        if origin_result["status"] == "ACCEPTED" and origin.get("outcome") == "APPROVE":
            destination_result = self.workflow.decide(
                event_id=f"{consignment_id}:DESTINATION:1",
                consignment_id=consignment_id,
                stage="DESTINATION",
                actor_id=destination_actor,
                decision=destination_decision,
                rule_version=DEMO_RULE_VERSION,
            )
        else:
            destination_result = {
                "status": "NOT_ATTEMPTED",
                "reason": "ORIGIN_STAGE_NOT_APPROVED",
                "duplicate": False,
            }
        destination = self.workflow.stage(consignment_id, "DESTINATION")
        institutions_approved = (
            origin_result["status"] == "ACCEPTED"
            and destination_result["status"] == "ACCEPTED"
            and origin.get("outcome") == "APPROVE"
            and destination.get("outcome") == "APPROVE"
        )
        tax = self.tax_rules.assess(declared_value_ugx, exempt=exempt) if institutions_approved else None
        payment = None
        payment_lifecycle_result = None
        if tax and tax["assessed_amount_ugx"] > 0:
            payment_id = f"{consignment_id}:TAX:1"
            obligation_id = f"{consignment_id}:OBL:TAX:1"
            amount = tax["assessed_amount_ugx"]
            self.payment_lifecycle.create_obligation(
                obligation_id, amount, "UGX", f"{consignment_id}:SYNTHETIC-ASSESSMENT:1"
            )
            instruction = self.payment_lifecycle.create_instruction(payment_id, obligation_id)
            payment = self.payment.submit(payment_id, amount, outcome=payment_outcome)
            ack = {
                "CONFIRMED": "ACCEPTED",
                "DECLINED": "DECLINED",
                "TIMEOUT_UNKNOWN": "UNKNOWN",
            }[payment_outcome]
            self.payment_lifecycle.record_provider_ack(
                payment_id, f"{payment_id}:ACK:1", ack,
                "SIMULATED-PROVIDER-REF" if ack == "ACCEPTED" else None,
            )
            if payment_outcome == "CONFIRMED":
                lifecycle = self.payment_lifecycle.record_settlement(
                    payment_id, f"{payment_id}:SETTLEMENT:1",
                    f"SIMULATED-SETTLEMENT:{payment_id}",
                    amount_minor=amount, currency="UGX",
                )
                lifecycle_status = "SIMULATED_SETTLED"
            elif payment_outcome == "DECLINED":
                lifecycle = self.payment_lifecycle.get_payment(payment_id)
                lifecycle_status = "SIMULATED_DECLINED"
            else:
                lifecycle = self.payment_lifecycle.record_reconciliation(
                    payment_id, f"{payment_id}:RECONCILIATION:1", "UNKNOWN"
                )
                lifecycle_status = "UNKNOWN_PENDING_RECONCILIATION"
            payment_lifecycle_result = {
                "execution_mode": "SIMULATED_ONLY",
                "obligation_id": obligation_id,
                "instruction_status": instruction["status"],
                "status": lifecycle_status,
                "recorded_status": lifecycle["status"],
                "settlement_reference": lifecycle["settlement_reference"],
                "history": self.payment_lifecycle.history(payment_id),
                "funds_moved": False,
            }
        elif tax and tax["exempt"]:
            payment = {
                "payment_id": None, "amount_ugx": 0, "status": "NOT_REQUIRED_EXEMPT",
                "funds_moved": False, "provider": "NOT_CALLED",
            }
            payment_lifecycle_result = {
                "execution_mode": "SIMULATED_ONLY",
                "status": "NOT_REQUIRED_EXEMPT",
                "funds_moved": False,
            }

        if origin_result["status"] == "REJECTED" or destination_result["status"] == "REJECTED":
            final_status = "REJECTED_AUTHORITY_ACTION_INVALID"
        elif origin.get("outcome") == "REJECT" or destination.get("outcome") == "REJECT":
            final_status = "INSTITUTIONAL_DECISION_REJECTED"
        elif not institutions_approved:
            final_status = "INSTITUTIONAL_DECISION_PENDING"
        elif payment and payment["status"] == "SIMULATED_CONFIRMED":
            final_status = "DEMO_READY_AFTER_SIMULATED_REMITTANCE"
        elif payment and payment["status"] == "NOT_REQUIRED_EXEMPT":
            final_status = "DEMO_READY_UNDER_SYNTHETIC_EXEMPTION"
        elif payment and payment["status"] == "SIMULATED_DECLINED":
            final_status = "PENDING_PAYMENT_RESOLUTION"
        elif payment and payment["status"] == "UNKNOWN_PENDING_RECONCILIATION":
            final_status = "PENDING_PAYMENT_RECONCILIATION"
        else:
            final_status = "PENDING"

        return {
            "case_id": consignment_id,
            "profile": "CENTRAL_DEMO_ONLINE",
            "status": final_status,
            "identity_mode": "SYNTHETIC_TEST_IDENTITIES",
            "minah_advisory": advisory,
            "institutional_decisions": {
                "origin": {"result": origin.get("outcome"), "operation": origin_result},
                "destination": {"result": destination.get("outcome"), "operation": destination_result},
            },
            "statutory_obligation": tax,
            "payment": payment,
            "payment_lifecycle": payment_lifecycle_result,
            "history": self.workflow.history(consignment_id),
            "claims": [
                "SYNTHETIC_RULES_NOT_LAW",
                "MINAH_ADVISORY_CANNOT_AUTHORISE",
                "PAYMENT_PROVIDER_SIMULATED_NO_FUNDS_MOVED",
                "LOCAL_SQLITE_WORKFLOW_NOT_DISTRIBUTED_CONSENSUS",
                "NO_PHYSICAL_NEXUS_POD",
                "NO_LIVE_IDENTITY_OR_INSTITUTIONAL_INTEGRATION",
            ],
        }


def demo_report() -> dict[str, Any]:
    """Return a deterministic happy-path report for CLI and CI smoke testing."""
    demo = IntegratedDemonstrator()
    try:
        return demo.run_case()
    finally:
        demo.close()


if __name__ == "__main__":
    import json
    print(json.dumps(demo_report(), indent=2, sort_keys=True))
