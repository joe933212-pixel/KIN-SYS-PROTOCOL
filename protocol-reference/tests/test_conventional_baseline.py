# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import concurrent.futures
import os
import subprocess
import time
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comparators.conventional_sqlite import ConventionalSQLiteBaseline


class ConventionalSQLiteBaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.temp.name, "baseline.sqlite")
        self.store = ConventionalSQLiteBaseline(self.database)
        self.store.create_object("P-001")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def request(self, **overrides):
        value = {
            "event_id": "E-001",
            "process_id": "P-001",
            "actor_id": "AUTHORITY-001",
            "event_type": "ACTIVATE",
            "rule_set": "LICENCE-RULES",
            "rule_version": "1.0",
        }
        value.update(overrides)
        return value

    def test_authorised_transition_persists_state_and_audit(self):
        result = self.store.execute(self.request())
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(self.store.object("P-001")["state"], "ACTIVE")
        self.assertEqual(self.store.object("P-001")["version"], 1)
        self.assertEqual(len(self.store.history("P-001")), 1)

    def test_duplicate_replay_and_conflicting_identifier(self):
        self.store.execute(self.request())
        duplicate = self.store.execute(self.request())
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(self.store.object("P-001")["version"], 1)
        with self.assertRaisesRegex(ValueError, "OPERATION_ID_CONFLICT"):
            self.store.execute(self.request(actor_id="OTHER-ACTOR"))
        self.assertEqual(len(self.store.history("P-001")), 1)

    def test_wrong_authority_and_rule_mismatch_do_not_transition(self):
        result = self.store.execute(self.request(actor_id="OTHER-ACTOR"))
        self.assertEqual(result["reason"], "AUTHORITY_MISMATCH")
        result = self.store.execute(self.request(event_id="E-002", rule_version="0.9"))
        self.assertEqual(result["reason"], "RULE_VERSION_MISMATCH")
        self.assertEqual(self.store.object("P-001")["state"], "PENDING")
        self.assertEqual(self.store.history("P-001"), [])

    def test_failure_before_commit_rolls_back_all_success_effects(self):
        with self.assertRaisesRegex(RuntimeError, "INJECTED_FAILURE_BEFORE_COMMIT"):
            self.store.execute(self.request(), fail_before_commit=True)
        self.assertEqual(self.store.object("P-001")["state"], "PENDING")
        self.assertEqual(self.store.object("P-001")["version"], 0)
        self.assertIsNone(self.store.operation("E-001"))
        self.assertEqual(self.store.history("P-001"), [])

    def test_committed_outcome_and_duplicate_survive_reopen(self):
        self.store.execute(self.request())
        self.store.close()
        self.store = ConventionalSQLiteBaseline(self.database)
        self.assertEqual(self.store.object("P-001")["state"], "ACTIVE")
        duplicate = self.store.execute(self.request())
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(self.store.object("P-001")["version"], 1)
        self.assertEqual(len(self.store.history("P-001")), 1)

    def test_concurrent_identical_requests_commit_once(self):
        self.store.close()
        barrier = threading.Barrier(2)

        def submit():
            store = ConventionalSQLiteBaseline(self.database)
            try:
                barrier.wait(timeout=5)
                return store.execute(self.request())
            finally:
                store.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))

        self.assertEqual(sorted(result["status"] for result in results), ["ACCEPTED", "ACCEPTED"])
        self.assertEqual(sum(result["duplicate"] for result in results), 1)
        self.store = ConventionalSQLiteBaseline(self.database)
        self.assertEqual(self.store.object("P-001")["version"], 1)
        self.assertEqual(len(self.store.history("P-001")), 1)



    def _terminate_child_at_marker(self, script, marker):
        child = subprocess.Popen(
            [sys.executable, "-c", script, str(Path(__file__).resolve().parents[1]), self.database, marker],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 8
        try:
            while time.monotonic() < deadline and not os.path.exists(marker):
                if child.poll() is not None:
                    stdout, stderr = child.communicate(timeout=1)
                    self.fail(f"Child exited before crash marker: {child.returncode}; stdout={stdout}; stderr={stderr}")
                time.sleep(0.01)
            self.assertTrue(os.path.exists(marker), "Child did not reach the intended crash point")
            child.terminate()
            child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)

    def test_process_termination_before_commit_leaves_no_partial_effect(self):
        marker = os.path.join(self.temp.name, "baseline-before-commit.marker")
        self.store.close()
        script = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
from comparators.conventional_sqlite import ConventionalSQLiteBaseline
store = ConventionalSQLiteBaseline(sys.argv[2])
def pause_at_crash_point():
    with open(sys.argv[3], "w", encoding="utf-8") as marker:
        marker.write("inside transaction before audit insert")
    while True:
        time.sleep(0.05)
store.db.create_function("pause_at_crash_point", 0, pause_at_crash_point)
store.db.execute(
    "CREATE TRIGGER pause_before_audit BEFORE INSERT ON baseline_audit "
    "BEGIN SELECT pause_at_crash_point(); END"
)
store.db.commit()
store.execute({
    "event_id": "E-001", "process_id": "P-001",
    "actor_id": "AUTHORITY-001", "event_type": "ACTIVATE",
    "rule_set": "LICENCE-RULES", "rule_version": "1.0"
})
"""
        self._terminate_child_at_marker(script, marker)
        self.store = ConventionalSQLiteBaseline(self.database)
        self.assertEqual(self.store.object("P-001")["state"], "PENDING")
        self.assertEqual(self.store.object("P-001")["version"], 0)
        self.assertIsNone(self.store.operation("E-001"))
        self.assertEqual(self.store.history("P-001"), [])

    def test_process_termination_after_commit_before_ack_is_idempotent(self):
        marker = os.path.join(self.temp.name, "baseline-after-commit.marker")
        self.store.close()
        script = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from comparators.conventional_sqlite import ConventionalSQLiteBaseline
store = ConventionalSQLiteBaseline(sys.argv[2])
store.execute({
    "event_id": "E-001", "process_id": "P-001",
    "actor_id": "AUTHORITY-001", "event_type": "ACTIVATE",
    "rule_set": "LICENCE-RULES", "rule_version": "1.0"
})
with open(sys.argv[3], "w", encoding="utf-8") as marker:
    marker.write("committed but no acknowledgement delivered")
while True:
    time.sleep(0.05)
"""
        self._terminate_child_at_marker(script, marker)
        self.store = ConventionalSQLiteBaseline(self.database)
        self.assertEqual(self.store.object("P-001")["state"], "ACTIVE")
        self.assertEqual(self.store.object("P-001")["version"], 1)
        self.assertEqual(len(self.store.history("P-001")), 1)
        retry = self.store.execute(self.request())
        self.assertTrue(retry["duplicate"])
        self.assertEqual(self.store.object("P-001")["version"], 1)
        self.assertEqual(len(self.store.history("P-001")), 1)

if __name__ == "__main__":
    unittest.main(verbosity=2)
