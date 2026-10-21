import concurrent.futures
import os
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
        result = self.store.execute(self.request(rule_version="0.9"))
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
