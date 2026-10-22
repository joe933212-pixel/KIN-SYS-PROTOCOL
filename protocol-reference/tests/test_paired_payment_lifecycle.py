# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Paired observable-contract tests for independent payment implementations."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from payment_lifecycle import PaymentLifecycle
from comparators.conventional_payment_lifecycle import ConventionalPaymentLifecycle


class PairedPaymentLifecycleTests(unittest.TestCase):
    def make_pair(self):
        systems = [PaymentLifecycle(), ConventionalPaymentLifecycle()]
        self.addCleanup(lambda: [system.close() for system in systems])
        for system in systems:
            system.create_obligation("OBL-1", 50000, "UGX", "ASSESSMENT-1")
            system.create_instruction("PAY-1", "OBL-1")
        return systems

    @staticmethod
    def vector(system):
        row = system.get_payment("PAY-1")
        return {
            "payment_id": row["payment_id"],
            "obligation_id": row["obligation_id"],
            "amount_minor": row["amount_minor"],
            "currency": row["currency"],
            "status": row["status"],
            "settlement_reference": row["settlement_reference"],
            "history_types": [item["event_type"] for item in system.history("PAY-1")],
        }

    def assert_pair_equal(self, systems):
        self.assertEqual(self.vector(systems[0]), self.vector(systems[1]))

    def test_same_instruction_retry_has_same_observable_result(self):
        systems = self.make_pair()
        for system in systems:
            system.create_instruction("PAY-1", "OBL-1")
        self.assert_pair_equal(systems)
        self.assertEqual(self.vector(systems[0])["history_types"], ["INSTRUCTION_CREATED"])

    def test_provider_acceptance_is_not_settlement_in_both(self):
        systems = self.make_pair()
        for system in systems:
            system.record_provider_ack("PAY-1", "ACK-1", "ACCEPTED", "PROVIDER-REF")
        self.assert_pair_equal(systems)
        self.assertEqual(self.vector(systems[0])["status"], "PROVIDER_ACCEPTED_SETTLEMENT_UNCONFIRMED")

    def test_unknown_outcome_remains_pending_reconciliation_in_both(self):
        systems = self.make_pair()
        for system in systems:
            system.record_provider_ack("PAY-1", "ACK-1", "UNKNOWN")
            system.record_reconciliation("PAY-1", "REC-1", "UNKNOWN")
        self.assert_pair_equal(systems)
        self.assertEqual(self.vector(systems[0])["status"], "UNKNOWN_PENDING_RECONCILIATION")

    def test_matching_settlement_evidence_finalizes_both_local_records(self):
        systems = self.make_pair()
        for system in systems:
            system.record_settlement("PAY-1", "SET-1", "SETTLEMENT-REF",
                                     amount_minor=50000, currency="UGX")
        self.assert_pair_equal(systems)
        self.assertEqual(self.vector(systems[0])["status"], "SETTLED")

    def test_mismatched_settlement_is_rejected_by_both(self):
        systems = self.make_pair()
        errors = []
        for system in systems:
            try:
                system.record_settlement("PAY-1", "SET-BAD", "SETTLEMENT-REF",
                                         amount_minor=50001, currency="UGX")
            except ValueError as exc:
                errors.append(str(exc))
        self.assertEqual(errors, ["SETTLEMENT_AMOUNT_OR_CURRENCY_MISMATCH"] * 2)
        self.assert_pair_equal(systems)
        self.assertEqual(self.vector(systems[0])["status"], "INSTRUCTION_CREATED")

    def test_reconciliation_cannot_invent_settlement(self):
        systems = self.make_pair()
        errors = []
        for system in systems:
            try:
                system.record_reconciliation("PAY-1", "REC-SETTLED", "SETTLED")
            except ValueError as exc:
                errors.append(str(exc))
        self.assertEqual(errors, ["SETTLEMENT_EVIDENCE_REQUIRED"] * 2)
        self.assert_pair_equal(systems)
        self.assertNotEqual(self.vector(systems[0])["status"], "SETTLED")


if __name__ == "__main__":
    unittest.main()
