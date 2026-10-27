# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from minah_first_ingress import IngressConflict, MinahFirstIngress


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
