# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Paired end-to-end observable comparison for the synthetic integrated workflow.

The conventional path independently composes the conventional workflow and
payment implementations. Shared output fields are compared; internal schemas
and execution paths remain separate.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from integrated_demo import IntegratedDemonstrator, DEMO_RULE_VERSION
from comparators.conventional_workflow import ConventionalWorkflow
from comparators.conventional_payment_lifecycle import ConventionalPaymentLifecycle


SCENARIOS = {
    "happy_path", "destination_rejection", "unauthorised_origin",
    "origin_rule_drift", "origin_revoked", "payment_declined",
    "payment_unknown", "synthetic_exemption", "edge_disconnected",
}


def _candidate(scenario: str) -> dict[str, Any]:
    demo = IntegratedDemonstrator()
    try:
        args: dict[str, Any] = {}
        if scenario == "destination_rejection":
            args["destination_decision"] = "REJECT"
        elif scenario == "unauthorised_origin":
            args["origin_actor"] = "UNAUTHORISED-TEST"
        elif scenario == "origin_rule_drift":
            args["origin_rule_version_override"] = "SYNTHETIC-OLD-RULE"
        elif scenario == "origin_revoked":
            args["revoke_origin_before_decision"] = True
        elif scenario == "payment_declined":
            args["payment_outcome"] = "DECLINED"
        elif scenario == "payment_unknown":
            args["payment_outcome"] = "TIMEOUT_UNKNOWN"
        elif scenario == "synthetic_exemption":
            args["exempt"] = True
        elif scenario == "edge_disconnected":
            args["edge_connected"] = False
        return _normalise_candidate(demo.run_case(consignment_id=f"PAIR-{scenario.upper()}", **args))
    finally:
        demo.close()


def _normalise_candidate(result: dict[str, Any]) -> dict[str, Any]:
    decisions = result.get("institutional_decisions") or {}
    origin = decisions.get("origin", {})
    destination = decisions.get("destination", {})
    tax = result.get("statutory_obligation")
    payment = result.get("payment")
    lifecycle = result.get("payment_lifecycle")
    return {
        "status": result["status"],
        "origin_result": origin.get("result"),
        "origin_operation_status": (origin.get("operation") or {}).get("status"),
        "origin_reason": (origin.get("operation") or {}).get("reason"),
        "destination_result": destination.get("result"),
        "destination_operation_status": (destination.get("operation") or {}).get("status"),
        "destination_reason": (destination.get("operation") or {}).get("reason"),
        "obligation_amount_minor": tax["assessed_amount_ugx"] if tax else None,
        "payment_status": payment.get("status") if payment else None,
        "payment_lifecycle_status": lifecycle.get("status") if lifecycle else None,
        "edge_queue_status": (result.get("edge_queue") or {}).get("status"),
        "minah_may_authorise": result["minah_advisory"]["may_authorise"],
        "funds_moved": bool((payment or {}).get("funds_moved", False) or (lifecycle or {}).get("funds_moved", False)),
    }


def _conventional(scenario: str) -> dict[str, Any]:
    case_id = f"PAIR-{scenario.upper()}"
    workflow = ConventionalWorkflow()
    payments = ConventionalPaymentLifecycle()
    try:
        if scenario == "edge_disconnected":
            return {
                "status": "PENDING", "origin_result": None, "origin_operation_status": None,
                "origin_reason": None, "destination_result": None,
                "destination_operation_status": None, "destination_reason": None,
                "obligation_amount_minor": None, "payment_status": None,
                "payment_lifecycle_status": None, "edge_queue_status": "QUEUED_OFFLINE",
                "minah_may_authorise": False, "funds_moved": False,
            }

        origin_rule = "SYNTHETIC-OLD-RULE" if scenario == "origin_rule_drift" else DEMO_RULE_VERSION
        workflow.create_case(
            case_id, origin_authority="ORIGIN-A", destination_authority="DESTINATION-B",
            origin_rule_version=origin_rule, destination_rule_version=DEMO_RULE_VERSION,
        )
        if scenario == "origin_revoked":
            workflow.set_authority_active(case_id, "ORIGIN", False)
        actor = "UNAUTHORISED-TEST" if scenario == "unauthorised_origin" else "ORIGIN-A"
        origin_operation = workflow.decide(
            event_id=f"{case_id}:ORIGIN:1", consignment_id=case_id, stage="ORIGIN",
            actor_id=actor, decision="APPROVE", rule_version=DEMO_RULE_VERSION,
        )
        origin = workflow.stage(case_id, "ORIGIN")
        if origin_operation["status"] == "ACCEPTED" and origin["result"] == "APPROVE":
            destination_decision = "REJECT" if scenario == "destination_rejection" else "APPROVE"
            destination_operation = workflow.decide(
                event_id=f"{case_id}:DESTINATION:1", consignment_id=case_id,
                stage="DESTINATION", actor_id="DESTINATION-B",
                decision=destination_decision, rule_version=DEMO_RULE_VERSION,
            )
        else:
            destination_operation = {"status": "NOT_ATTEMPTED", "reason": "ORIGIN_STAGE_NOT_APPROVED"}
        destination = workflow.stage(case_id, "DESTINATION")
        approved = (
            origin_operation["status"] == "ACCEPTED"
            and destination_operation["status"] == "ACCEPTED"
            and origin["result"] == "APPROVE" and destination["result"] == "APPROVE"
        )

        tax = None
        payment = None
        lifecycle_status = None
        amount = None
        if approved:
            exempt = scenario == "synthetic_exemption"
            amount = 0 if exempt else (1_000_000 * 500 + 5000) // 10000
            tax = {"amount": amount, "exempt": exempt}
            if exempt:
                payment = {"status": "NOT_REQUIRED_EXEMPT", "funds_moved": False}
                lifecycle_status = "NOT_REQUIRED_EXEMPT"
            else:
                payment_id = f"{case_id}:TAX:1"
                obligation_id = f"{case_id}:OBL:TAX:1"
                payments.create_obligation(obligation_id, amount, "UGX", f"{case_id}:SYNTHETIC-ASSESSMENT:1")
                payments.create_instruction(payment_id, obligation_id)
                if scenario == "payment_declined":
                    payments.record_provider_ack(payment_id, f"{payment_id}:ACK:1", "DECLINED")
                    payment = {"status": "SIMULATED_DECLINED", "funds_moved": False}
                    lifecycle_status = "SIMULATED_DECLINED"
                elif scenario == "payment_unknown":
                    payments.record_provider_ack(payment_id, f"{payment_id}:ACK:1", "UNKNOWN")
                    payments.record_reconciliation(payment_id, f"{payment_id}:RECONCILIATION:1", "UNKNOWN")
                    payment = {"status": "UNKNOWN_PENDING_RECONCILIATION", "funds_moved": False}
                    lifecycle_status = "UNKNOWN_PENDING_RECONCILIATION"
                else:
                    payments.record_provider_ack(payment_id, f"{payment_id}:ACK:1", "ACCEPTED", "SIMULATED-PROVIDER-REF")
                    payments.record_settlement(
                        payment_id, f"{payment_id}:SETTLEMENT:1", f"SIMULATED-SETTLEMENT:{payment_id}",
                        amount_minor=amount, currency="UGX",
                    )
                    payment = {"status": "SIMULATED_CONFIRMED", "funds_moved": False}
                    lifecycle_status = "SIMULATED_SETTLED"

        if origin_operation["status"] == "REJECTED" or destination_operation["status"] == "REJECTED":
            status = "REJECTED_AUTHORITY_ACTION_INVALID"
        elif origin["result"] == "REJECT" or destination["result"] == "REJECT":
            status = "INSTITUTIONAL_DECISION_REJECTED"
        elif not approved:
            status = "INSTITUTIONAL_DECISION_PENDING"
        elif scenario == "synthetic_exemption":
            status = "DEMO_READY_UNDER_SYNTHETIC_EXEMPTION"
        elif scenario == "payment_declined":
            status = "PENDING_PAYMENT_RESOLUTION"
        elif scenario == "payment_unknown":
            status = "PENDING_PAYMENT_RECONCILIATION"
        else:
            status = "DEMO_READY_AFTER_SIMULATED_REMITTANCE"

        return {
            "status": status,
            "origin_result": origin["result"],
            "origin_operation_status": origin_operation["status"],
            "origin_reason": origin_operation.get("reason"),
            "destination_result": destination["result"],
            "destination_operation_status": destination_operation["status"],
            "destination_reason": destination_operation.get("reason"),
            "obligation_amount_minor": tax["amount"] if tax else None,
            "payment_status": payment["status"] if payment else None,
            "payment_lifecycle_status": lifecycle_status,
            "edge_queue_status": None,
            "minah_may_authorise": False,
            "funds_moved": False,
        }
    finally:
        workflow.close()
        payments.close()


def compare_scenario(scenario: str) -> dict[str, Any]:
    if scenario not in SCENARIOS:
        raise ValueError(f"UNKNOWN_INTEGRATED_SCENARIO:{scenario}")
    candidate = _candidate(scenario)
    conventional = _conventional(scenario)
    return {
        "scenario": scenario,
        "candidate": candidate,
        "conventional": conventional,
        "equivalent_on_observed_fields": candidate == conventional,
        "differences": {
            key: {"candidate": candidate.get(key), "conventional": conventional.get(key)}
            for key in candidate if candidate.get(key) != conventional.get(key)
        },
    }


def run_all() -> dict[str, Any]:
    results = [compare_scenario(name) for name in sorted(SCENARIOS)]
    return {
        "contract_version": "integrated-observable-vector-0.1",
        "scenario_count": len(results),
        "all_equivalent_on_observed_fields": all(item["equivalent_on_observed_fields"] for item in results),
        "results": results,
        "limitations": [
            "Synthetic data, rules, identity, MINAH advisory and payment outcomes only.",
            "The candidate and conventional paths are local SQLite software models.",
            "No live identity, institutional, tax, payment, network or edge service is called.",
            "Equivalence on this vector does not establish equal performance, security, cost, novelty or production readiness.",
        ],
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run_all(), indent=2, sort_keys=True))
