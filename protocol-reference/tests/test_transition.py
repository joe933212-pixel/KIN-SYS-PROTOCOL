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
from transition import EventIdConflict, ProtocolStore


class TransitionContractTests(unittest.TestCase):
    def setUp(self):
        self.store = ProtocolStore()
        self.store.create_process(process_id="P-001")

    def tearDown(self):
        self.store.close()

    def event(self, **overrides):
        event = {
            "event_id": "E-001",
            "process_id": "P-001",
            "actor_id": "AUTHORITY-001",
            "event_type": "ACTIVATE",
            "rule_set": "LICENCE-RULES",
            "rule_version": "1.0",
        }
        event.update(overrides)
        return event

    def test_authorised_transition_commits_state_and_history(self):
        result = self.store.execute(self.event())
        self.assertEqual(result.status, "ACCEPTED")
        self.assertEqual(self.store.process("P-001")["state"], "ACTIVE")
        history = self.store.history("P-001")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["event_id"], "E-001")
        self.assertEqual(history[0]["rule_version"], "1.0")

    def test_identical_duplicate_does_not_transition_twice(self):
        self.store.execute(self.event())
        before = self.store.process("P-001")["state_version"]
        result = self.store.execute(self.event())
        self.assertTrue(result.duplicate)
        self.assertEqual(self.store.process("P-001")["state_version"], before)
        self.assertEqual(len(self.store.history("P-001")), 1)

    def test_event_id_reuse_with_different_content_is_conflict(self):
        self.store.execute(self.event())
        with self.assertRaises(EventIdConflict):
            self.store.execute(self.event(actor_id="SOMEONE-ELSE"))
        self.assertEqual(self.store.process("P-001")["state"], "ACTIVE")
        self.assertEqual(len(self.store.history("P-001")), 1)

    def test_wrong_authority_is_audited_without_state_change(self):
        result = self.store.execute(self.event(actor_id="UNAUTHORISED"))
        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(result.reason, "AUTHORITY_MISMATCH")
        self.assertEqual(self.store.process("P-001")["state"], "PENDING")
        self.assertEqual(self.store.event("E-001")["outcome"], "REJECTED")
        self.assertEqual(self.store.history("P-001"), [])

    def test_revoked_authority_is_rejected(self):
        self.store.set_authority_active("P-001", False)
        result = self.store.execute(self.event())
        self.assertEqual(result.reason, "AUTHORITY_REVOKED")
        self.assertEqual(self.store.process("P-001")["state"], "PENDING")

    def test_false_rule_is_audited_without_state_change(self):
        self.store.set_rule("P-001", version="1.0", eligible=False)
        result = self.store.execute(self.event())
        self.assertEqual(result.reason, "RULE_REJECTED")
        self.assertIsNotNone(self.store.event("E-001"))
        self.assertEqual(self.store.history("P-001"), [])

    def test_rule_version_mismatch_is_rejected(self):
        result = self.store.execute(self.event(rule_version="0.9"))
        self.assertEqual(result.reason, "RULE_VERSION_MISMATCH")
        self.assertEqual(self.store.process("P-001")["state"], "PENDING")

    def test_failure_before_commit_rolls_back_state_event_and_history(self):
        with self.assertRaisesRegex(RuntimeError, "INJECTED_FAILURE_BEFORE_COMMIT"):
            self.store.execute(self.event(), fail_before_commit=True)
        self.assertEqual(self.store.process("P-001")["state"], "PENDING")
        self.assertEqual(self.store.process("P-001")["state_version"], 0)
        self.assertIsNone(self.store.event("E-001"))
        self.assertEqual(self.store.history("P-001"), [])

    def test_later_rule_change_does_not_rewrite_history(self):
        self.store.execute(self.event())
        self.store.set_rule("P-001", version="2.0", eligible=False)
        history = self.store.history("P-001")
        self.assertEqual(history[0]["rule_version"], "1.0")

    def test_undefined_transition_is_rejected_and_audited(self):
        result = self.store.execute(self.event(event_type="DELETE"))
        self.assertEqual(result.reason, "UNDEFINED_TRANSITION")
        self.assertEqual(self.store.process("P-001")["state"], "PENDING")
        self.assertIsNotNone(self.store.event("E-001"))

    def test_committed_state_history_and_duplicate_outcome_survive_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "protocol.sqlite")
            store = ProtocolStore(database=database)
            store.create_process(process_id="P-001")
            first = store.execute(self.event())
            self.assertEqual(first.status, "ACCEPTED")
            store.close()

            reopened = ProtocolStore(database=database)
            try:
                self.assertEqual(reopened.process("P-001")["state"], "ACTIVE")
                self.assertEqual(reopened.process("P-001")["state_version"], 1)
                self.assertEqual(len(reopened.history("P-001")), 1)
                retry = reopened.execute(self.event())
                self.assertTrue(retry.duplicate)
                self.assertEqual(reopened.process("P-001")["state_version"], 1)
                self.assertEqual(len(reopened.history("P-001")), 1)
            finally:
                reopened.close()

    def test_precommit_rollback_remains_absent_after_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "protocol.sqlite")
            store = ProtocolStore(database=database)
            store.create_process(process_id="P-001")
            with self.assertRaisesRegex(RuntimeError, "INJECTED_FAILURE_BEFORE_COMMIT"):
                store.execute(self.event(), fail_before_commit=True)
            store.close()

            reopened = ProtocolStore(database=database)
            try:
                self.assertEqual(reopened.process("P-001")["state"], "PENDING")
                self.assertEqual(reopened.process("P-001")["state_version"], 0)
                self.assertIsNone(reopened.event("E-001"))
                self.assertEqual(reopened.history("P-001"), [])
            finally:
                reopened.close()



    def test_concurrent_identical_requests_from_separate_connections_commit_once(self):
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "protocol.sqlite")
            setup = ProtocolStore(database=database)
            setup.create_process(process_id="P-001")
            setup.close()

            barrier = threading.Barrier(2)

            def submit():
                store = ProtocolStore(database=database)
                try:
                    barrier.wait(timeout=5)
                    return store.execute(self.event())
                finally:
                    store.close()

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: submit(), range(2)))

            self.assertEqual(sorted(result.status for result in results), ["ACCEPTED", "ACCEPTED"])
            self.assertEqual(sum(result.duplicate for result in results), 1)

            verify = ProtocolStore(database=database)
            try:
                self.assertEqual(verify.process("P-001")["state"], "ACTIVE")
                self.assertEqual(verify.process("P-001")["state_version"], 1)
                self.assertEqual(len(verify.history("P-001")), 1)
                self.assertEqual(verify.event("E-001")["outcome"], "ACCEPTED")
            finally:
                verify.close()


    def _terminate_child_at_marker(self, script, database, marker, exit_code):
        child = subprocess.Popen(
            [sys.executable, "-c", script, str(Path(__file__).resolve().parents[1]), database, marker],
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

    def test_process_termination_before_commit_recovers_without_partial_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "protocol.sqlite")
            marker = os.path.join(directory, "before-commit.marker")
            setup = ProtocolStore(database=database)
            setup.create_process(process_id="P-001")
            setup.close()

            script = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
from transition import ProtocolStore
store = ProtocolStore(database=sys.argv[2])
def pause_at_crash_point():
    with open(sys.argv[3], "w", encoding="utf-8") as marker:
        marker.write("inside transaction before history insert")
    while True:
        time.sleep(0.05)
store.db.create_function("pause_at_crash_point", 0, pause_at_crash_point)
store.db.execute(
    "CREATE TRIGGER pause_before_history BEFORE INSERT ON transition_history "
    "BEGIN SELECT pause_at_crash_point(); END"
)
store.db.commit()
store.execute({
    "event_id": "E-001", "process_id": "P-001",
    "actor_id": "AUTHORITY-001", "event_type": "ACTIVATE",
    "rule_set": "LICENCE-RULES", "rule_version": "1.0"
})
"""
            self._terminate_child_at_marker(script, database, marker, -15)

            reopened = ProtocolStore(database=database)
            try:
                self.assertEqual(reopened.process("P-001")["state"], "PENDING")
                self.assertEqual(reopened.process("P-001")["state_version"], 0)
                self.assertIsNone(reopened.event("E-001"))
                self.assertEqual(reopened.history("P-001"), [])
            finally:
                reopened.close()

    def test_process_termination_after_commit_before_ack_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "protocol.sqlite")
            marker = os.path.join(directory, "after-commit.marker")
            setup = ProtocolStore(database=database)
            setup.create_process(process_id="P-001")
            setup.close()

            script = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from transition import ProtocolStore
store = ProtocolStore(database=sys.argv[2])
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
            self._terminate_child_at_marker(script, database, marker, -15)

            reopened = ProtocolStore(database=database)
            try:
                self.assertEqual(reopened.process("P-001")["state"], "ACTIVE")
                self.assertEqual(reopened.process("P-001")["state_version"], 1)
                self.assertEqual(len(reopened.history("P-001")), 1)
                retry = reopened.execute({
                    "event_id": "E-001", "process_id": "P-001",
                    "actor_id": "AUTHORITY-001", "event_type": "ACTIVATE",
                    "rule_set": "LICENCE-RULES", "rule_version": "1.0"
                })
                self.assertTrue(retry.duplicate)
                self.assertEqual(reopened.process("P-001")["state_version"], 1)
                self.assertEqual(len(reopened.history("P-001")), 1)
            finally:
                reopened.close()

if __name__ == "__main__":
    unittest.main(verbosity=2)
