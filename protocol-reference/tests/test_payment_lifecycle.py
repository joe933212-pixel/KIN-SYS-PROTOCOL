# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Payment-boundary lifecycle tests; no real provider or funds."""
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from payment_lifecycle import PaymentLifecycle


class PaymentLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "payments.sqlite")
        self.payments = PaymentLifecycle(self.db_path)
        self.payments.create_obligation("OBL-1", 50000, "UGX", "SYNTHETIC-ASSESSMENT-1")
        self.payments.create_instruction("PAY-1", "OBL-1")

    def tearDown(self):
        self.payments.close()
        self.temp.cleanup()

    def test_instruction_retry_is_idempotent_and_does_not_resubmit(self):
        first = self.payments.create_instruction("PAY-1", "OBL-1")
        second = self.payments.create_instruction("PAY-1", "OBL-1")
        self.assertEqual(first["payment_id"], second["payment_id"])
        self.assertEqual(second["status"], "INSTRUCTION_CREATED")
        self.assertEqual(len(self.payments.history("PAY-1")), 1)

    def test_one_obligation_cannot_receive_second_payment_instruction(self):
        with self.assertRaisesRegex(ValueError, "OBLIGATION_ALREADY_HAS_PAYMENT_INSTRUCTION"):
            self.payments.create_instruction("PAY-2", "OBL-1")

    def test_provider_acceptance_is_not_settlement(self):
        result = self.payments.record_provider_ack("PAY-1", "EV-ACK-1", "ACCEPTED", "PROVIDER-REF")
        self.assertEqual(result["status"], "PROVIDER_ACCEPTED_SETTLEMENT_UNCONFIRMED")
        self.assertIsNone(result["settlement_reference"])

    def test_timeout_unknown_stays_unknown_and_reconciliation_does_not_resubmit(self):
        self.payments.record_provider_ack("PAY-1", "EV-ACK-1", "UNKNOWN")
        result = self.payments.record_reconciliation("PAY-1", "EV-REC-1", "UNKNOWN")
        self.assertEqual(result["status"], "UNKNOWN_PENDING_RECONCILIATION")
        self.assertEqual(len(self.payments.history("PAY-1")), 3)

    def test_settlement_requires_matching_amount_and_currency(self):
        self.payments.record_provider_ack("PAY-1", "EV-ACK-1", "ACCEPTED", "PROVIDER-REF")
        with self.assertRaisesRegex(ValueError, "SETTLEMENT_AMOUNT_OR_CURRENCY_MISMATCH"):
            self.payments.record_settlement(
                "PAY-1", "EV-SETTLE-BAD", "SET-REF", amount_minor=49999, currency="UGX"
            )
        self.assertNotEqual(self.payments.get_payment("PAY-1")["status"], "SETTLED")

    def test_settlement_updates_payment_and_obligation_only_with_evidence(self):
        self.payments.record_settlement(
            "PAY-1", "EV-SETTLE-1", "SET-REF-1", amount_minor=50000, currency="UGX"
        )
        self.assertEqual(self.payments.get_payment("PAY-1")["status"], "SETTLED")
        obligation = self.payments.db.execute(
            "SELECT status FROM obligations WHERE obligation_id='OBL-1'"
        ).fetchone()
        self.assertEqual(obligation["status"], "SETTLED")

    def test_same_settlement_retry_is_idempotent(self):
        args = ("PAY-1", "EV-SETTLE-1", "SET-REF-1")
        first = self.payments.record_settlement(
            *args, amount_minor=50000, currency="UGX"
        )
        second = self.payments.record_settlement(
            *args, amount_minor=50000, currency="UGX"
        )
        self.assertEqual(first["settlement_reference"], second["settlement_reference"])
        self.assertEqual(len(self.payments.history("PAY-1")), 2)  # instruction + one settlement event

    def test_payment_id_cannot_be_reused_for_different_request(self):
        self.payments.create_obligation("OBL-2", 60000, "UGX", "SYNTHETIC-ASSESSMENT-2")
        with self.assertRaisesRegex(ValueError, "PAYMENT_ID_CONFLICT"):
            self.payments.create_instruction("PAY-1", "OBL-2")

    def test_reconciliation_cannot_claim_settlement_without_settlement_evidence(self):
        with self.assertRaisesRegex(ValueError, "SETTLEMENT_EVIDENCE_REQUIRED"):
            self.payments.record_reconciliation("PAY-1", "EV-REC-1", "SETTLED")
        self.assertNotEqual(self.payments.get_payment("PAY-1")["status"], "SETTLED")

    def test_event_id_reuse_with_different_evidence_is_rejected(self):
        self.payments.record_provider_ack("PAY-1", "EV-ACK-1", "UNKNOWN")
        with self.assertRaisesRegex(ValueError, "EVENT_ID_CONFLICT"):
            self.payments.record_provider_ack("PAY-1", "EV-ACK-1", "ACCEPTED")

    def test_concurrent_instructions_cannot_create_two_payments_for_one_obligation(self):
        self.payments.create_obligation("OBL-CONCURRENT", 70000, "UGX", "SYNTHETIC-CONCURRENT-ASSESSMENT")
        barrier = threading.Barrier(2)
        outcomes = []
        outcomes_lock = threading.Lock()

        def attempt(payment_id):
            connection = PaymentLifecycle(self.db_path)
            try:
                barrier.wait(timeout=5)
                result = connection.create_instruction(payment_id, "OBL-CONCURRENT")
                outcome = ("created", result["payment_id"])
            except Exception as exc:
                outcome = ("error", str(exc))
            finally:
                connection.close()
            with outcomes_lock:
                outcomes.append(outcome)

        threads = [
            threading.Thread(target=attempt, args=("PAY-CONCURRENT-A",)),
            threading.Thread(target=attempt, args=("PAY-CONCURRENT-B",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads), "concurrent writers hung")
        self.assertEqual(sum(kind == "created" for kind, _ in outcomes), 1, outcomes)
        errors = [value for kind, value in outcomes if kind == "error"]
        self.assertEqual(errors, ["OBLIGATION_ALREADY_HAS_PAYMENT_INSTRUCTION"], outcomes)
        rows = self.payments.db.execute(
            "SELECT payment_id FROM payments WHERE obligation_id='OBL-CONCURRENT'"
        ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_instruction_failure_rolls_back_payment_and_obligation_status(self):
        self.payments.create_obligation("OBL-2", 60000, "UGX", "SYNTHETIC-ASSESSMENT-2")
        original_event = self.payments._event

        def fail_event(*args, **kwargs):
            raise ValueError("INJECTED_EVENT_WRITE_FAILURE")

        self.payments._event = fail_event
        try:
            with self.assertRaisesRegex(ValueError, "INJECTED_EVENT_WRITE_FAILURE"):
                self.payments.create_instruction("PAY-2", "OBL-2")
        finally:
            self.payments._event = original_event

        payment = self.payments.db.execute(
            "SELECT payment_id FROM payments WHERE obligation_id='OBL-2'"
        ).fetchone()
        obligation = self.payments.db.execute(
            "SELECT status FROM obligations WHERE obligation_id='OBL-2'"
        ).fetchone()
        self.assertIsNone(payment)
        self.assertEqual(obligation["status"], "RECORDED")

    def test_payment_records_survive_database_reopen(self):
        self.payments.close()
        self.payments = PaymentLifecycle(self.db_path)
        self.assertEqual(self.payments.get_payment("PAY-1")["status"], "INSTRUCTION_CREATED")
        self.assertEqual(len(self.payments.history("PAY-1")), 1)


if __name__ == "__main__":
    unittest.main()
