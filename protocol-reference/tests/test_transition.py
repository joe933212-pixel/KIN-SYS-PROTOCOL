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
from transition import EventIdConflict, ProtocolError, ProtocolStore


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

    def test_downstream_fence_rejects_older_writer_after_newer_token_is_seen(self):
        self.store.create_process(process_id="P-002")
        newer = {
            "tenant_id": "tenant-ug", "deployment_id": "MINAH-UG-01",
            "execution_epoch": 4, "fencing_token": 12,
        }
        older = {**newer, "execution_epoch": 3, "fencing_token": 11}
        self.store.execute(self.event(execution_fence=newer))
        with self.assertRaisesRegex(ProtocolError, "EXECUTION_FENCE_STALE"):
            self.store.execute(self.event(
                event_id="E-002", process_id="P-002", execution_fence=older))
        self.assertEqual(self.store.process("P-002")["state"], "PENDING")
        self.assertIsNone(self.store.event("E-002"))

    def test_downstream_fence_rejects_reuse_of_token_for_a_distinct_event(self):
        self.store.create_process(process_id="P-002")
        fence = {
            "tenant_id": "tenant-ug", "deployment_id": "MINAH-UG-01",
            "execution_epoch": 4, "fencing_token": 12,
        }
        self.store.execute(self.event(execution_fence=fence))
        # The same lease/fence cannot authorise a second business event, even
        # when the second process is otherwise independently eligible.
        with self.assertRaisesRegex(ProtocolError, "EXECUTION_FENCE_STALE"):
            self.store.execute(self.event(
                event_id="E-002", process_id="P-002", execution_fence=fence))
        self.assertEqual(self.store.process("P-002")["state"], "PENDING")
        self.assertIsNone(self.store.event("E-002"))

    def test_fence_sequences_are_isolated_by_tenant_and_deployment(self):
        self.store.create_process(process_id="P-002")
        self.store.create_process(process_id="P-003")
        shared_deployment_other_tenant = {
            "tenant_id": "tenant-ke", "deployment_id": "MINAH-UG-01",
            "execution_epoch": 1, "fencing_token": 1,
        }
        other_deployment_same_tenant = {
            "tenant_id": "tenant-ug", "deployment_id": "MINAH-UG-02",
            "execution_epoch": 1, "fencing_token": 1,
        }
        first = {
            "tenant_id": "tenant-ug", "deployment_id": "MINAH-UG-01",
            "execution_epoch": 1, "fencing_token": 1,
        }

        self.assertEqual(
            self.store.execute(self.event(execution_fence=first)).status,
            "ACCEPTED",
        )
        self.assertEqual(
            self.store.execute(self.event(
                event_id="E-002", process_id="P-002",
                execution_fence=shared_deployment_other_tenant,
            )).status,
            "ACCEPTED",
        )
        self.assertEqual(
            self.store.execute(self.event(
                event_id="E-003", process_id="P-003",
                execution_fence=other_deployment_same_tenant,
            )).status,
            "ACCEPTED",
        )

    def test_fence_watermark_rolls_back_with_failed_transition(self):
        self.store.create_process(process_id="P-002")
        scope = {"tenant_id": "tenant-ug", "deployment_id": "MINAH-UG-01"}
        self.store.execute(self.event(execution_fence={
            **scope, "execution_epoch": 2, "fencing_token": 10,
        }))

        # Fence advancement and the business transition share one SQLite
        # transaction. A failure after fence persistence must roll both back.
        with self.assertRaisesRegex(RuntimeError, "INJECTED_FAILURE_BEFORE_COMMIT"):
            self.store.execute(self.event(
                event_id="E-FAILED", process_id="P-002",
                execution_fence={
                    **scope, "execution_epoch": 2, "fencing_token": 12,
                },
            ), fail_before_commit=True)

        self.assertEqual(self.store.process("P-002")["state"], "PENDING")
        self.assertIsNone(self.store.event("E-FAILED"))
        self.assertEqual(self.store.history("P-002"), [])

        # Token 11 remains valid: failed token 12 was never committed.
        result = self.store.execute(self.event(
            event_id="E-RETRY", process_id="P-002",
            execution_fence={
                **scope, "execution_epoch": 2, "fencing_token": 11,
            },
        ))
        self.assertEqual(result.status, "ACCEPTED")
        self.assertEqual(self.store.process("P-002")["state_version"], 1)

    def test_recovery_retry_can_use_new_fence_without_changing_instruction_identity(self):
        self.store.execute(self.event(execution_fence={
            "tenant_id": "tenant-ug", "deployment_id": "MINAH-UG-01",
            "execution_epoch": 4, "fencing_token": 12,
        }))
        retry = self.store.execute(self.event(execution_fence={
            "tenant_id": "tenant-ug", "deployment_id": "MINAH-UG-01",
            "execution_epoch": 4, "fencing_token": 13,
        }))
        self.assertTrue(retry.duplicate)
        self.assertEqual(self.store.process("P-001")["state_version"], 1)

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

    def test_concurrent_distinct_events_for_same_process_commit_at_most_one_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "protocol.sqlite")
            setup = ProtocolStore(database=database)
            setup.create_process(process_id="P-SHARED")
            setup.close()
            barrier = threading.Barrier(2)

            def submit(event_id):
                store = ProtocolStore(database=database)
                try:
                    barrier.wait(timeout=5)
                    result = store.execute(self.event(event_id=event_id, process_id="P-SHARED"))
                    return result.status
                finally:
                    store.close()

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                statuses = list(pool.map(submit, ["E-SHARED-1", "E-SHARED-2"]))
            self.assertEqual(sorted(statuses), ["ACCEPTED", "REJECTED"])

            verify = ProtocolStore(database=database)
            try:
                process = verify.process("P-SHARED")
                self.assertEqual(process["state"], "ACTIVE")
                self.assertEqual(process["state_version"], 1)
                self.assertEqual(len(verify.history("P-SHARED")), 1)
                outcomes = [verify.event(event_id)["outcome"] for event_id in ("E-SHARED-1", "E-SHARED-2")]
                self.assertEqual(sorted(outcomes), ["ACCEPTED", "REJECTED"])
            finally:
                verify.close()

    def test_concurrent_event_id_collision_across_processes_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "protocol.sqlite")
            setup = ProtocolStore(database=database)
            setup.create_process(process_id="P-001")
            setup.create_process(process_id="P-002")
            setup.close()
            barrier = threading.Barrier(2)

            def submit(process_id):
                store = ProtocolStore(database=database)
                try:
                    barrier.wait(timeout=5)
                    try:
                        result = store.execute(self.event(event_id="E-SHARED", process_id=process_id))
                        return ("RESULT", result.status, result.duplicate)
                    except EventIdConflict:
                        return ("CONFLICT", None, False)
                finally:
                    store.close()

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(submit, ["P-001", "P-002"]))
            self.assertEqual(sum(item[0] == "RESULT" for item in outcomes), 1)
            self.assertEqual(sum(item[0] == "CONFLICT" for item in outcomes), 1)

            verify = ProtocolStore(database=database)
            try:
                event = verify.event("E-SHARED")
                self.assertIsNotNone(event)
                self.assertIn(event["process_id"], {"P-001", "P-002"})
                self.assertEqual(len(verify.history(event["process_id"])), 1)
                other = "P-002" if event["process_id"] == "P-001" else "P-001"
                self.assertEqual(verify.history(other), [])
            finally:
                verify.close()

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


    def test_process_termination_rolls_back_fence_watermark_and_allows_safe_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "protocol.sqlite")
            marker = os.path.join(directory, "fence-before-commit.marker")
            setup = ProtocolStore(database=database)
            setup.create_process(process_id="P-001")
            setup.close()

            fence = {
                "tenant_id": "tenant-ug", "deployment_id": "MINAH-UG-01",
                "execution_epoch": 7, "fencing_token": 21,
            }
            script = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from transition import ProtocolStore
store = ProtocolStore(database=sys.argv[2])
def pause_at_crash_point():
    with open(sys.argv[3], "w", encoding="utf-8") as marker:
        marker.write("fence and business writes staged before commit")
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
    "rule_set": "LICENCE-RULES", "rule_version": "1.0",
    "execution_fence": {
        "tenant_id": "tenant-ug", "deployment_id": "MINAH-UG-01",
        "execution_epoch": 7, "fencing_token": 21
    }
})
"""
            self._terminate_child_at_marker(script, database, marker, -15)

            reopened = ProtocolStore(database=database)
            try:
                self.assertEqual(reopened.process("P-001")["state"], "PENDING")
                self.assertEqual(reopened.process("P-001")["state_version"], 0)
                self.assertIsNone(reopened.event("E-001"))
                self.assertEqual(reopened.history("P-001"), [])
                watermark = reopened.db.execute(
                    "SELECT highest_token, highest_epoch FROM execution_fences "
                    "WHERE tenant_id=? AND deployment_id=?",
                    ("tenant-ug", "MINAH-UG-01"),
                ).fetchone()
                self.assertIsNone(watermark)

                # The crash-injection trigger is persistent schema state, but
                # its Python callback belonged to the terminated process.
                # Remove the test-only trigger before exercising normal retry.
                reopened.db.execute("DROP TRIGGER pause_before_history")
                reopened.db.commit()

                # The token was never committed, so the same authoritative
                # lease can safely retry the exact instruction after recovery.
                retry = reopened.execute({
                    "event_id": "E-001", "process_id": "P-001",
                    "actor_id": "AUTHORITY-001", "event_type": "ACTIVATE",
                    "rule_set": "LICENCE-RULES", "rule_version": "1.0",
                    "execution_fence": fence,
                })
                self.assertEqual(retry.status, "ACCEPTED")
                self.assertEqual(reopened.process("P-001")["state_version"], 1)
                self.assertEqual(len(reopened.history("P-001")), 1)
                committed = reopened.db.execute(
                    "SELECT highest_token, highest_epoch FROM execution_fences "
                    "WHERE tenant_id=? AND deployment_id=?",
                    ("tenant-ug", "MINAH-UG-01"),
                ).fetchone()
                self.assertEqual(tuple(committed), (21, 7))
            finally:
                reopened.close()

if __name__ == "__main__":
    unittest.main(verbosity=2)
