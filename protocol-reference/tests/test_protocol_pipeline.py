# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""End-to-end MINAH -> IVM -> CEE lifecycle and recovery tests."""
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol_pipeline import (
    ExecutionConflict, KinSysProtocolPipeline, LIFECYCLE, LifecycleViolation,
)


def request(**overrides):
    value = {
        "operation_id": "OP-001",
        "event_id": "EV-001",
        "process_id": "PROC-001",
        "actor_id": "AUTHORITY-001",
        "event_type": "ACTIVATE",
        "jurisdiction": "UG-DEMO",
        "rule_set": "KIN-SYS-DEMO-RULES",
        "rule_version": "1.0",
        "authority_active": True,
        "eligible": True,
        "evidence": {
            "consignment_id": "PROC-001",
            "commodity": "SYNTHETIC_MAIZE",
            "origin": "UGANDA-DEMO",
            "destination": "BORDER-DEMO",
            "rule_refs": ["SYNTHETIC-RULE-1"],
        },
    }
    value.update(overrides)
    return value


class ProtocolPipelineTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = KinSysProtocolPipeline()
        self.addCleanup(self.pipeline.close)

    def test_complete_path_runs_minah_ivm_and_cee_in_order(self):
        result = self.pipeline.execute(request())
        self.assertEqual(result["status"], "COMMITTED")
        self.assertFalse(result["funds_moved"])
        self.assertEqual(result["minah_disposition"]["status"], "ADVISORY_ONLY")
        self.assertFalse(result["minah_disposition"]["may_authorise"])
        self.assertEqual(result["ivm_decision"]["to_state"], "ACTIVE")
        self.assertEqual([event["phase"] for event in result["cee_events"]], list(LIFECYCLE))
        self.assertEqual(result["commit_scope"], "LOCAL_IVM_SQLITE_ONLY_NOT_DISTRIBUTED_FINALITY")

    def test_missing_evidence_defers_before_ivm_execution(self):
        result = self.pipeline.execute(request(evidence={"commodity": "SYNTHETIC_MAIZE"}))
        self.assertEqual(result["status"], "DEFERRED")
        self.assertIsNone(result["ivm_decision"])
        self.assertEqual(self.pipeline.cee.get("OP-001")["phase"], "FINALIZED")
        self.assertEqual(self.pipeline.cee.events("OP-001")[-1]["status"], "DEFERRED")
        with self.assertRaises(Exception):
            self.pipeline.ivm.process("PROC-001")

    def test_wrong_actor_is_rejected_by_ivm_and_not_committed(self):
        result = self.pipeline.execute(request(actor_id="UNAUTHORISED-ACTOR"))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["reason"], "AUTHORITY_MISMATCH")
        self.assertIsNone(result["ivm_decision"]["to_state"])
        self.assertEqual(self.pipeline.ivm.process("PROC-001")["state"], "PENDING")

    def test_revoked_authority_is_rejected(self):
        result = self.pipeline.execute(request(authority_active=False))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["reason"], "AUTHORITY_REVOKED")
        self.assertEqual(self.pipeline.ivm.process("PROC-001")["state"], "PENDING")

    def test_rule_ineligibility_is_rejected(self):
        result = self.pipeline.execute(request(eligible=False))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["reason"], "RULE_REJECTED")
        self.assertEqual(self.pipeline.ivm.process("PROC-001")["state"], "PENDING")

    def test_operation_id_cannot_be_reused_for_different_payload(self):
        self.pipeline.execute(request())
        with self.assertRaises(ExecutionConflict):
            self.pipeline.execute(request(rule_version="2.0"))

    def test_event_id_conflict_is_rejected_by_ivm(self):
        self.pipeline.execute(request())
        with self.assertRaises(Exception):
            self.pipeline.execute(request(operation_id="OP-002", event_id="EV-001", process_id="PROC-002"))

    def test_retry_after_ivm_commit_before_cee_ack_recovers_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            cee_db = str(Path(tmp) / "cee.sqlite3")
            ivm_db = str(Path(tmp) / "ivm.sqlite3")
            pipeline = KinSysProtocolPipeline(cee_database=cee_db, ivm_database=ivm_db)
            try:
                with self.assertRaisesRegex(RuntimeError, "INJECTED_FAILURE_AFTER_IVM_COMMIT"):
                    pipeline.execute(request(), fail_after_ivm_commit=True)
            finally:
                pipeline.close()
            resumed = KinSysProtocolPipeline(cee_database=cee_db, ivm_database=ivm_db)
            try:
                result = resumed.execute(request())
                self.assertEqual(result["status"], "COMMITTED")
                self.assertTrue(result["duplicate"])
                self.assertEqual(resumed.ivm.process("PROC-001")["state"], "ACTIVE")
                self.assertEqual(len(resumed.ivm.history("PROC-001")), 1)
                self.assertEqual([e["phase"] for e in resumed.cee.events("OP-001")][-2:], ["COMMITTED", "FINALIZED"])
            finally:
                resumed.close()

    def test_finalized_operation_is_idempotent(self):
        first = self.pipeline.execute(request())
        second = self.pipeline.execute(request())
        self.assertEqual(second["status"], "COMMITTED")
        self.assertTrue(second["duplicate"])
        self.assertEqual(len(self.pipeline.ivm.history("PROC-001")), 1)

    def test_illegal_lifecycle_transition_is_rejected(self):
        self.pipeline.cee.start("OP-X", request(operation_id="OP-X", event_id="EV-X", process_id="PROC-X"))
        with self.assertRaises(LifecycleViolation):
            self.pipeline.cee.advance("OP-X", "COMMITTED")


if __name__ == "__main__":
    unittest.main()
