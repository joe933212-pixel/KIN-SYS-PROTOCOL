# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Tests for the CEE-owned synthetic case coordinator; no real funds or services."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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

    def test_happy_path_records_ordered_cee_minah_institution_ivm_and_payment_trace(self):
        result = self.coordinator.execute(request())
        self.assertEqual(result["status"], "COMPLETED_WITH_SIMULATED_SETTLEMENT")
        self.assertFalse(result["funds_moved"])
        self.assertFalse(result["minah_disposition"]["may_authorise"])
        self.assertEqual(result["assessment"]["legal_status"], "SYNTHETIC_DEMO_RULE_NOT_LAW")
        self.assertEqual(result["payment_lifecycle"]["recorded_status"], "SETTLED")
        components = [event["component"] for event in result["component_trace"]]
        self.assertLess(components.index("MINAH"), components.index("INSTITUTIONAL"))
        self.assertLess(components.index("INSTITUTIONAL"), components.index("IVM"))
        self.assertLess(components.index("IVM"), components.index("PAYMENT"))
        self.assertEqual(result["ivm_decision"]["to_state"], "ACTIVE")

    def test_origin_rejection_prevents_destination_and_payment(self):
        result = self.coordinator.execute(request("CASE-ORIGIN-REJECT", origin_decision="REJECT"))
        self.assertEqual(result["status"], "REJECTED")
        self.assertIsNone(result["obligation"])
        self.assertIsNone(result["payment_lifecycle"])
        self.assertEqual(result["institutional_decisions"][-1]["status"], "NOT_ATTEMPTED")
        with self.assertRaises(ValueError):
            self.coordinator.payments.get_payment("CASE-ORIGIN-REJECT:PAYMENT:1")

    def test_destination_rejection_prevents_ivm_economic_gate_and_payment(self):
        result = self.coordinator.execute(request("CASE-DEST-REJECT", destination_decision="REJECT"))
        self.assertEqual(result["status"], "REJECTED")
        self.assertNotIn("ivm_decision", result)
        self.assertIsNone(result["obligation"])
        self.assertIsNone(result["payment_lifecycle"])
        with self.assertRaises(ValueError):
            self.coordinator.payments.get_payment("CASE-DEST-REJECT:PAYMENT:1")

    def test_missing_minah_evidence_defers_before_institutional_or_financial_effects(self):
        payload = request("CASE-MINAH-DEFER", evidence={"commodity": "SYNTHETIC_MAIZE"})
        result = self.coordinator.execute(payload)
        self.assertEqual(result["status"], "DEFERRED")
        self.assertEqual(result["reason"], "MINAH_REQUIRES_ADDITIONAL_EVIDENCE")
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

    def test_revoked_origin_authority_blocks_all_downstream_work(self):
        result = self.coordinator.execute(request("CASE-REVOKED", revoke_origin_before_decision=True))
        self.assertEqual(result["status"], "REJECTED")
        self.assertIsNone(result["obligation"])
        self.assertIsNone(result["payment_lifecycle"])
        self.assertEqual(result["institutional_decisions"][0]["reason"], "AUTHORITY_REVOKED")


if __name__ == "__main__":
    unittest.main()
