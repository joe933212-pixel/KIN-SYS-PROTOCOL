# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""End-to-end synthetic KIN-SYS demonstrator tests; no real services or funds."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integrated_demo import IntegratedDemonstrator, SyntheticTaxRules


class IntegratedDemonstratorTests(unittest.TestCase):
    def run_case(self, **kwargs):
        demo = IntegratedDemonstrator()
        self.addCleanup(demo.close)
        return demo.run_case(**kwargs)

    def test_happy_path_integrates_authority_minah_tax_and_simulated_payment(self):
        result = self.run_case()
        self.assertEqual(result["status"], "DEMO_READY_AFTER_SIMULATED_REMITTANCE")
        self.assertEqual(result["institutional_decisions"]["origin"]["result"], "APPROVE")
        self.assertEqual(result["institutional_decisions"]["destination"]["result"], "APPROVE")
        self.assertEqual(result["minah_advisory"]["may_authorise"], False)
        self.assertEqual(result["statutory_obligation"]["legal_status"], "SYNTHETIC_DEMO_RULE_NOT_LAW")
        self.assertEqual(result["statutory_obligation"]["assessed_amount_ugx"], 50_000)
        self.assertEqual(result["payment"]["status"], "SIMULATED_CONFIRMED")
        self.assertFalse(result["payment"]["funds_moved"])
        self.assertEqual(result["payment_lifecycle"]["status"], "SIMULATED_SETTLED")
        self.assertEqual(result["payment_lifecycle"]["recorded_status"], "SETTLED")
        self.assertFalse(result["payment_lifecycle"]["funds_moved"])
        self.assertEqual(len(result["payment_lifecycle"]["history"]), 3)
        self.assertEqual(len(result["history"]), 2)

    def test_destination_rejection_cannot_be_overridden_by_origin_approval(self):
        result = self.run_case(destination_decision="REJECT")
        self.assertEqual(result["status"], "INSTITUTIONAL_DECISION_REJECTED")
        self.assertEqual(result["institutional_decisions"]["origin"]["result"], "APPROVE")
        self.assertEqual(result["institutional_decisions"]["destination"]["result"], "REJECT")
        self.assertIsNone(result["statutory_obligation"])
        self.assertIsNone(result["payment"])

    def test_payment_timeout_remains_unresolved_not_paid(self):
        result = self.run_case(payment_outcome="TIMEOUT_UNKNOWN")
        self.assertEqual(result["status"], "PENDING_PAYMENT_RECONCILIATION")
        self.assertEqual(result["payment"]["status"], "UNKNOWN_PENDING_RECONCILIATION")
        self.assertFalse(result["payment"]["funds_moved"])
        self.assertEqual(result["payment_lifecycle"]["status"], "UNKNOWN_PENDING_RECONCILIATION")
        self.assertEqual(result["payment_lifecycle"]["recorded_status"], "UNKNOWN_PENDING_RECONCILIATION")
        self.assertFalse(result["payment_lifecycle"]["funds_moved"])

    def test_declined_payment_is_not_settled(self):
        result = self.run_case(payment_outcome="DECLINED")
        self.assertEqual(result["status"], "PENDING_PAYMENT_RESOLUTION")
        self.assertEqual(result["payment_lifecycle"]["status"], "SIMULATED_DECLINED")
        self.assertEqual(result["payment_lifecycle"]["recorded_status"], "PROVIDER_DECLINED")
        self.assertIsNone(result["payment_lifecycle"]["settlement_reference"])

    def test_minah_missing_evidence_does_not_create_institutional_approval(self):
        result = self.run_case(minah_evidence={"commodity": "SYNTHETIC_MAIZE"})
        self.assertEqual(result["minah_advisory"]["status"], "NEEDS_INFORMATION")
        self.assertEqual(result["minah_advisory"]["may_authorise"], False)
        # Institutional decisions are still made only by the configured test actors.
        self.assertEqual(result["institutional_decisions"]["origin"]["result"], "APPROVE")

    def test_unauthorised_actor_is_rejected(self):
        result = self.run_case(origin_actor="UNAUTHORISED-TEST")
        self.assertEqual(result["institutional_decisions"]["origin"]["operation"]["reason"],
                         "AUTHORITY_MISMATCH")
        self.assertEqual(result["status"], "REJECTED_AUTHORITY_ACTION_INVALID")
        self.assertIsNone(result["statutory_obligation"])

    def test_revoked_origin_authority_blocks_decision_and_downstream_stage(self):
        result = self.run_case(revoke_origin_before_decision=True)
        self.assertEqual(result["institutional_decisions"]["origin"]["operation"]["reason"],
                         "AUTHORITY_REVOKED")
        self.assertEqual(result["institutional_decisions"]["destination"]["operation"]["status"],
                         "NOT_ATTEMPTED")
        self.assertEqual(result["status"], "REJECTED_AUTHORITY_ACTION_INVALID")
        self.assertIsNone(result["statutory_obligation"])
        self.assertEqual(result["history"], [])

    def test_rule_version_drift_blocks_origin_and_downstream_stage(self):
        result = self.run_case(origin_rule_version_override="SYNTHETIC-OLD-RULE")
        self.assertEqual(result["institutional_decisions"]["origin"]["operation"]["reason"],
                         "RULE_VERSION_MISMATCH")
        self.assertEqual(result["institutional_decisions"]["destination"]["operation"]["status"],
                         "NOT_ATTEMPTED")
        self.assertEqual(result["status"], "REJECTED_AUTHORITY_ACTION_INVALID")
        self.assertEqual(result["institutional_decisions"]["destination"]["operation"]["status"], "NOT_ATTEMPTED")
        self.assertIsNone(result["statutory_obligation"])

    def test_disconnected_edge_profile_defers_online_required_decisions(self):
        result = self.run_case(edge_connected=False)
        self.assertEqual(result["profile"], "SIMULATED_EDGE_DISCONNECTED")
        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(result["history"], [])
        self.assertIsNone(result["payment"])
        self.assertEqual(result["edge_queue"]["status"], "QUEUED_OFFLINE")
        self.assertEqual(result["edge_queue"]["payload"]["action"], "REQUEST_CENTRAL_REVIEW")
        self.assertIn("OFFLINE_QUEUE_REPLAY_IS_NOT_INSTITUTIONAL_APPROVAL", result["claims"])

    def test_reconnect_with_current_context_only_enters_central_review_queue(self):
        result = self.run_case(edge_connected=False, edge_reconnect=True)
        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(result["edge_queue"]["status"], "READY_FOR_CENTRAL_REVIEW")
        self.assertEqual(result["reason"], "READY_FOR_CENTRAL_REVIEW_NOT_INSTITUTIONAL_APPROVAL")
        self.assertEqual(result["history"], [])

    def test_reconnect_with_stale_evidence_remains_deferred(self):
        result = self.run_case(
            edge_connected=False, edge_reconnect=True,
            edge_current_evidence_version="SYNTHETIC-EVIDENCE-2",
        )
        self.assertEqual(result["edge_queue"]["status"], "DEFERRED_STALE_EVIDENCE")
        self.assertEqual(result["status"], "PENDING")

    def test_reconnect_with_rule_drift_remains_deferred(self):
        result = self.run_case(
            edge_connected=False, edge_reconnect=True,
            edge_current_rule_version="SYNTHETIC-OLD-RULE",
        )
        self.assertEqual(result["edge_queue"]["status"], "DEFERRED_RULE_VERSION_MISMATCH")
        self.assertEqual(result["status"], "PENDING")

    def test_reconnect_with_revoked_authority_is_rejected(self):
        result = self.run_case(
            edge_connected=False, edge_reconnect=True, edge_authority_active=False,
        )
        self.assertEqual(result["edge_queue"]["status"], "REJECTED_AUTHORITY_REVOKED")
        self.assertEqual(result["status"], "PENDING")

    def test_synthetic_exemption_has_explicit_zero_obligation_without_payment(self):
        result = self.run_case(exempt=True)
        self.assertEqual(result["status"], "DEMO_READY_UNDER_SYNTHETIC_EXEMPTION")
        self.assertEqual(result["statutory_obligation"]["assessed_amount_ugx"], 0)
        self.assertEqual(result["payment"]["status"], "NOT_REQUIRED_EXEMPT")
        self.assertEqual(result["payment_lifecycle"]["status"], "NOT_REQUIRED_EXEMPT")

    def test_restart_after_obligation_commit_resumes_workflow_without_duplicate_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = str(Path(tmp) / "kin-sys-recovery")
            script = r"""
import os
import sys
from integrated_demo import IntegratedDemonstrator
from payment_lifecycle import PaymentLifecycle
original = PaymentLifecycle.create_instruction
def crash_before_instruction(self, payment_id, obligation_id):
    if payment_id.startswith("RECOVERY-CASE:"):
        os._exit(82)
    return original(self, payment_id, obligation_id)
PaymentLifecycle.create_instruction = crash_before_instruction
demo = IntegratedDemonstrator(database_prefix=sys.argv[1])
demo.run_case(consignment_id="RECOVERY-CASE")
"""
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            child = subprocess.run(
                [sys.executable, "-c", script, prefix],
                env=env, capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(child.returncode, 82, child.stderr)

            resumed = IntegratedDemonstrator(database_prefix=prefix)
            self.addCleanup(resumed.close)
            result = resumed.run_case(consignment_id="RECOVERY-CASE")

            self.assertEqual(result["status"], "DEMO_READY_AFTER_SIMULATED_REMITTANCE")
            self.assertEqual(result["institutional_decisions"]["origin"]["operation"]["status"], "ACCEPTED")
            self.assertEqual(result["institutional_decisions"]["destination"]["operation"]["status"], "ACCEPTED")
            self.assertEqual(result["payment_lifecycle"]["recorded_status"], "SETTLED")
            self.assertFalse(result["payment_lifecycle"]["funds_moved"])
            self.assertEqual(len(result["history"]), 2)
            self.assertEqual(len(result["payment_lifecycle"]["history"]), 3)
            obligations = resumed.payment_lifecycle.db.execute(
                "SELECT COUNT(*) FROM obligations WHERE obligation_id='RECOVERY-CASE:OBL:TAX:1'"
            ).fetchone()[0]
            payments = resumed.payment_lifecycle.db.execute(
                "SELECT COUNT(*) FROM payments WHERE obligation_id='RECOVERY-CASE:OBL:TAX:1'"
            ).fetchone()[0]
            self.assertEqual(obligations, 1)
            self.assertEqual(payments, 1)

    def test_restart_after_provider_ack_recovers_without_duplicate_payment(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = str(Path(tmp) / "kin-sys-ack-recovery")
            script = r"""
import os
import sys
from integrated_demo import IntegratedDemonstrator
from payment_lifecycle import PaymentLifecycle
def crash_before_settlement(self, *args, **kwargs):
    os._exit(83)
PaymentLifecycle.record_settlement = crash_before_settlement
demo = IntegratedDemonstrator(database_prefix=sys.argv[1])
demo.run_case(consignment_id="ACK-RECOVERY-CASE")
"""
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            child = subprocess.run(
                [sys.executable, "-c", script, prefix],
                env=env, capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(child.returncode, 83, child.stderr)

            resumed = IntegratedDemonstrator(database_prefix=prefix)
            self.addCleanup(resumed.close)
            result = resumed.run_case(consignment_id="ACK-RECOVERY-CASE")
            payment_id = "ACK-RECOVERY-CASE:TAX:1"
            payment = resumed.payment_lifecycle.get_payment(payment_id)
            history = resumed.payment_lifecycle.history(payment_id)

            self.assertEqual(result["status"], "DEMO_READY_AFTER_SIMULATED_REMITTANCE")
            self.assertEqual(payment["status"], "SETTLED")
            self.assertEqual(payment["settlement_reference"], f"SIMULATED-SETTLEMENT:{payment_id}")
            self.assertEqual(
                [event["event_type"] for event in history],
                ["INSTRUCTION_CREATED", "PROVIDER_ACKNOWLEDGEMENT", "SETTLEMENT_EVIDENCE"],
            )
            self.assertEqual(len({event["event_id"] for event in history}), 3)
            self.assertFalse(result["payment_lifecycle"]["funds_moved"])

    def test_restart_after_origin_approval_respects_destination_authority_revocation(self):
        from workflows.institutional_workflow import InstitutionalWorkflow

        with tempfile.TemporaryDirectory() as tmp:
            prefix = str(Path(tmp) / "origin-approval-recovery")
            script = r"""
import os
import sys
from integrated_demo import IntegratedDemonstrator
from workflows.institutional_workflow import InstitutionalWorkflow
original = InstitutionalWorkflow.decide
def crash_after_origin_commit(self, **kwargs):
    result = original(self, **kwargs)
    if kwargs.get("stage") == "ORIGIN" and result.get("status") == "ACCEPTED":
        os._exit(84)
    return result
InstitutionalWorkflow.decide = crash_after_origin_commit
demo = IntegratedDemonstrator(database_prefix=sys.argv[1])
demo.run_case(consignment_id="ORIGIN-RECOVERY-CASE")
"""
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            child = subprocess.run(
                [sys.executable, "-c", script, prefix],
                env=env, capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(child.returncode, 84, child.stderr)

            resumed = IntegratedDemonstrator(database_prefix=prefix)
            self.addCleanup(resumed.close)
            resumed.workflow.set_authority_active(
                "ORIGIN-RECOVERY-CASE", "DESTINATION", False
            )
            result = resumed.run_case(consignment_id="ORIGIN-RECOVERY-CASE")

            self.assertEqual(
                result["institutional_decisions"]["origin"]["operation"]["status"],
                "ACCEPTED",
            )
            self.assertTrue(
                result["institutional_decisions"]["origin"]["operation"]["duplicate"]
            )
            self.assertEqual(
                result["institutional_decisions"]["destination"]["operation"]["reason"],
                "AUTHORITY_REVOKED",
            )
            self.assertEqual(result["status"], "REJECTED_AUTHORITY_ACTION_INVALID")
            self.assertIsNone(result["statutory_obligation"])
            self.assertIsNone(result["payment"])
            self.assertEqual(len(result["history"]), 1)
            self.assertFalse(
                any(row["stage"] == "DESTINATION" for row in result["history"])
            )

    def test_restart_after_origin_approval_defers_destination_when_rule_version_changes(self):
        from workflows.institutional_workflow import InstitutionalWorkflow

        with tempfile.TemporaryDirectory() as tmp:
            prefix = str(Path(tmp) / "origin-rule-drift-recovery")
            script = r"""
import os
import sys
from integrated_demo import IntegratedDemonstrator
from workflows.institutional_workflow import InstitutionalWorkflow
original = InstitutionalWorkflow.decide
def crash_after_origin_commit(self, **kwargs):
    result = original(self, **kwargs)
    if kwargs.get("stage") == "ORIGIN" and result.get("status") == "ACCEPTED":
        os._exit(85)
    return result
InstitutionalWorkflow.decide = crash_after_origin_commit
demo = IntegratedDemonstrator(database_prefix=sys.argv[1])
demo.run_case(consignment_id="RULE-DRIFT-RECOVERY-CASE")
"""
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            child = subprocess.run(
                [sys.executable, "-c", script, prefix],
                env=env, capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(child.returncode, 85, child.stderr)

            resumed = IntegratedDemonstrator(database_prefix=prefix)
            self.addCleanup(resumed.close)
            resumed.workflow.set_rule_version(
                "RULE-DRIFT-RECOVERY-CASE", "DESTINATION", "SYNTHETIC-NEW-RULE"
            )
            result = resumed.run_case(
                consignment_id="RULE-DRIFT-RECOVERY-CASE",
                destination_rule_version_override="SYNTHETIC-NEW-RULE",
            )

            self.assertEqual(
                result["institutional_decisions"]["origin"]["operation"]["status"],
                "ACCEPTED",
            )
            self.assertTrue(
                result["institutional_decisions"]["origin"]["operation"]["duplicate"]
            )
            self.assertEqual(
                result["institutional_decisions"]["destination"]["operation"]["reason"],
                "RULE_VERSION_MISMATCH",
            )
            self.assertEqual(result["status"], "REJECTED_AUTHORITY_ACTION_INVALID")
            self.assertIsNone(result["statutory_obligation"])
            self.assertIsNone(result["payment"])
            self.assertEqual(len(result["history"]), 1)

    def test_restart_after_destination_approval_before_obligation_creation(self):
        from payment_lifecycle import PaymentLifecycle

        with tempfile.TemporaryDirectory() as tmp:
            prefix = str(Path(tmp) / "destination-approval-recovery")
            script = r"""
import os
import sys
from integrated_demo import IntegratedDemonstrator
from payment_lifecycle import PaymentLifecycle
def crash_before_obligation(self, *args, **kwargs):
    os._exit(86)
PaymentLifecycle.create_obligation = crash_before_obligation
demo = IntegratedDemonstrator(database_prefix=sys.argv[1])
demo.run_case(consignment_id="DESTINATION-RECOVERY-CASE")
"""
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            child = subprocess.run(
                [sys.executable, "-c", script, prefix],
                env=env, capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(child.returncode, 86, child.stderr)

            resumed = IntegratedDemonstrator(database_prefix=prefix)
            self.addCleanup(resumed.close)
            result = resumed.run_case(consignment_id="DESTINATION-RECOVERY-CASE")

            self.assertEqual(result["status"], "DEMO_READY_AFTER_SIMULATED_REMITTANCE")
            self.assertTrue(result["institutional_decisions"]["origin"]["operation"]["duplicate"])
            self.assertTrue(result["institutional_decisions"]["destination"]["operation"]["duplicate"])
            self.assertEqual(
                [row["stage"] for row in result["history"]],
                ["ORIGIN", "DESTINATION"],
            )
            self.assertEqual(len(result["history"]), 2)
            self.assertEqual(result["payment_lifecycle"]["status"], "SIMULATED_SETTLED")
            self.assertFalse(result["payment_lifecycle"]["funds_moved"])
            obligation_count = resumed.payment_lifecycle.db.execute(
                "SELECT COUNT(*) FROM obligations WHERE obligation_id=?",
                ("DESTINATION-RECOVERY-CASE:OBL:TAX:1",),
            ).fetchone()[0]
            instruction_count = resumed.payment_lifecycle.db.execute(
                "SELECT COUNT(*) FROM payments WHERE payment_id=?",
                ("DESTINATION-RECOVERY-CASE:TAX:1",),
            ).fetchone()[0]
            self.assertEqual(obligation_count, 1)
            self.assertEqual(instruction_count, 1)
            self.assertEqual(len(result["payment_lifecycle"]["history"]), 3)

    def test_reusing_consignment_id_with_different_authority_is_rejected(self):
        from workflows.institutional_workflow import InstitutionalWorkflow, WorkflowConflict
        workflow = InstitutionalWorkflow()
        self.addCleanup(workflow.close)
        workflow.create_case(
            "CASE-CONFLICT", origin_authority="ORIGIN-A",
            destination_authority="DESTINATION-B",
        )
        with self.assertRaisesRegex(WorkflowConflict, "CASE_ID_CONFLICT"):
            workflow.create_case(
                "CASE-CONFLICT", origin_authority="ORIGIN-CHANGED",
                destination_authority="DESTINATION-B",
            )


    def test_restart_after_both_approvals_blocks_obligation_when_origin_authority_is_revoked(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = str(Path(tmp) / "approval-authority-revoked")
            script = r"""
import os
import sys
from integrated_demo import IntegratedDemonstrator
from payment_lifecycle import PaymentLifecycle
def crash_before_obligation(self, *args, **kwargs):
    os._exit(87)
PaymentLifecycle.create_obligation = crash_before_obligation
demo = IntegratedDemonstrator(database_prefix=sys.argv[1])
demo.run_case(consignment_id="AUTHORITY-REVOKED-AFTER-APPROVAL")
"""
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            child = subprocess.run(
                [sys.executable, "-c", script, prefix],
                env=env, capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(child.returncode, 87, child.stderr)

            resumed = IntegratedDemonstrator(database_prefix=prefix)
            self.addCleanup(resumed.close)
            resumed.workflow.set_authority_active(
                "AUTHORITY-REVOKED-AFTER-APPROVAL", "ORIGIN", False
            )
            result = resumed.run_case(
                consignment_id="AUTHORITY-REVOKED-AFTER-APPROVAL"
            )

            self.assertTrue(result["institutional_decisions"]["origin"]["operation"]["duplicate"])
            self.assertTrue(result["institutional_decisions"]["destination"]["operation"]["duplicate"])
            self.assertEqual(result["status"], "REJECTED_AUTHORITY_ACTION_INVALID")
            self.assertIsNone(result["statutory_obligation"])
            self.assertIsNone(result["payment"])
            self.assertEqual(
                resumed.payment_lifecycle.db.execute("SELECT COUNT(*) FROM obligations").fetchone()[0],
                0,
            )

    def test_restart_after_both_approvals_blocks_obligation_when_origin_rule_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = str(Path(tmp) / "approval-rule-version-changed")
            script = r"""
import os
import sys
from integrated_demo import IntegratedDemonstrator
from payment_lifecycle import PaymentLifecycle
def crash_before_obligation(self, *args, **kwargs):
    os._exit(88)
PaymentLifecycle.create_obligation = crash_before_obligation
demo = IntegratedDemonstrator(database_prefix=sys.argv[1])
demo.run_case(consignment_id="RULE-CHANGED-AFTER-APPROVAL")
"""
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            child = subprocess.run(
                [sys.executable, "-c", script, prefix],
                env=env, capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(child.returncode, 88, child.stderr)

            resumed = IntegratedDemonstrator(database_prefix=prefix)
            self.addCleanup(resumed.close)
            resumed.workflow.set_rule_version(
                "RULE-CHANGED-AFTER-APPROVAL", "ORIGIN", "SYNTHETIC-NEW-RULE"
            )
            result = resumed.run_case(
                consignment_id="RULE-CHANGED-AFTER-APPROVAL",
                origin_rule_version_override="SYNTHETIC-NEW-RULE",
            )

            self.assertTrue(result["institutional_decisions"]["origin"]["operation"]["duplicate"])
            self.assertTrue(result["institutional_decisions"]["destination"]["operation"]["duplicate"])
            self.assertEqual(result["status"], "REJECTED_AUTHORITY_ACTION_INVALID")
            self.assertIsNone(result["statutory_obligation"])
            self.assertIsNone(result["payment"])
            self.assertEqual(
                resumed.payment_lifecycle.db.execute("SELECT COUNT(*) FROM obligations").fetchone()[0],
                0,
            )

    def test_tax_calculator_rejects_negative_or_non_integer_amounts(self):
        rules = SyntheticTaxRules()
        for value in (-1, 1.5, "100"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    rules.assess(value)


if __name__ == "__main__":
    unittest.main()
