# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Offline queue tests; replay is never treated as institutional approval."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge_queue import EdgeOperationQueue


class EdgeOperationQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "edge.sqlite")
        self.queue = EdgeOperationQueue(self.path)
        self.queue.enqueue(
            "OP-1", {"case_id": "CASE-1", "action": "REQUEST_REVIEW"},
            evidence_version="EVIDENCE-1", rule_version="RULE-1",
        )

    def tearDown(self):
        self.queue.close()
        self.temp.cleanup()

    def test_enqueue_retry_is_idempotent(self):
        again = self.queue.enqueue(
            "OP-1", {"action": "REQUEST_REVIEW", "case_id": "CASE-1"},
            evidence_version="EVIDENCE-1", rule_version="RULE-1",
        )
        self.assertEqual(again["status"], "QUEUED_OFFLINE")
        self.assertEqual(len(self.queue.history("OP-1")), 1)

    def test_operation_id_conflict_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "EDGE_OPERATION_ID_CONFLICT"):
            self.queue.enqueue(
                "OP-1", {"case_id": "OTHER", "action": "REQUEST_REVIEW"},
                evidence_version="EVIDENCE-1", rule_version="RULE-1",
            )

    def test_current_context_only_becomes_ready_for_central_review(self):
        result = self.queue.replay(
            "OP-1", current_evidence_version="EVIDENCE-1",
            current_rule_version="RULE-1", authority_active=True,
        )
        self.assertEqual(result["status"], "READY_FOR_CENTRAL_REVIEW")
        self.assertNotEqual(result["status"], "APPROVED")

    def test_stale_evidence_is_deferred(self):
        result = self.queue.replay(
            "OP-1", current_evidence_version="EVIDENCE-2",
            current_rule_version="RULE-1", authority_active=True,
        )
        self.assertEqual(result["status"], "DEFERRED_STALE_EVIDENCE")
        self.assertEqual(result["replay_reason"], "EVIDENCE_VERSION_CHANGED_WHILE_OFFLINE")

    def test_rule_drift_is_deferred(self):
        result = self.queue.replay(
            "OP-1", current_evidence_version="EVIDENCE-1",
            current_rule_version="RULE-2", authority_active=True,
        )
        self.assertEqual(result["status"], "DEFERRED_RULE_VERSION_MISMATCH")

    def test_revoked_authority_is_rejected_at_reconnect(self):
        result = self.queue.replay(
            "OP-1", current_evidence_version="EVIDENCE-1",
            current_rule_version="RULE-1", authority_active=False,
        )
        self.assertEqual(result["status"], "REJECTED_AUTHORITY_REVOKED")

    def test_deferred_operation_requires_new_id_after_context_changes(self):
        deferred = self.queue.replay(
            "OP-1", current_evidence_version="EVIDENCE-2",
            current_rule_version="RULE-1", authority_active=True,
        )
        self.assertEqual(deferred["status"], "DEFERRED_STALE_EVIDENCE")
        same_id_retry = self.queue.replay(
            "OP-1", current_evidence_version="EVIDENCE-1",
            current_rule_version="RULE-1", authority_active=True,
        )
        self.assertEqual(same_id_retry["status"], "DEFERRED_STALE_EVIDENCE")
        fresh = self.queue.enqueue(
            "OP-2", {"case_id": "CASE-1", "action": "REQUEST_REVIEW", "evidence_ref": "EVIDENCE-2"},
            evidence_version="EVIDENCE-2", rule_version="RULE-1",
        )
        result = self.queue.replay(
            "OP-2", current_evidence_version="EVIDENCE-2",
            current_rule_version="RULE-1", authority_active=True,
        )
        self.assertEqual(fresh["status"], "QUEUED_OFFLINE")
        self.assertEqual(result["status"], "READY_FOR_CENTRAL_REVIEW")

    def test_queue_and_history_survive_reopen(self):
        self.queue.replay(
            "OP-1", current_evidence_version="EVIDENCE-1",
            current_rule_version="RULE-1", authority_active=True,
        )
        self.queue.close()
        self.queue = EdgeOperationQueue(self.path)
        self.assertEqual(self.queue.get("OP-1")["status"], "READY_FOR_CENTRAL_REVIEW")
        self.assertEqual(len(self.queue.history("OP-1")), 2)


if __name__ == "__main__":
    unittest.main()
