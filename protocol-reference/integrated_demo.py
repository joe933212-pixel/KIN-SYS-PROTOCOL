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
from edge_queue import EdgeOperationQueue
from system_model import make_trace_event, system_manifest, validate_system_trace


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

    def __init__(self, database: str = ":memory:", *, database_prefix: str | None = None):
        """Use in-memory stores by default or separate durable stores under a shared prefix."""
        if database_prefix is None:
            workflow_db = payment_db = edge_db = database
        else:
            workflow_db = f"{database_prefix}.workflow.sqlite3"
            payment_db = f"{database_prefix}.payments.sqlite3"
            edge_db = f"{database_prefix}.edge.sqlite3"
        self.workflow = InstitutionalWorkflow(workflow_db)
        self.identity = SyntheticIdentityGate()
        self.minah = DeterministicMinahAdapter()
        self.tax_rules = SyntheticTaxRules()
        self.payment = SimulatedPaymentProvider()
        self.payment_lifecycle = PaymentLifecycle(payment_db)
        self.edge_queue = EdgeOperationQueue(edge_db)

    def close(self) -> None:
        self.workflow.close()
        self.payment_lifecycle.close()
        self.edge_queue.close()

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
        edge_reconnect: bool = False,
        edge_current_evidence_version: str = "SYNTHETIC-EVIDENCE-1",
        edge_current_rule_version: str = DEMO_RULE_VERSION,
        edge_authority_active: bool = True,
        exempt: bool = False,
        revoke_origin_before_decision: bool = False,
        origin_rule_version_override: str | None = None,
        destination_rule_version_override: str | None = None,
    ) -> dict[str, Any]:
        trace: list[dict[str, Any]] = []
        if not self.identity.permits("EXPORTER-TEST", "SUBMIT_CONSIGNMENT"):
            raise PermissionError("EXPORTER_NOT_AUTHORISED")
        trace.append(make_trace_event(
            case_id=consignment_id,
            operation_id=f"{consignment_id}:IDENTITY:SUBMIT:1",
            component_id="identity_authority",
            status="ACCEPTED",
            mode="SYNTHETIC_IDENTITIES",
            component_version="synthetic-identity-gate-0.1",
            reason="SYNTHETIC_PRINCIPAL_PERMITTED",
        ))
        evidence = minah_evidence or {
            "consignment_id": consignment_id,
            "commodity": "SYNTHETIC_MAIZE",
            "origin": "UGANDA-DEMO",
            "destination": "BORDER-DEMO",
            "rule_refs": ["SYNTHETIC-RULE-PACK-1"],
        }
        advisory = self.minah.review(evidence)
        trace.append(make_trace_event(
            case_id=consignment_id,
            operation_id=f"{consignment_id}:MINAH:REVIEW:1",
            component_id="minah_ai",
            status=advisory["status"],
            mode="DETERMINISTIC_MOCK",
            component_version=advisory["model_version"],
            evidence_refs=advisory["source_refs"],
            reason="ADVISORY_ONLY_NO_AUTHORITY",
        ))
        self.workflow.create_case(
            consignment_id,
            origin_authority="ORIGIN-A",
            destination_authority="DESTINATION-B",
            origin_rule_version=origin_rule_version_override or DEMO_RULE_VERSION,
            destination_rule_version=destination_rule_version_override or DEMO_RULE_VERSION,
        )
        trace.append(make_trace_event(
            case_id=consignment_id,
            operation_id=f"{consignment_id}:CASE:CREATE:1",
            component_id="protocol_orchestration",
            status="ACCEPTED",
            mode="LOCAL_SQLITE",
            component_version="institutional-workflow-0.1",
            reason="CASE_CONTEXT_CREATED_OR_REUSED",
        ))
        if not edge_connected:
            # Queue a request for later central review; never decide institutionally offline.
            queued = self.edge_queue.enqueue(
                f"{consignment_id}:EDGE:SUBMIT:1",
                {"case_id": consignment_id, "action": "REQUEST_CENTRAL_REVIEW"},
                evidence_version="SYNTHETIC-EVIDENCE-1",
                rule_version=DEMO_RULE_VERSION,
            )
            if edge_reconnect:
                queued = self.edge_queue.replay(
                    queued["operation_id"],
                    current_evidence_version=edge_current_evidence_version,
                    current_rule_version=edge_current_rule_version,
                    authority_active=edge_authority_active,
                )
            trace.append(make_trace_event(
                case_id=consignment_id,
                operation_id=queued["operation_id"],
                component_id="edge_operation",
                status=queued["status"],
                mode="LOCAL_SQLITE_EDGE_PROFILE",
                component_version="edge-queue-sqlite-0.1",
                evidence_refs=[queued.get("evidence_version", "SYNTHETIC-EVIDENCE-1")],
                reason=queued.get("replay_reason") or "CENTRAL_REVIEW_REQUIRED",
            ))
            trace.append(make_trace_event(
                case_id=consignment_id,
                operation_id=f"{consignment_id}:CASE:OUTCOME:1",
                component_id="protocol_orchestration",
                status="PENDING",
                mode="SIMULATED_EDGE",
                component_version="kin-sys-system-model-0.1",
                reason="OFFLINE_SUBMISSION_DOES_NOT_GRANT_INSTITUTIONAL_APPROVAL",
            ))
            return {
                "case_id": consignment_id,
                "profile": "SIMULATED_EDGE_RECONNECTED" if edge_reconnect else "SIMULATED_EDGE_DISCONNECTED",
                "system_model": system_manifest(),
                "system_trace": trace,
                "system_trace_valid": validate_system_trace(trace, case_id=consignment_id),
                "status": "PENDING",
                "reason": (
                    "READY_FOR_CENTRAL_REVIEW_NOT_INSTITUTIONAL_APPROVAL"
                    if queued["status"] == "READY_FOR_CENTRAL_REVIEW"
                    else queued["replay_reason"] or "INSTITUTIONAL_DECISIONS_REQUIRE_CONNECTIVITY_IN_THIS_PROFILE"
                ),
                "minah_advisory": advisory,
                "identity_mode": "SYNTHETIC_TEST_IDENTITIES",
                "payment": None,
                "payment_lifecycle": None,
                "edge_queue": {**queued, "history": self.edge_queue.history(queued["operation_id"])},
                "history": [],
                "claims": [
                    "NO_PHYSICAL_NEXUS_POD", "NO_LIVE_IDENTITY", "NO_LIVE_PAYMENT",
                    "OFFLINE_QUEUE_REPLAY_IS_NOT_INSTITUTIONAL_APPROVAL",
                ],
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
        trace.append(make_trace_event(
            case_id=consignment_id,
            operation_id=f"{consignment_id}:ORIGIN:1",
            component_id="institutional_coordination",
            status=origin_result["status"],
            mode="TWO_INSTITUTION_SYNTHETIC_WORKFLOW",
            component_version="institutional-workflow-0.1",
            reason=origin_result.get("reason"),
        ))
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
        trace.append(make_trace_event(
            case_id=consignment_id,
            operation_id=f"{consignment_id}:DESTINATION:1",
            component_id="institutional_coordination",
            status=destination_result["status"],
            mode="TWO_INSTITUTION_SYNTHETIC_WORKFLOW",
            component_version="institutional-workflow-0.1",
            reason=destination_result.get("reason"),
        ))
        # A replayed historical operation may still report ACCEPTED after its
        # authority has been revoked or its rule version has changed. Downstream
        # obligations require current authority/rule state plus matching approval
        # history, not merely the original operation's idempotent replay result.
        history = self.workflow.history(consignment_id)

        def current_approval_is_valid(stage_name: str, stage_state: dict[str, Any]) -> bool:
            matching_history = [
                event for event in history
                if event["stage"] == stage_name
                and event["new_outcome"] == "APPROVE"
                and event["rule_version"] == DEMO_RULE_VERSION
                and event["authority_id"] == stage_state["authority_id"]
                and event["actor_id"] == stage_state["authority_id"]
            ]
            return (
                stage_state["authority_active"] == 1
                and stage_state["rule_version"] == DEMO_RULE_VERSION
                and stage_state["outcome"] == "APPROVE"
                and bool(matching_history)
            )

        origin_currently_valid = current_approval_is_valid("ORIGIN", origin)
        destination_currently_valid = current_approval_is_valid("DESTINATION", destination)
        institutions_approved = (
            origin_result["status"] == "ACCEPTED"
            and destination_result["status"] == "ACCEPTED"
            and origin_currently_valid
            and destination_currently_valid
        )
        tax = self.tax_rules.assess(declared_value_ugx, exempt=exempt) if institutions_approved else None
        trace.append(make_trace_event(
            case_id=consignment_id,
            operation_id=f"{consignment_id}:TAX:ASSESS:1",
            component_id="rules_and_statutory_policy",
            status=tax["status"] if tax else "NOT_ATTEMPTED",
            mode="FICTIONAL_RULES" if tax else "PREREQUISITE_BLOCKED",
            component_version=DEMO_RULE_VERSION,
            evidence_refs=[f"{consignment_id}:ORIGIN:1", f"{consignment_id}:DESTINATION:1"] if tax else [],
            reason=tax["legal_status"] if tax else "REQUIRED_INSTITUTIONAL_APPROVALS_NOT_VALID",
        ))
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

        payment_trace_status = (
            payment_lifecycle_result["status"]
            if payment_lifecycle_result
            else "NOT_ATTEMPTED"
        )
        payment_trace_refs = []
        if payment_lifecycle_result and payment_lifecycle_result.get("obligation_id"):
            payment_trace_refs.append(payment_lifecycle_result["obligation_id"])
        if payment and payment.get("payment_id"):
            payment_trace_refs.append(payment["payment_id"])
        trace.append(make_trace_event(
            case_id=consignment_id,
            operation_id=f"{consignment_id}:ECONOMIC-EFFECT:1",
            component_id="economic_accounting_and_remittance",
            status=payment_trace_status,
            mode="LOCAL_SQLITE_AND_SIMULATED_PROVIDER" if payment_lifecycle_result else "PREREQUISITE_BLOCKED",
            component_version="payment-lifecycle-0.1",
            evidence_refs=payment_trace_refs,
            reason=(
                "SYNTHETIC_OR_SIMULATED_PAYMENT_ONLY"
                if payment_lifecycle_result
                else "NO_VALID_ASSESSMENT_OR_NO_PAYMENT_REQUIRED"
            ),
        ))
        if origin_result["status"] == "REJECTED" or destination_result["status"] == "REJECTED":
            final_status = "REJECTED_AUTHORITY_ACTION_INVALID"
        elif origin.get("outcome") == "REJECT" or destination.get("outcome") == "REJECT":
            final_status = "INSTITUTIONAL_DECISION_REJECTED"
        elif not origin_currently_valid or not destination_currently_valid:
            final_status = "REJECTED_AUTHORITY_ACTION_INVALID"
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

        trace.append(make_trace_event(
            case_id=consignment_id,
            operation_id=f"{consignment_id}:CASE:OUTCOME:1",
            component_id="protocol_orchestration",
            status=final_status,
            mode="LOCAL_SYNTHETIC",
            component_version="kin-sys-system-model-0.1",
            reason="WHOLE_CASE_OUTCOME",
        ))
        return {
            "case_id": consignment_id,
            "profile": "CENTRAL_DEMO_ONLINE",
            "system_model": system_manifest(),
            "system_trace": trace,
            "system_trace_valid": validate_system_trace(trace, case_id=consignment_id),
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
            "edge_queue": None,
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
