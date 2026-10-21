# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Paired contract tests for separate institutional decisions."""
import os
import subprocess
import sys
import tempfile
import time
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

    def test_paired_result_vector_matches_for_independent_institution_decisions(self):
        vectors = []
        for implementation in IMPLEMENTATIONS:
            store = self.make_store(implementation)
            origin_result = self.decide(store, event_id="O-1", stage="ORIGIN",
                                        actor_id="ORIGIN-A", decision="APPROVE")
            destination_result = self.decide(store, event_id="D-1", stage="DESTINATION",
                                             actor_id="DESTINATION-B", decision="REJECT")
            origin = store.stage("C-001", "ORIGIN")
            destination = store.stage("C-001", "DESTINATION")
            history = store.history("C-001")
            vectors.append({
                "statuses": [origin_result["status"], destination_result["status"]],
                "stage_outcomes": [
                    origin.get("outcome", origin.get("result")),
                    destination.get("outcome", destination.get("result")),
                ],
                "stage_versions": [
                    origin.get("version", origin.get("revision")),
                    destination.get("version", destination.get("revision")),
                ],
                "history": [
                    (item.get("stage", item.get("step_key")),
                     item.get("actor_id", item.get("decided_by")),
                     item.get("rule_version", item.get("rule_id")),
                     item.get("prior_outcome", item.get("result_before")),
                     item.get("new_outcome", item.get("result_after")))
                    for item in history
                ],
            })
        self.assertEqual(vectors[0], vectors[1])

    def test_process_death_between_institutions_preserves_committed_origin_only(self):
        for implementation in IMPLEMENTATIONS:
            with self.subTest(implementation=implementation.__name__):
                with tempfile.TemporaryDirectory() as directory:
                    database = str(Path(directory) / "workflow.sqlite")
                    marker = str(Path(directory) / "origin-committed.marker")
                    module = ("workflows.institutional_workflow", "InstitutionalWorkflow") if implementation is InstitutionalWorkflow else ("comparators.conventional_workflow", "ConventionalWorkflow")
                    child_script = r"""
import importlib, sys, time
sys.path.insert(0, sys.argv[1])
module = importlib.import_module(sys.argv[4])
store = getattr(module, sys.argv[5])(sys.argv[2])
store.create_case("C-001", origin_authority="ORIGIN-A", destination_authority="DESTINATION-B")
store.decide(event_id="O-1", consignment_id="C-001", stage="ORIGIN",
             actor_id="ORIGIN-A", decision="APPROVE", rule_version="1.0")
with open(sys.argv[3], "w", encoding="utf-8") as handle:
    handle.write("origin committed; destination not yet attempted")
while True:
    time.sleep(0.05)
"""
                    child = subprocess.Popen(
                        [sys.executable, "-c", child_script, str(ROOT), database, marker, module[0], module[1]],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    deadline = time.monotonic() + 8
                    try:
                        while time.monotonic() < deadline and not os.path.exists(marker):
                            if child.poll() is not None:
                                self.fail(f"Workflow child exited before marker: {child.returncode}")
                            time.sleep(0.01)
                        self.assertTrue(os.path.exists(marker), "Child did not reach post-origin commit point")
                        child.terminate()
                        child.wait(timeout=5)
                    finally:
                        if child.poll() is None:
                            child.kill()
                            child.wait(timeout=5)
                    reopened = implementation(database)
                    try:
                        origin = reopened.stage("C-001", "ORIGIN")
                        destination = reopened.stage("C-001", "DESTINATION")
                        self.assertEqual(origin.get("outcome", origin.get("result")), "APPROVE")
                        self.assertEqual(destination.get("outcome", destination.get("result")), "PENDING")
                        self.assertEqual(len(reopened.history("C-001")), 1)
                    finally:
                        reopened.close()

    def _propose(self, store, event_id="P-1", actor="REVIEWER"):
        return store.correct_decision(event_id=event_id, consignment_id="C-001", stage="ORIGIN",
            actor_id=actor, replacement_decision="APPROVE", rule_version="1.0",
            supersedes_event_id="O-1", correction_basis="Verified missing certificate")

    def _authorize(self, store, event_id="A-1", actor="ORIGIN-A", proposal="P-1"):
        return store.authorize_correction(event_id=event_id, consignment_id="C-001",
            stage="ORIGIN", actor_id=actor, rule_version="1.0", proposal_event_id=proposal)

    def test_reviewer_proposal_does_not_change_institutional_decision(self):
        for implementation in IMPLEMENTATIONS:
            with self.subTest(implementation=implementation.__name__):
                store = self.make_store(implementation)
                self.decide(store, event_id="O-1", stage="ORIGIN", actor_id="ORIGIN-A", decision="REJECT")
                self.assertEqual(self._propose(store)["status"], "ACCEPTED")
                self.assertEqual(store.stage("C-001", "ORIGIN").get("outcome", store.stage("C-001", "ORIGIN").get("result")), "REJECT")
                self.assertEqual(len(store.history("C-001")), 1)

    def test_only_institution_can_apply_linked_correction(self):
        for implementation in IMPLEMENTATIONS:
            with self.subTest(implementation=implementation.__name__):
                store = self.make_store(implementation)
                self.decide(store, event_id="O-1", stage="ORIGIN", actor_id="ORIGIN-A", decision="REJECT")
                self.assertEqual(self._propose(store)["status"], "ACCEPTED")
                denied = self._authorize(store, actor="COORDINATOR")
                self.assertEqual(denied["reason"], "AUTHORITY_MISMATCH")
                self.assertEqual(store.stage("C-001", "ORIGIN").get("outcome", store.stage("C-001", "ORIGIN").get("result")), "REJECT")
                applied = self._authorize(store)
                self.assertEqual(applied["status"], "ACCEPTED")
                history = store.history("C-001")
                self.assertEqual(len(history), 2)
                self.assertEqual(history[0].get("new_outcome", history[0].get("result_after")), "REJECT")
                self.assertEqual(history[1].get("new_outcome", history[1].get("result_after")), "APPROVE")
                if implementation is InstitutionalWorkflow:
                    link = store.db.execute("SELECT superseded_event_id, correction_authority_id, correction_basis, proposal_event_id FROM stage_corrections WHERE correction_event_id='A-1'").fetchone()
                else:
                    link = store.db.execute("SELECT replaces_operation, reviewer, rationale, proposal_operation FROM correction_log WHERE correction_key='A-1'").fetchone()
                self.assertEqual(tuple(link), ("O-1", "ORIGIN-A", "Verified missing certificate", "P-1"))

    def test_correction_proposal_and_authorisation_replays_are_idempotent(self):
        for implementation in IMPLEMENTATIONS:
            with self.subTest(implementation=implementation.__name__):
                store = self.make_store(implementation)
                self.decide(store, event_id="O-1", stage="ORIGIN", actor_id="ORIGIN-A", decision="REJECT")
                request = dict(event_id="P-1", consignment_id="C-001", stage="ORIGIN", actor_id="REVIEWER",
                    replacement_decision="APPROVE", rule_version="1.0", supersedes_event_id="O-1",
                    correction_basis="Verified missing certificate")
                self.assertFalse(store.correct_decision(**request)["duplicate"])
                self.assertTrue(store.correct_decision(**request)["duplicate"])
                error = WorkflowConflict if implementation is InstitutionalWorkflow else ValueError
                with self.assertRaises(error):
                    store.correct_decision(**dict(request, correction_basis="Different basis"))
                auth = dict(event_id="A-1", consignment_id="C-001", stage="ORIGIN",
                    actor_id="ORIGIN-A", rule_version="1.0", proposal_event_id="P-1")
                self.assertFalse(store.authorize_correction(**auth)["duplicate"])
                self.assertTrue(store.authorize_correction(**auth)["duplicate"])
                with self.assertRaises(error):
                    store.authorize_correction(**dict(auth, proposal_event_id="OTHER"))
                self.assertEqual(len(store.history("C-001")), 2)

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
