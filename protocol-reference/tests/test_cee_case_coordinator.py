# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Tests for the CEE-owned synthetic case coordinator; no real funds or services."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from authority_adapter import UnavailableAuthorityAdapter
from cee_case_coordinator import CEECaseCoordinator, CaseConflict


def request(operation_id="CASE-001", **overrides):
    payload = {
        "operation_id": operation_id,
        "case_id": operation_id + ":CASE",
        "jurisdiction": "UG-DEMO",
        "evidence": {
            "consignment_id": operation_id + ":CASE",
            "commodity": "SYNTHETIC_MAIZE",
            "origin": "UGANDA-DEMO",
            "destination": "BORDER-DEMO",
            "rule_refs": ["SYNTHETIC-RULE-1"],
        },
        "origin_actor": "ORIGIN-A",
        "destination_actor": "DESTINATION-B",
        "execution_actor": "AUTHORITY-001",
        "origin_decision": "APPROVE",
        "destination_decision": "APPROVE",
        "declared_value_minor": 1_000_000,
        "payment_outcome": "CONFIRMED",
        "exempt": False,
        "revoke_origin_before_decision": False,
    }
    payload.update(overrides)
    return payload


class CEECaseCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = CEECaseCoordinator()
        self.addCleanup(self.coordinator.close)

    def test_happy_path_exposes_minah_then_cee_and_final_decision(self):
        result = self.coordinator.execute(request())
        self.assertEqual(result["status"], "COMPLETED_WITH_SIMULATED_SETTLEMENT")
        self.assertEqual(result["final_decision"], "PASSED")
        self.assertFalse(result["funds_moved"])
        self.assertFalse(result["minah_disposition"]["may_authorise"])
        self.assertEqual(result["assessment"]["legal_status"], "SYNTHETIC_DEMO_RULE_NOT_LAW")
        self.assertEqual(result["payment_lifecycle"]["recorded_status"], "SETTLED")
        components = [event["component"] for event in result["component_trace"]]
        first_cee = components.index("CEE")
        self.assertTrue(all(component == "MINAH" for component in components[:first_cee]))
        self.assertTrue(all(component == "CEE" for component in components[first_cee:]))
        self.assertEqual(result["execution_result"]["to_state"], "ACTIVE")

    def test_unknown_terminal_phase_is_deferred_not_promoted_to_passed(self):
        operation_id = "CASE-UNKNOWN-TERMINAL"
        payload = request(operation_id)
        self.coordinator.execute(payload)
        result = self.coordinator._finish(
            operation_id, "UNRECOGNIZED_PHASE", "UNRECOGNIZED_STATUS",
            {"reason": "regression-test", "funds_moved": False},
        )
        self.assertEqual(result["final_decision"], "DEFERRED")
        self.assertNotEqual(result["final_decision"], "PASSED")

    def test_case_runtime_shares_cee_store_and_rejects_split_store_configuration(self):
        self.assertIs(self.coordinator.cee_db, self.coordinator.ivm.db)
        with self.assertRaisesRegex(ValueError, "SPLIT_CASE_RUNTIME_STORAGE_NO_LONGER_SUPPORTED"):
            CEECaseCoordinator(cee_database="case-cee.sqlite3", ivm_database="case-runtime.sqlite3")

    def test_authority_adapter_exception_defers_case_before_admission(self):
        class BrokenAdapter:
            def evaluate(self, request):
                raise TimeoutError("sensitive backend details must not leak")

        coordinator = CEECaseCoordinator(authority_adapter=BrokenAdapter())
        try:
            result = coordinator.execute(request("CASE-AUTH-TIMEOUT"))
            self.assertEqual(result["final_decision"], "DEFERRED")
            self.assertEqual(result["reason"], "AUTHORITY_GATE_UNAVAILABLE")
            self.assertEqual(result["authority_gate"]["adapter"], "ADAPTER_ERROR")
            self.assertNotIn("ADMITTED", [event["phase"] for event in result["cee_events"]])
            self.assertIsNone(result["institutional_decisions"])
        finally:
            coordinator.close()

    def test_unavailable_authority_adapter_defers_case_before_admission(self):
        coordinator = CEECaseCoordinator(authority_adapter=UnavailableAuthorityAdapter())
        try:
            result = coordinator.execute(request("CASE-AUTH-UNAVAILABLE"))
            self.assertEqual(result["final_decision"], "DEFERRED")
            self.assertEqual(result["reason"], "AUTHORITY_VERIFICATION_UNAVAILABLE")
            self.assertEqual(result["authority_gate"]["outcome"], "DEFER")
            self.assertNotIn("ADMITTED", [event["phase"] for event in result["cee_events"]])
            self.assertIsNone(result["institutional_decisions"])
            self.assertIsNone(result["payment_lifecycle"])
        finally:
            coordinator.close()

    def test_execution_authority_is_rejected_before_cee_admission(self):
        result = self.coordinator.execute(request("CASE-AUTH-REJECT", execution_actor="UNAUTHORISED-ACTOR"))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["final_decision"], "REJECTED")
        self.assertEqual(result["reason"], "AUTHORITY_MISMATCH")
        self.assertIsNone(result["institutional_decisions"])
        self.assertNotIn("ADMITTED", [event["phase"] for event in result["cee_events"]])

    def test_origin_rejection_prevents_destination_and_payment(self):
        result = self.coordinator.execute(request("CASE-ORIGIN-REJECT", origin_decision="REJECT"))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["final_decision"], "REJECTED")
        self.assertIsNone(result["obligation"])
        self.assertIsNone(result["payment_lifecycle"])
        self.assertEqual(result["institutional_decisions"][-1]["status"], "NOT_ATTEMPTED")
        with self.assertRaises(ValueError):
            self.coordinator.payments.get_payment("CASE-ORIGIN-REJECT:PAYMENT:1")

    def test_destination_rejection_prevents_ivm_economic_gate_and_payment(self):
        result = self.coordinator.execute(request("CASE-DEST-REJECT", destination_decision="REJECT"))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["final_decision"], "REJECTED")
        self.assertNotIn("execution_result", result)
        self.assertIsNone(result["obligation"])
        self.assertIsNone(result["payment_lifecycle"])
        with self.assertRaises(ValueError):
            self.coordinator.payments.get_payment("CASE-DEST-REJECT:PAYMENT:1")

    def test_missing_minah_evidence_defers_before_institutional_or_financial_effects(self):
        payload = request("CASE-MINAH-DEFER", evidence={"commodity": "SYNTHETIC_MAIZE"})
        result = self.coordinator.execute(payload)
        self.assertEqual(result["status"], "DEFERRED")
        self.assertEqual(result["final_decision"], "DEFERRED")
        self.assertEqual(result["reason"], "MINAH_REQUIRES_ADDITIONAL_EVIDENCE")
        self.assertNotIn("ADMITTED", [event["phase"] for event in result["cee_events"]])
        self.assertIsNone(result["institutional_decisions"])
        self.assertIsNone(result["payment_lifecycle"])
        self.assertFalse(result["funds_moved"])

    def test_identical_replay_returns_same_result_and_conflicting_reuse_fails(self):
        payload = request("CASE-REPLAY")
        first = self.coordinator.execute(payload)
        second = self.coordinator.execute(payload)
        self.assertEqual(first["status"], second["status"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(len(self.coordinator.payments.history("CASE-REPLAY:PAYMENT:1")), 3)
        changed = request("CASE-REPLAY", declared_value_minor=2_000_000)
        with self.assertRaises(CaseConflict):
            self.coordinator.execute(changed)

    def test_unknown_provider_outcome_is_not_reported_as_settled(self):
        result = self.coordinator.execute(request("CASE-UNKNOWN", payment_outcome="TIMEOUT_UNKNOWN"))
        self.assertEqual(result["status"], "PENDING_PAYMENT_RECONCILIATION")
        self.assertEqual(result["payment_lifecycle"]["recorded_status"], "UNKNOWN_PENDING_RECONCILIATION")
        self.assertEqual(self.coordinator.inspect("CASE-UNKNOWN")["phase"], "PENDING_RECONCILIATION")
        self.assertFalse(result["funds_moved"])

    def test_crash_after_submission_intent_does_not_blindly_resubmit(self):
        class CrashOnceProvider:
            def __init__(self):
                self.calls = 0
            def submit(self, payment_id, amount_ugx, *, outcome="CONFIRMED"):
                self.calls += 1
                raise RuntimeError("simulated crash after request may have left process")

        provider = CrashOnceProvider()
        self.coordinator.provider = provider
        payload = request("CASE-UNCERTAIN-CRASH")
        with self.assertRaises(RuntimeError):
            self.coordinator.execute(payload)
        resumed = self.coordinator.execute(payload)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(resumed["status"], "PENDING_PAYMENT_RECONCILIATION")
        self.assertEqual(resumed["payment_lifecycle"]["recorded_status"], "UNKNOWN_PENDING_RECONCILIATION")
        self.assertFalse(resumed["funds_moved"])

    def test_source_sequenced_reconciliation_resolves_unknown_without_resubmission(self):
        payload = request("CASE-RECONCILE", payment_outcome="TIMEOUT_UNKNOWN")
        first = self.coordinator.execute(payload)
        self.assertEqual(first["status"], "PENDING_PAYMENT_RECONCILIATION")
        reconciled = self.coordinator.reconcile(
            "CASE-RECONCILE", "CASE-RECONCILE:RECON:2", "NOT_SETTLED",
            source_sequence=2, provider_reference="SYNTHETIC-RECON-REF",
        )
        self.assertEqual(reconciled["status"], "PENDING_PAYMENT_RESOLUTION")
        self.assertEqual(reconciled["payment_lifecycle"]["recorded_status"], "RECONCILED_NOT_SETTLED")
        replay = self.coordinator.reconcile(
            "CASE-RECONCILE", "CASE-RECONCILE:RECON:2", "NOT_SETTLED",
            source_sequence=2, provider_reference="SYNTHETIC-RECON-REF",
        )
        self.assertTrue(replay["duplicate"])
        with self.assertRaises(ValueError):
            self.coordinator.reconcile(
                "CASE-RECONCILE", "CASE-RECONCILE:STALE", "UNKNOWN", source_sequence=1,
            )

    def test_settlement_evidence_must_match_pinned_obligation(self):
        result = self.coordinator.execute(request("CASE-SETTLEMENT-MISMATCH", payment_outcome="TIMEOUT_UNKNOWN"))
        amount = result["obligation"]["amount_minor"]
        with self.assertRaisesRegex(ValueError, "SETTLEMENT_DOES_NOT_MATCH_PINNED_OBLIGATION"):
            self.coordinator.record_settlement_evidence(
                "CASE-SETTLEMENT-MISMATCH", "SETTLE-MISMATCH-1", "SYNTHETIC-SETTLE-1",
                amount_minor=amount + 1, currency="UGX",
            )
        self.assertNotEqual(
            self.coordinator.payments.get_payment("CASE-SETTLEMENT-MISMATCH:PAYMENT:1")["status"],
            "SETTLED",
        )

    def test_settlement_evidence_recovers_after_payment_store_commit_before_cee_write(self):
        result = self.coordinator.execute(request("CASE-SETTLEMENT-CRASH", payment_outcome="TIMEOUT_UNKNOWN"))
        payment_id = result["payment_lifecycle"]["payment_id"]
        original = self.coordinator.payments.record_settlement
        calls = {"count": 0}

        def crash_after_payment_commit(*args, **kwargs):
            committed = original(*args, **kwargs)
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("simulated process crash after payment-store commit")
            return committed

        self.coordinator.payments.record_settlement = crash_after_payment_commit
        obligation = result["obligation"]
        with self.assertRaisesRegex(RuntimeError, "simulated process crash"):
            self.coordinator.record_settlement_evidence(
                "CASE-SETTLEMENT-CRASH", "CASE-SETTLEMENT-EVENT-1", "SYNTHETIC-SETTLEMENT-REF",
                amount_minor=obligation["amount_minor"], currency=obligation["currency"],
            )
        self.assertEqual(self.coordinator.payments.get_payment(payment_id)["status"], "SETTLED")
        self.assertEqual(self.coordinator.inspect("CASE-SETTLEMENT-CRASH")["phase"], "PENDING_RECONCILIATION")

        resumed = self.coordinator.record_settlement_evidence(
            "CASE-SETTLEMENT-CRASH", "CASE-SETTLEMENT-EVENT-1", "SYNTHETIC-SETTLEMENT-REF",
            amount_minor=obligation["amount_minor"], currency=obligation["currency"],
        )
        self.assertEqual(resumed["status"], "COMPLETED_WITH_RECONCILIATION_CONFIRMED_SETTLEMENT")
        self.assertEqual(self.coordinator.payments.get_payment(payment_id)["status"], "SETTLED")
        history = self.coordinator.payments.history(payment_id)
        self.assertEqual(sum(e["event_type"] == "SETTLEMENT_EVIDENCE" for e in history), 1)
        self.assertEqual(
            sum(e["phase"] == "SETTLEMENT_EVIDENCE" for e in self.coordinator.inspect("CASE-SETTLEMENT-CRASH")["events"]),
            1,
        )
        self.assertFalse(resumed["funds_moved"])

    def test_settlement_event_id_reuse_with_changed_payload_conflicts(self):
        result = self.coordinator.execute(request("CASE-SETTLEMENT-CONFLICT", payment_outcome="TIMEOUT_UNKNOWN"))
        obligation = result["obligation"]
        args = ("CASE-SETTLEMENT-CONFLICT", "CASE-SETTLEMENT-EVENT-1", "SYNTHETIC-SETTLEMENT-REF")
        self.coordinator.record_settlement_evidence(
            *args, amount_minor=obligation["amount_minor"], currency=obligation["currency"],
        )
        with self.assertRaises(CaseConflict):
            self.coordinator.record_settlement_evidence(
                *args, amount_minor=obligation["amount_minor"] + 1, currency=obligation["currency"],
            )

    def test_revoked_origin_authority_blocks_all_downstream_work(self):
        result = self.coordinator.execute(request("CASE-REVOKED", revoke_origin_before_decision=True))
        self.assertEqual(result["status"], "REJECTED")
        self.assertIsNone(result["obligation"])
        self.assertIsNone(result["payment_lifecycle"])
        self.assertEqual(result["institutional_decisions"][0]["reason"], "AUTHORITY_REVOKED")


if __name__ == "__main__":
    unittest.main()
