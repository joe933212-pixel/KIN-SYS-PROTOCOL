# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Crash-window regression tests across independently committed prototype stores.

These tests establish local SQLite retry behaviour only. They do not prove
atomicity or exactly-once effects across real distributed services.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cee_case_coordinator import CEECaseCoordinator


def request(operation_id, **overrides):
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
        "payment_outcome": "TIMEOUT_UNKNOWN",
        "exempt": False,
        "revoke_origin_before_decision": False,
    }
    payload.update(overrides)
    return payload


class CrossStoreRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = CEECaseCoordinator()
        self.addCleanup(self.coordinator.close)

    def test_institutional_commit_before_cee_audit_recovers_without_duplicate_decision(self):
        original_event = self.coordinator._event
        injected = {"done": False}

        def crash_after_institutional_commit(operation_id, component, phase, status, detail):
            if component == "INSTITUTIONAL" and phase == "ORIGIN" and not injected["done"]:
                injected["done"] = True
                raise RuntimeError("injected crash after institutional decision commit")
            return original_event(operation_id, component, phase, status, detail)

        self.coordinator._event = crash_after_institutional_commit
        payload = request("CROSS-STORE-ORIGIN-CRASH")
        with self.assertRaisesRegex(RuntimeError, "after institutional decision commit"):
            self.coordinator.execute(payload)

        committed_history = self.coordinator.workflow.history(payload["case_id"])
        self.assertEqual(len([e for e in committed_history if e["stage"] == "ORIGIN"]), 1)

        self.coordinator._event = original_event
        resumed = self.coordinator.execute(payload)

        history = self.coordinator.workflow.history(payload["case_id"])
        self.assertEqual(len([e for e in history if e["stage"] == "ORIGIN"]), 1)
        self.assertEqual(resumed["final_decision"], "DEFERRED")
        self.assertEqual(resumed["payment_lifecycle"]["recorded_status"], "UNKNOWN_PENDING_RECONCILIATION")
        self.assertFalse(resumed["funds_moved"])
        cee_events = self.coordinator.inspect(payload["operation_id"])["events"]
        self.assertEqual(sum(
            e["component"] == "INSTITUTIONAL" and e["phase"] == "ORIGIN"
            for e in cee_events
        ), 1)

    def test_payment_reconciliation_commit_before_cee_audit_recovers_idempotently(self):
        payload = request("CROSS-STORE-RECON-CRASH")
        initial = self.coordinator.execute(payload)
        payment_id = initial["payment_lifecycle"]["payment_id"]
        original = self.coordinator.payments.record_reconciliation
        injected = {"done": False}

        def commit_then_crash(*args, **kwargs):
            result = original(*args, **kwargs)
            if not injected["done"]:
                injected["done"] = True
                raise RuntimeError("injected crash after payment reconciliation commit")
            return result

        self.coordinator.payments.record_reconciliation = commit_then_crash
        args = (payload["operation_id"], "RECON-CRASH-EVENT-1", "NOT_SETTLED")
        kwargs = {"source_sequence": 1, "provider_reference": "SYNTHETIC-RECON"}
        with self.assertRaisesRegex(RuntimeError, "after payment reconciliation commit"):
            self.coordinator.reconcile(*args, **kwargs)

        self.assertEqual(
            self.coordinator.payments.get_payment(payment_id)["status"],
            "RECONCILED_NOT_SETTLED",
        )
        before = self.coordinator.inspect(payload["operation_id"])
        self.assertFalse(any(
            e["phase"] == "RECONCILIATION" and e["detail"].get("event_id") == args[1]
            for e in before["events"]
        ))

        self.coordinator.payments.record_reconciliation = original
        resumed = self.coordinator.reconcile(*args, **kwargs)
        self.assertEqual(resumed["payment_lifecycle"]["recorded_status"], "RECONCILED_NOT_SETTLED")
        self.assertEqual(self.coordinator.payments.get_payment(payment_id)["status"], "RECONCILED_NOT_SETTLED")
        payment_history = self.coordinator.payments.history(payment_id)
        self.assertEqual(sum(e["event_id"] == args[1] for e in payment_history), 1)
        after = self.coordinator.inspect(payload["operation_id"])
        self.assertEqual(sum(
            e["phase"] == "RECONCILIATION" and e["detail"].get("event_id") == args[1]
            for e in after["events"]
        ), 1)
        self.assertEqual(resumed["final_decision"], "DEFERRED")
        self.assertFalse(resumed["funds_moved"])


if __name__ == "__main__":
    unittest.main()
