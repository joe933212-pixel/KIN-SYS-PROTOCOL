# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""End-to-end MINAH -> CEE -> final-decision lifecycle and recovery tests."""
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from authority_adapter import AuthorityGateDecision, UnavailableAuthorityAdapter
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

    def test_cee_owns_one_shared_journal_and_execution_connection(self):
        self.assertIs(self.pipeline.cee.db, self.pipeline.ivm.db)
        tables = {
            row[0]
            for row in self.pipeline.cee.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertTrue({"cee_operations", "cee_events", "processes", "events", "transition_history"} <= tables)

    def test_split_runtime_database_configuration_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "SPLIT_RUNTIME_STORAGE_NO_LONGER_SUPPORTED"):
                KinSysProtocolPipeline(
                    cee_database=str(Path(tmp) / "cee.sqlite3"),
                    ivm_database=str(Path(tmp) / "runtime.sqlite3"),
                )

    def test_complete_path_runs_minah_then_cee_to_final_decision(self):
        result = self.pipeline.execute(request())
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(result["final_decision"], "PASSED")
        self.assertFalse(result["funds_moved"])
        self.assertEqual(result["minah_disposition"]["status"], "ADVISORY_ONLY")
        self.assertFalse(result["minah_disposition"]["may_authorise"])
        self.assertEqual(result["execution_result"]["to_state"], "ACTIVE")
        self.assertEqual([event["phase"] for event in result["cee_events"]], list(LIFECYCLE))
        components = [event["component"] for event in result["component_trace"]]
        self.assertEqual(components[0], "MINAH")
        self.assertNotIn("IVM", components)
        self.assertTrue(all(component == "CEE" for component in components[1:]))
        self.assertEqual(components[-1], "CEE")
        phases = [event["phase"] for event in result["cee_events"]]
        self.assertLess(phases.index("MINAH_REVIEWED"), phases.index("ADMITTED"))
        self.assertEqual(result["component_trace"][-2]["detail"]["execution_result"]["to_state"], "ACTIVE")
        self.assertEqual(result["component_trace"][-1]["detail"]["phase"], "FINALIZED")
        self.assertEqual(result["commit_scope"], "LOCAL_CEE_REFERENCE_STORE_ONLY_NOT_DISTRIBUTED_FINALITY")

    def test_minah_cannot_self_authorise_or_bypass_ivm_boundary(self):
        class SelfAuthorisingMinah:
            def review(self, evidence):
                return {
                    "status": "ADVISORY_ONLY",
                    "missing_fields": [],
                    "source_refs": [],
                    "uncertainty": "LOW",
                    "may_authorise": True,
                    "model_version": "hostile-test-model",
                }

        hostile = KinSysProtocolPipeline(minah_adapter=SelfAuthorisingMinah())
        try:
            result = hostile.execute(request())
            self.assertEqual(result["status"], "REJECTED")
            self.assertEqual(result["final_decision"], "REJECTED")
            self.assertEqual(result["reason"], "MINAH_DISPOSITION_CONTRACT_VIOLATION")
            phases = [event["phase"] for event in result["cee_events"]]
            self.assertNotIn("ADMITTED", phases)
            self.assertIsNone(result["execution_result"])
            self.assertEqual(hostile.cee.get("OP-001")["phase"], "FINALIZED")
            with self.assertRaises(Exception):
                hostile.ivm.process("PROC-001")
        finally:
            hostile.close()

    def test_missing_evidence_defers_before_ivm_execution(self):
        result = self.pipeline.execute(request(evidence={"commodity": "SYNTHETIC_MAIZE"}))
        self.assertEqual(result["status"], "DEFERRED")
        self.assertEqual(result["final_decision"], "DEFERRED")
        phases = [event["phase"] for event in result["cee_events"]]
        self.assertNotIn("ADMITTED", phases)
        self.assertLess(phases.index("MINAH_REVIEWED"), phases.index("FINALIZED"))
        self.assertIsNone(result["execution_result"])
        self.assertIn("MINAH", [event["component"] for event in result["component_trace"]])
        self.assertNotIn("IVM", [event["component"] for event in result["component_trace"]])
        self.assertEqual(self.pipeline.cee.get("OP-001")["phase"], "FINALIZED")
        self.assertEqual(self.pipeline.cee.events("OP-001")[-1]["status"], "DEFERRED")
        with self.assertRaises(Exception):
            self.pipeline.ivm.process("PROC-001")

    def test_unavailable_authority_adapter_defers_before_cee_admission(self):
        pipeline = KinSysProtocolPipeline(authority_adapter=UnavailableAuthorityAdapter())
        try:
            result = pipeline.execute(request())
            self.assertEqual(result["final_decision"], "DEFERRED")
            self.assertEqual(result["reason"], "AUTHORITY_VERIFICATION_UNAVAILABLE")
            self.assertEqual(result["authority_gate"]["outcome"], "DEFER")
            self.assertNotIn("ADMITTED", [event["phase"] for event in result["cee_events"]])
            with self.assertRaises(Exception):
                pipeline.ivm.process("PROC-001")
        finally:
            pipeline.close()

    def test_authority_adapter_exception_defers_closed(self):
        class BrokenAdapter:
            def evaluate(self, request):
                raise TimeoutError("sensitive backend details must not leak")

        pipeline = KinSysProtocolPipeline(authority_adapter=BrokenAdapter())
        try:
            result = pipeline.execute(request())
            self.assertEqual(result["final_decision"], "DEFERRED")
            self.assertEqual(result["reason"], "AUTHORITY_GATE_UNAVAILABLE")
            self.assertEqual(result["authority_gate"]["adapter"], "ADAPTER_ERROR")
            self.assertEqual(result["authority_gate"]["details"], {"error_type": "TimeoutError"})
            self.assertNotIn("ADMITTED", [event["phase"] for event in result["cee_events"]])
        finally:
            pipeline.close()

    def test_invalid_authority_adapter_output_defers_closed(self):
        class InvalidAdapter:
            def evaluate(self, request):
                return {"outcome": "ALLOW"}

        pipeline = KinSysProtocolPipeline(authority_adapter=InvalidAdapter())
        try:
            result = pipeline.execute(request())
            self.assertEqual(result["final_decision"], "DEFERRED")
            self.assertEqual(result["reason"], "INVALID_AUTHORITY_GATE_RESPONSE")
            self.assertNotIn("ADMITTED", [event["phase"] for event in result["cee_events"]])
        finally:
            pipeline.close()

    def test_wrong_actor_is_rejected_before_cee_admission(self):
        result = self.pipeline.execute(request(actor_id="UNAUTHORISED-ACTOR"))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["final_decision"], "REJECTED")
        self.assertEqual(result["reason"], "AUTHORITY_MISMATCH")
        self.assertIsNone(result["execution_result"])
        phases = [event["phase"] for event in result["cee_events"]]
        self.assertNotIn("ADMITTED", phases)
        with self.assertRaises(Exception):
            self.pipeline.ivm.process("PROC-001")

    def test_revoked_authority_is_rejected_before_cee_admission(self):
        revoked = KinSysProtocolPipeline(authority_active=False)
        try:
            result = revoked.execute(request())
            self.assertEqual(result["status"], "REJECTED")
            self.assertEqual(result["final_decision"], "REJECTED")
            self.assertEqual(result["reason"], "AUTHORITY_REVOKED")
            self.assertIsNone(result["execution_result"])
            self.assertNotIn("ADMITTED", [event["phase"] for event in result["cee_events"]])
            with self.assertRaises(Exception):
                revoked.ivm.process("PROC-001")
        finally:
            revoked.close()

    def test_rule_ineligibility_is_rejected_before_cee_admission(self):
        evidence = request()["evidence"]
        evidence["commodity"] = "SYNTHETIC_PROHIBITED_COMMODITY"
        result = self.pipeline.execute(request(evidence=evidence))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["final_decision"], "REJECTED")
        self.assertEqual(result["reason"], "RULE_REJECTED")
        self.assertIsNone(result["execution_result"])
        self.assertNotIn("ADMITTED", [event["phase"] for event in result["cee_events"]])
        with self.assertRaises(Exception):
            self.pipeline.ivm.process("PROC-001")

    def test_operation_id_cannot_be_reused_for_different_payload(self):
        self.pipeline.execute(request())
        changed = request()
        changed["evidence"] = {**changed["evidence"], "origin": "DIFFERENT-ORIGIN"}
        with self.assertRaises(ExecutionConflict):
            self.pipeline.execute(changed)

    def test_event_id_conflict_is_rejected_by_ivm(self):
        self.pipeline.execute(request())
        with self.assertRaises(Exception):
            self.pipeline.execute(request(operation_id="OP-002", event_id="EV-001", process_id="PROC-002"))

    def test_retry_after_ivm_commit_before_cee_ack_recovers_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            cee_db = str(Path(tmp) / "cee.sqlite3")
            pipeline = KinSysProtocolPipeline(cee_database=cee_db)
            try:
                with self.assertRaisesRegex(RuntimeError, "INJECTED_FAILURE_AFTER_IVM_COMMIT"):
                    pipeline.execute(request(), fail_after_ivm_commit=True)
            finally:
                pipeline.close()
            resumed = KinSysProtocolPipeline(cee_database=cee_db)
            try:
                result = resumed.execute(request())
                self.assertEqual(result["status"], "PASSED")
                self.assertEqual(result["final_decision"], "PASSED")
                self.assertTrue(result["duplicate"])
                self.assertEqual(resumed.ivm.process("PROC-001")["state"], "ACTIVE")
                self.assertEqual(len(resumed.ivm.history("PROC-001")), 1)
                self.assertEqual([e["phase"] for e in resumed.cee.events("OP-001")][-2:], ["COMMITTED", "FINALIZED"])
            finally:
                resumed.close()

    def test_finalized_operation_is_idempotent(self):
        first = self.pipeline.execute(request())
        second = self.pipeline.execute(request())
        self.assertEqual(second["status"], "PASSED")
        self.assertEqual(second["final_decision"], "PASSED")
        self.assertTrue(second["duplicate"])
        self.assertEqual(len(self.pipeline.ivm.history("PROC-001")), 1)

    def test_illegal_lifecycle_transition_is_rejected(self):
        self.pipeline.cee.start("OP-X", request(operation_id="OP-X", event_id="EV-X", process_id="PROC-X"))
        with self.assertRaises(LifecycleViolation):
            self.pipeline.cee.advance("OP-X", "COMMITTED")


if __name__ == "__main__":
    unittest.main()
