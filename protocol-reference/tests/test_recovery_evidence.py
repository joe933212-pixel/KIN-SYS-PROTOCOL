# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Read-only recovery evidence bundle tests."""
import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol_pipeline import KinSysProtocolPipeline
from recovery_evidence import build_recovery_evidence_bundle


def sample_request(operation_id="EVIDENCE-001", event_id="EVIDENCE-EVENT-001", **overrides):
    value = {
        "operation_id": operation_id,
        "event_id": event_id,
        "process_id": "EVIDENCE-PROCESS-001",
        "actor_id": "AUTHORITY-001",
        "event_type": "ACTIVATE",
        "jurisdiction": "UG-DEMO",
        "rule_set": "KIN-SYS-DEMO-RULES",
        "rule_version": "1.0",
        "evidence": {
            "consignment_id": "EVIDENCE-PROCESS-001",
            "commodity": "SYNTHETIC_MAIZE",
            "origin": "UGANDA-DEMO",
            "destination": "BORDER-DEMO",
            "rule_refs": ["SYNTHETIC-RULE-1"],
            "private_document": "DO-NOT-EXPORT-RAW-IDENTITY-DOCUMENT",
        },
    }
    value.update(overrides)
    return value


class RecoveryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = KinSysProtocolPipeline()
        self.addCleanup(self.pipeline.close)

    def _counts(self):
        return {
            "cee_operations": self.pipeline.cee.db.execute("SELECT COUNT(*) FROM cee_operations").fetchone()[0],
            "cee_events": self.pipeline.cee.db.execute("SELECT COUNT(*) FROM cee_events").fetchone()[0],
            "cee_observations": self.pipeline.cee.db.execute("SELECT COUNT(*) FROM cee_observations").fetchone()[0],
            "ivm_events": self.pipeline.ivm.db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "ivm_history": self.pipeline.ivm.db.execute("SELECT COUNT(*) FROM transition_history").fetchone()[0],
        }

    def test_committed_bundle_is_complete_bounded_and_read_only(self):
        self.pipeline.execute(sample_request())
        before = self._counts()
        bundle = build_recovery_evidence_bundle(self.pipeline, "EVIDENCE-001")
        after = self._counts()
        self.assertEqual(bundle["capture_status"], "COMPLETE")
        self.assertEqual(bundle["consistency"]["status"], "CONSISTENT")
        self.assertEqual(bundle["operation"]["phase"], "FINALIZED")
        self.assertEqual(bundle["ivm"]["event"]["outcome"], "ACCEPTED")
        self.assertEqual(len(bundle["ivm"]["transition_history"]), 1)
        self.assertEqual(before, after)
        encoded = json.dumps(bundle, sort_keys=True)
        self.assertNotIn("DO-NOT-EXPORT-RAW-IDENTITY-DOCUMENT", encoded)
        self.assertNotIn('"evidence":', encoded)
        self.assertEqual(len(bundle["bundle_digest"]), 64)

    def test_rejected_ivm_event_is_reported_without_fabricated_history(self):
        self.pipeline.execute(sample_request(
            operation_id="EVIDENCE-REJECTED",
            event_id="EVIDENCE-EVENT-REJECTED",
            evidence={
                "consignment_id": "EVIDENCE-PROCESS-001",
                "commodity": "SYNTHETIC_PROHIBITED_COMMODITY",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
                "rule_refs": ["SYNTHETIC-RULE-1"],
            },
        ))
        bundle = build_recovery_evidence_bundle(self.pipeline, "EVIDENCE-REJECTED")
        self.assertEqual(bundle["capture_status"], "COMPLETE")
        self.assertEqual(bundle["ivm"]["event"]["outcome"], "REJECTED")
        self.assertEqual(bundle["ivm"]["transition_history"], [])
        self.assertEqual(bundle["consistency"]["status"], "CONSISTENT")

    def test_conflicting_cee_claim_is_preserved_and_reported_not_repaired(self):
        self.pipeline.execute(sample_request())
        self.pipeline.cee.db.execute(
            "UPDATE cee_operations SET outcome='REJECTED' WHERE operation_id=?",
            ("EVIDENCE-001",),
        )
        self.pipeline.cee.db.commit()
        before = self._counts()
        bundle = build_recovery_evidence_bundle(self.pipeline, "EVIDENCE-001")
        self.assertEqual(bundle["consistency"]["status"], "CONFLICT")
        self.assertEqual(bundle["ivm"]["event"]["outcome"], "ACCEPTED")
        self.assertEqual(bundle["operation"]["outcome"], "REJECTED")
        self.assertEqual(before, self._counts())

    def test_missing_operation_returns_incomplete_bundle(self):
        bundle = build_recovery_evidence_bundle(self.pipeline, "DOES-NOT-EXIST")
        self.assertEqual(bundle["capture_status"], "INCOMPLETE")
        self.assertEqual(bundle["consistency"]["status"], "OPERATION_NOT_FOUND")
        self.assertEqual(len(bundle["bundle_digest"]), 64)


if __name__ == "__main__":
    unittest.main()
