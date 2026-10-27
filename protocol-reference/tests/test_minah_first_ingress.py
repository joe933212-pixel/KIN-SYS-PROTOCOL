# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from minah_first_ingress import IngressConflict, MinahFirstIngress, _fingerprint
from governed_contracts import compute_payload_digest


class FakeMinah:
    def __init__(self, log, status="PASS"):
        self.log, self.status, self.calls = log, status, 0
    def review_instruction(self, instruction):
        self.log.append("MINAH")
        self.calls += 1
        return {"status": self.status, "disposition_ref": "minah:case-1"}


class FakeIdentity:
    def __init__(self, log, verified=True):
        self.log, self.verified = log, verified
    def verify_principal(self, instruction):
        self.log.append("IDENTITY")
        return {"verified": self.verified}


class FakeAuthority:
    def __init__(self, log, verified=True):
        self.log, self.verified = log, verified
    def verify_authority(self, instruction):
        self.log.append("AUTHORITY")
        return {"verified": self.verified}


class FakeCEE:
    def __init__(self, log):
        self.log, self.calls = log, 0
    def execute_governed_instruction(self, instruction, **context):
        self.log.append("CEE")
        self.calls += 1
        return {
            "protocol_decision": "PASSED", "reason_code": "CHECKS_PASSED",
            "user_message": "Required checks passed; the authorised next step may proceed.",
            "cee_execution_ref": "cee:exec-1", "external_effect_status": "NOT_ATTEMPTED",
            "evidence_refs": [],
        }


class MinahFirstIngressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = str(Path(self.tmp.name) / "ingress.sqlite")
        self.log = []
        self.minah = FakeMinah(self.log)
        self.identity = FakeIdentity(self.log)
        self.authority = FakeAuthority(self.log)
        self.cee = FakeCEE(self.log)
        self.ingress = MinahFirstIngress(
            self.db_path, minah=self.minah, identity=self.identity,
            authority=self.authority, cee=self.cee,
        )
        self.addCleanup(self.ingress.close)

    def instruction(self, **changes):
        value = {
            "request_id": "req-1", "operation_id": "op-1",
            "idempotency_key": "idem-0001", "principal_ref": "principal:1",
            "authority_ref": "authority:1", "action": "submit_transfer",
            "target_ref": "account:masked", "purpose": "supplier payment",
            "jurisdiction": "UG", "rule_pack_version": "ug-rules-1",
        }
        value.update(changes)
        if value.get("contract_version") == "kin-sys-governed-contract-0.1":
            value.setdefault("submitted_at", "2026-10-27T10:00:00+03:00")
            value.setdefault("payload_digest", "sha256:" + ("0" * 64))
            value.setdefault("evidence_refs", [])
            value.setdefault("constraints", {})
            value["payload_digest"] = compute_payload_digest(value)
        return value

    def test_order_is_minah_then_identity_then_authority_then_cee(self):
        result = self.ingress.execute(self.instruction())
        self.assertEqual(self.log, ["MINAH", "IDENTITY", "AUTHORITY", "CEE"])
        self.assertEqual(result["protocol_decision"], "PASSED")

    def test_minah_rejection_prevents_all_downstream_calls(self):
        self.minah.status = "REJECTED"
        result = self.ingress.execute(self.instruction())
        self.assertEqual(result["protocol_decision"], "REJECTED")
        self.assertEqual(self.log, ["MINAH"])
        self.assertEqual(self.cee.calls, 0)

    def test_minah_deferral_prevents_downstream_calls(self):
        self.minah.status = "DEFERRED"
        result = self.ingress.execute(self.instruction())
        self.assertEqual(result["protocol_decision"], "DEFERRED")
        self.assertEqual(self.log, ["MINAH"])

    def test_identity_failure_prevents_authority_and_cee(self):
        self.identity.verified = False
        result = self.ingress.execute(self.instruction())
        self.assertEqual(result["reason_code"], "IDENTITY_NOT_VERIFIED")
        self.assertEqual(self.log, ["MINAH", "IDENTITY"])

    def test_authority_failure_prevents_cee(self):
        self.authority.verified = False
        result = self.ingress.execute(self.instruction())
        self.assertEqual(result["reason_code"], "AUTHORITY_NOT_VERIFIED")
        self.assertEqual(self.log, ["MINAH", "IDENTITY", "AUTHORITY"])

    def test_identical_replay_returns_saved_result_without_repeating_adapters(self):
        first = self.ingress.execute(self.instruction())
        replay = self.ingress.execute(self.instruction())
        self.assertEqual(first["protocol_decision"], replay["protocol_decision"])
        self.assertTrue(replay["duplicate"])
        self.assertEqual(self.cee.calls, 1)
        self.assertEqual(self.minah.calls, 1)

    def test_operation_id_conflict_is_rejected(self):
        self.ingress.execute(self.instruction())
        with self.assertRaises(IngressConflict):
            self.ingress.execute(self.instruction(action="different_action"))




    def test_unresolved_recovery_does_not_finalize_unknown_ingress_result(self):
        instruction = self.instruction(
            contract_version="kin-sys-governed-contract-0.1",
            submitted_at="2026-10-27T10:00:00+03:00",
            payload_digest="sha256:" + ("b" * 64),
            evidence_refs=[],
        )
        instruction["payload_digest"] = compute_payload_digest(instruction)

        def pipeline_crash(request):
            raise RuntimeError("simulated interrupted pipeline")

        self.ingress.close()
        ingress = MinahFirstIngress(
            self.db_path, pipeline_executor=pipeline_crash
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "simulated interrupted"):
                ingress.execute(
                    instruction,
                    execution_request={"operation_id": "op-1", "event_id": "event-1", "_contract_envelope": instruction},
                )
            unresolved = ingress.recover(
                "op-1",
                pipeline_recovery=lambda operation_id, request: {
                    "operation_id": operation_id,
                    "status": "UNKNOWN",
                    "reason": "RECOVERY_REQUIRES_MANUAL_RECONCILIATION",
                    "recovery_required": True,
                },
                pipeline_inspector=lambda operation_id: {"phase": "REQUESTED"},
            )
            self.assertEqual(unresolved["protocol_decision"], "UNKNOWN")
            self.assertTrue(unresolved["recovery_required"])
            self.assertIsNone(ingress.inspect("op-1")["result"])
            self.assertEqual(
                ingress.inspect("op-1")["phase"], "UNKNOWN_REQUIRES_RECONCILIATION"
            )

            def exception_with_sensitive_detail(operation_id, request):
                raise RuntimeError("database path /secret/provider-token")

            ingress.recover(
                "op-1",
                pipeline_recovery=exception_with_sensitive_detail,
                pipeline_inspector=lambda operation_id: {"phase": "COMMITTING"},
            )
            blocked_event = ingress.recovery_events("op-1")[-1]
            self.assertEqual(blocked_event["action"], "RECOVERY_BLOCKED")
            self.assertEqual(
                blocked_event["evidence"]["reason_code"], "RECOVERY_CALLBACK_ERROR"
            )
            self.assertNotIn("secret", str(blocked_event["evidence"]))
            self.assertIsNone(ingress.inspect("op-1")["result"])
        finally:
            ingress.close()


    def test_pipeline_ingress_rejects_operation_id_mismatch_before_journaling(self):
        self.ingress.close()
        ingress = MinahFirstIngress(
            self.db_path,
            pipeline_executor=lambda request: {
                "operation_id": request["operation_id"], "status": "COMMITTED"
            },
        )
        try:
            with self.assertRaisesRegex(
                IngressConflict, "EXECUTION_REQUEST_OPERATION_ID_MISMATCH"
            ):
                ingress.execute(
                    self.instruction(),
                    execution_request={"operation_id": "different-operation"},
                )
            self.assertIsNone(ingress.inspect("op-1"))
        finally:
            ingress.close()

    def test_v01_pipeline_ingress_requires_matching_validated_contract_envelope(self):
        self.ingress.close()
        ingress = MinahFirstIngress(
            self.db_path,
            pipeline_executor=lambda request: {
                "operation_id": request["operation_id"], "status": "COMMITTED"
            },
        )
        instruction = self.instruction(
            contract_version="kin-sys-governed-contract-0.1",
            rule_pack_version="KIN-SYS-DEMO-RULES:1.0",
        )
        try:
            with self.assertRaisesRegex(
                IngressConflict, "EXECUTION_REQUEST_NOT_BOUND_TO_INGRESS_INSTRUCTION"
            ):
                ingress.execute(
                    instruction,
                    execution_request={"operation_id": "op-1", "event_id": "event-1"},
                )
            self.assertIsNone(ingress.inspect("op-1"))
        finally:
            ingress.close()

    def test_operation_id_cannot_be_rebound_to_different_execution_request(self):
        instruction = self.instruction()
        self.ingress.execute(
            instruction,
            execution_request={"operation_id": "op-1", "event_id": "stable-event-1"},
        )
        with self.assertRaisesRegex(
            IngressConflict, "OPERATION_ID_REUSED_WITH_DIFFERENT_EXECUTION_REQUEST"
        ):
            self.ingress.execute(
                instruction,
                execution_request={"operation_id": "op-1", "event_id": "different-event"},
            )

    def test_pipeline_journal_replay_survives_ingress_object_restart(self):
        instruction = self.instruction(
            contract_version="kin-sys-governed-contract-0.1",
            rule_pack_version="KIN-SYS-DEMO-RULES:1.0",
        )
        execution_calls = []

        def execute_once(request):
            execution_calls.append(request["operation_id"])
            return {"operation_id": request["operation_id"], "status": "COMMITTED", "reason": None}

        self.ingress.close()
        first = MinahFirstIngress(self.db_path, pipeline_executor=execute_once)
        result = first.execute(instruction, execution_request={"operation_id": "op-1", "_contract_envelope": instruction})
        first.close()
        self.assertEqual(result["protocol_decision"], "PASSED")
        self.assertEqual(execution_calls, ["op-1"])

        def must_not_execute(request):
            raise AssertionError("a completed ingress replay must not call the pipeline")

        reopened = MinahFirstIngress(self.db_path, pipeline_executor=must_not_execute)
        try:
            replay = reopened.execute(instruction, execution_request={"operation_id": "op-1", "_contract_envelope": instruction})
            self.assertTrue(replay["duplicate"])
            self.assertEqual(replay["protocol_decision"], "PASSED")
            self.assertEqual(replay["pipeline_result"]["status"], "COMMITTED")
            self.assertEqual(reopened.inspect("op-1")["phase"], "PASSED")
            self.assertEqual(execution_calls, ["op-1"])
        finally:
            reopened.close()

    def test_interrupted_inflight_operation_is_unknown_not_resubmitted(self):
        class CrashingCEE(FakeCEE):
            def execute_governed_instruction(self, instruction, **context):
                self.log.append("CEE")
                self.calls += 1
                raise RuntimeError("simulated interruption after handoff")
        self.ingress.cee = CrashingCEE(self.log)
        with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
            self.ingress.execute(self.instruction())
        calls_before_retry = list(self.log)
        result = self.ingress.execute(self.instruction())
        self.assertEqual(result["protocol_decision"], "UNKNOWN")
        self.assertEqual(self.log, calls_before_retry)
        self.assertEqual(self.ingress.cee.calls, 1)


    def test_pipeline_inflight_replay_preserves_contract_binding_and_stays_unknown(self):
        instruction = self.instruction(
            contract_version="kin-sys-governed-contract-0.1",
            rule_pack_version="KIN-SYS-DEMO-RULES:1.0",
        )

        def interrupted(request):
            raise RuntimeError("simulated crash after ingress handoff")

        self.ingress.close()
        first = MinahFirstIngress(self.db_path, pipeline_executor=interrupted)
        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            first.execute(instruction, execution_request={"operation_id": "op-1", "_contract_envelope": instruction})
        first.close()

        def must_not_replay(request):
            raise AssertionError("uncertain operation must not be resubmitted")

        reopened = MinahFirstIngress(self.db_path, pipeline_executor=must_not_replay)
        try:
            result = reopened.execute(instruction, execution_request={"operation_id": "op-1", "_contract_envelope": instruction})
            self.assertEqual(result["protocol_decision"], "UNKNOWN")
            self.assertEqual(result["reason_code"], "INGRESS_IN_FLIGHT_REQUIRES_RECONCILIATION")
            self.assertEqual(result["contract_version"], "kin-sys-governed-contract-0.1")
            self.assertEqual(result["request_id"], instruction["request_id"])
            self.assertEqual(result["rule_pack_version"], "KIN-SYS-DEMO-RULES:1.0")
            self.assertEqual(reopened.inspect("op-1")["phase"], "UNKNOWN_REQUIRES_RECONCILIATION")
        finally:
            reopened.close()


    def test_uncertain_ingress_reconciles_only_from_finalized_cee_result(self):
        instruction = self.instruction(
            contract_version="kin-sys-governed-contract-0.1",
            rule_pack_version="KIN-SYS-DEMO-RULES:1.0",
            submitted_at="2026-10-27T10:00:00+03:00",
            payload_digest="sha256:" + ("e" * 64),
            evidence={"commodity": "SYNTHETIC_MAIZE"},
        )
        instruction["payload_digest"] = compute_payload_digest(instruction)

        def interrupted(request):
            raise RuntimeError("simulated lost acknowledgement")

        self.ingress.close()
        ingress = MinahFirstIngress(self.db_path, pipeline_executor=interrupted)
        with self.assertRaisesRegex(RuntimeError, "lost acknowledgement"):
            ingress.execute(instruction, execution_request={"operation_id": "op-1", "_contract_envelope": instruction})

        unresolved = ingress.reconcile("op-1", {
            "phase": "EXECUTING",
            "result": None,
        })
        self.assertIsNone(unresolved["result"])
        self.assertEqual(unresolved["phase"], "UNKNOWN_REQUIRES_RECONCILIATION")

        finalized_snapshot = {
            "phase": "FINALIZED",
            "result": {"operation_id": "op-1", "status": "COMMITTED", "reason": None},
            "cee_events": [{"phase": "FINALIZED", "status": "COMMITTED"}],
            "component_trace": [{"component": "CEE", "detail": {"phase": "FINALIZED"}}],
        }
        mismatch = ingress.reconcile(
            "op-1", {**finalized_snapshot, "request_fingerprint": "wrong-fingerprint"}
        )
        self.assertIsNone(mismatch["result"])
        self.assertEqual(mismatch["phase"], "UNKNOWN_REQUIRES_RECONCILIATION")

        recovered = ingress.reconcile(
            "op-1",
            {
                **finalized_snapshot,
                "request_fingerprint": _fingerprint({"operation_id": "op-1", "_contract_envelope": instruction}),
            },
        )
        self.assertEqual(recovered["protocol_decision"], "PASSED")
        self.assertTrue(recovered["reconciled"])
        self.assertEqual(recovered["pipeline_result"]["status"], "COMMITTED")
        self.assertEqual(recovered["pipeline_result"]["cee_events"][0]["phase"], "FINALIZED")
        self.assertEqual(ingress.inspect("op-1")["phase"], "PASSED")
        ingress.close()

    def test_unknown_ceeresponse_is_not_reported_as_passed(self):
        class InvalidCEE(FakeCEE):
            def execute_governed_instruction(self, instruction, **context):
                self.log.append("CEE")
                return {"protocol_decision": "MADE_UP"}
        self.ingress.cee = InvalidCEE(self.log)
        result = self.ingress.execute(self.instruction())
        self.assertEqual(result["protocol_decision"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
