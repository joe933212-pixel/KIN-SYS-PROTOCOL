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

    def test_settlement_rejects_payment_relinked_to_another_obligation_in_both(self):
        systems = self.make_pair()
        for system in systems:
            if hasattr(system, "db"):
                system.create_obligation("OBL-OTHER", 50000, "UGX", "OTHER-ASSESSMENT")
                system.db.execute(
                    "UPDATE payments SET obligation_id='OBL-OTHER' WHERE payment_id='PAY-1'"
                )
                system.db.commit()
            else:
                system.create_obligation("OBL-OTHER", 50000, "UGX", "OTHER-ASSESSMENT")
                system.connection.execute(
                    "UPDATE transfers SET liability_key='OBL-OTHER' WHERE transfer_key='PAY-1'"
                )
                system.connection.commit()

        errors = []
        for system in systems:
            try:
                system.record_settlement(
                    "PAY-1", "SET-RELINKED", "SETTLEMENT-REF",
                    amount_minor=50000, currency="UGX",
                )
            except ValueError as exc:
                errors.append(str(exc))
        self.assertEqual(errors, ["PAYMENT_RECORD_INTEGRITY_MISMATCH"] * 2)
        for system in systems:
            self.assertNotEqual(system.get_payment("PAY-1")["status"], "SETTLED")
            self.assertNotIn(
                "SET-RELINKED", [event["event_id"] for event in system.history("PAY-1")]
            )

    def test_settlement_rejects_changed_obligation_in_both_implementations(self):
        systems = self.make_pair()
        for system in systems:
            connection = getattr(system, "db", getattr(system, "connection", None))
            connection.execute(
                "UPDATE obligations SET amount_minor=51000 WHERE obligation_id='OBL-1'"
            ) if hasattr(system, "db") else connection.execute(
                "UPDATE liabilities SET value_minor=51000 WHERE liability_key='OBL-1'"
            )
            connection.commit()

        errors = []
        for system in systems:
            try:
                system.record_settlement(
                    "PAY-1", "SET-MISMATCH", "SETTLEMENT-REF",
                    amount_minor=50000, currency="UGX",
                )
            except ValueError as exc:
                errors.append(str(exc))
        self.assertEqual(errors, ["OBLIGATION_PAYMENT_MISMATCH"] * 2)
        self.assert_pair_equal(systems)
        self.assertEqual(self.vector(systems[0])["status"], "INSTRUCTION_CREATED")
        self.assertEqual(self.vector(systems[0])["history_types"], ["INSTRUCTION_CREATED"])

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

    def test_duplicate_settlement_retry_does_not_duplicate_history(self):
        systems = self.make_pair()
        for system in systems:
            system.record_settlement("PAY-1", "SET-1", "SETTLEMENT-REF",
                                     amount_minor=50000, currency="UGX")
            system.record_settlement("PAY-1", "SET-1", "SETTLEMENT-REF",
                                     amount_minor=50000, currency="UGX")
        self.assert_pair_equal(systems)
        self.assertEqual(self.vector(systems[0])["history_types"],
                         ["INSTRUCTION_CREATED", "SETTLEMENT_EVIDENCE"])

    def test_conflicting_event_id_reuse_is_rejected_by_both(self):
        systems = self.make_pair()
        errors = []
        for system in systems:
            system.record_provider_ack("PAY-1", "ACK-1", "UNKNOWN")
            try:
                system.record_provider_ack("PAY-1", "ACK-1", "ACCEPTED", "PROVIDER-REF")
            except ValueError as exc:
                errors.append(str(exc))
        self.assertEqual(errors, ["EVENT_ID_CONFLICT"] * 2)
        self.assert_pair_equal(systems)
        self.assertEqual(self.vector(systems[0])["status"], "UNKNOWN_PENDING_RECONCILIATION")


if __name__ == "__main__":
    unittest.main()
