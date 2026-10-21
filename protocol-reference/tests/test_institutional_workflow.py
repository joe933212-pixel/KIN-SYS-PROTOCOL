# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Paired contract tests for separate institutional decisions."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflows.institutional_workflow import InstitutionalWorkflow, WorkflowConflict
from comparators.conventional_workflow import ConventionalWorkflow


IMPLEMENTATIONS = (InstitutionalWorkflow, ConventionalWorkflow)


class InstitutionalWorkflowContractTests(unittest.TestCase):
    def make_store(self, implementation):
        store = implementation()
        store.create_case("C-001", origin_authority="ORIGIN-A",
                          destination_authority="DESTINATION-B")
        self.addCleanup(store.close)
        return store

    def decide(self, store, *, event_id, stage, actor_id, decision):
        return store.decide(event_id=event_id, consignment_id="C-001", stage=stage,
                            actor_id=actor_id, decision=decision, rule_version="1.0")

    def test_origin_approval_does_not_override_destination_rejection(self):
        for implementation in IMPLEMENTATIONS:
            with self.subTest(implementation=implementation.__name__):
                store = self.make_store(implementation)
                self.assertEqual(self.decide(store, event_id="O-1", stage="ORIGIN",
                                             actor_id="ORIGIN-A", decision="APPROVE")["status"], "ACCEPTED")
                self.assertEqual(self.decide(store, event_id="D-1", stage="DESTINATION",
                                             actor_id="DESTINATION-B", decision="REJECT")["status"], "ACCEPTED")
                origin = store.stage("C-001", "ORIGIN")
                destination = store.stage("C-001", "DESTINATION")
                self.assertEqual(origin.get("outcome", origin.get("result")), "APPROVE")
                self.assertEqual(destination.get("outcome", destination.get("result")), "REJECT")
                history = store.history("C-001")
                self.assertEqual([(h.get("stage", h.get("step_key")), h.get("actor_id", h.get("decided_by")))
                                  for h in history],
                                 [("ORIGIN", "ORIGIN-A"), ("DESTINATION", "DESTINATION-B")])

    def test_coordination_actor_cannot_make_institutional_decision(self):
        for implementation in IMPLEMENTATIONS:
            with self.subTest(implementation=implementation.__name__):
                store = self.make_store(implementation)
                result = self.decide(store, event_id="O-1", stage="ORIGIN",
                                     actor_id="COORDINATOR", decision="APPROVE")
                self.assertEqual(result["status"], "REJECTED")
                self.assertEqual(result["reason"], "AUTHORITY_MISMATCH")
                self.assertEqual(store.stage("C-001", "ORIGIN").get("outcome",
                                 store.stage("C-001", "ORIGIN").get("result")), "PENDING")
                self.assertEqual(store.history("C-001"), [])

    def test_duplicate_replay_and_conflicting_identifier(self):
        for implementation in IMPLEMENTATIONS:
            with self.subTest(implementation=implementation.__name__):
                store = self.make_store(implementation)
                request = dict(event_id="O-1", consignment_id="C-001", stage="ORIGIN",
                               actor_id="ORIGIN-A", decision="APPROVE", rule_version="1.0")
                self.assertFalse(store.decide(**request)["duplicate"])
                self.assertTrue(store.decide(**request)["duplicate"])
                changed = dict(request, decision="REJECT")
                conflict = WorkflowConflict if implementation is InstitutionalWorkflow else ValueError
                with self.assertRaises(conflict):
                    store.decide(**changed)
                self.assertEqual(len(store.history("C-001")), 1)

    def test_authority_revocation_and_rule_version_are_stage_specific(self):
        for implementation in IMPLEMENTATIONS:
            with self.subTest(implementation=implementation.__name__):
                store = self.make_store(implementation)
                store.set_authority_active("C-001", "DESTINATION", False)
                result = self.decide(store, event_id="D-1", stage="DESTINATION",
                                     actor_id="DESTINATION-B", decision="APPROVE")
                self.assertEqual(result["reason"], "AUTHORITY_REVOKED")
                store.set_authority_active("C-001", "DESTINATION", True)
                store.set_rule_version("C-001", "DESTINATION", "2.0")
                result = self.decide(store, event_id="D-2", stage="DESTINATION",
                                     actor_id="DESTINATION-B", decision="APPROVE")
                self.assertEqual(result["reason"], "RULE_VERSION_MISMATCH")
                self.assertEqual(store.stage("C-001", "DESTINATION").get("outcome",
                                 store.stage("C-001", "DESTINATION").get("result")), "PENDING")

    def test_file_reopen_preserves_each_institution_decision(self):
        for implementation in IMPLEMENTATIONS:
            with self.subTest(implementation=implementation.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    path = str(Path(directory) / "workflow.sqlite")
                    store = implementation(path)
                    store.create_case("C-001", origin_authority="ORIGIN-A",
                                      destination_authority="DESTINATION-B")
                    store.decide(event_id="O-1", consignment_id="C-001", stage="ORIGIN",
                                 actor_id="ORIGIN-A", decision="APPROVE", rule_version="1.0")
                    store.close()
                    reopened = implementation(path)
                    try:
                        self.assertEqual(reopened.stage("C-001", "ORIGIN").get("outcome",
                                         reopened.stage("C-001", "ORIGIN").get("result")), "APPROVE")
                        self.assertEqual(reopened.stage("C-001", "DESTINATION").get("outcome",
                                         reopened.stage("C-001", "DESTINATION").get("result")), "PENDING")
                        self.assertEqual(len(reopened.history("C-001")), 1)
                    finally:
                        reopened.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
