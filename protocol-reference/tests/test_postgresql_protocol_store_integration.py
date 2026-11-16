# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Real PostgreSQL integration tests for the durable IVM transition store."""
from __future__ import annotations

import os
import sys
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DSN = os.environ.get("KIN_SYS_POSTGRES_TEST_DSN")


@unittest.skipUnless(DSN, "KIN_SYS_POSTGRES_TEST_DSN is not configured")
class PostgreSQLProtocolStoreIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        cls.psycopg, cls.sql = psycopg, sql

    def setUp(self):
        from postgresql_migrations import apply_migrations
        from postgresql_protocol_store import PostgreSQLProtocolStore

        self.schema = "kin_sys_ivm_" + uuid.uuid4().hex
        self.admin = self.psycopg.connect(DSN, autocommit=True)
        self.admin.execute(
            self.sql.SQL("CREATE SCHEMA {}").format(self.sql.Identifier(self.schema))
        )

        def connect():
            connection = self.psycopg.connect(DSN)
            connection.execute(
                self.sql.SQL("SET search_path TO {}").format(
                    self.sql.Identifier(self.schema)
                )
            )
            connection.commit()
            return connection

        self.connect = connect
        with self.connect() as connection:
            apply_migrations(connection)
        self.store = PostgreSQLProtocolStore(self.connect)

    def tearDown(self):
        self.store.close()
        self.admin.execute(
            self.sql.SQL("DROP SCHEMA {} CASCADE").format(self.sql.Identifier(self.schema))
        )
        self.admin.close()

    @staticmethod
    def event(event_id="E-IVM-001", process_id="P-IVM-001", **extra):
        value = {
            "event_id": event_id, "process_id": process_id,
            "actor_id": "AUTHORITY-001", "event_type": "ACTIVATE",
            "rule_set": "LICENCE-RULES", "rule_version": "1.0",
        }
        value.update(extra)
        return value

    def create_process(self, process_id="P-IVM-001"):
        self.store.create_process(process_id=process_id)

    def test_transition_and_history_survive_adapter_replacement(self):
        self.create_process()
        result = self.store.execute(self.event())
        self.assertEqual(result.status, "ACCEPTED")
        self.store = type(self.store)(self.connect)
        self.assertEqual(self.store.process("P-IVM-001")["state"], "ACTIVE")
        self.assertEqual(self.store.process("P-IVM-001")["state_version"], 1)
        self.assertEqual(len(self.store.history("P-IVM-001")), 1)
        self.assertEqual(self.store.event("E-IVM-001")["outcome"], "ACCEPTED")
        self.store.verify_integrity()

    def test_identical_retry_returns_original_decision_without_second_transition(self):
        self.create_process()
        first = self.store.execute(self.event())
        retry = type(self.store)(self.connect).execute(self.event())
        self.assertEqual(first.history_id, retry.history_id)
        self.assertTrue(retry.duplicate)
        self.assertEqual(self.store.process("P-IVM-001")["state_version"], 1)
        self.assertEqual(len(self.store.history("P-IVM-001")), 1)

    def test_event_id_reuse_with_different_content_fails_closed(self):
        self.create_process()
        self.store.execute(self.event())
        from transition import EventIdConflict
        with self.assertRaisesRegex(EventIdConflict, "EVENT_ID_REUSED"):
            self.store.execute(self.event(actor_id="OTHER-ACTOR"))

    def test_precommit_failure_rolls_back_process_event_history_and_fence(self):
        self.create_process()
        fence = {
            "tenant_id": "tenant-ug", "deployment_id": "MINAH-UG-01",
            "execution_epoch": 4, "fencing_token": 12,
        }
        with self.assertRaisesRegex(RuntimeError, "INJECTED_FAILURE_BEFORE_COMMIT"):
            self.store.execute(self.event(execution_fence=fence), fail_before_commit=True)
        self.assertEqual(self.store.process("P-IVM-001")["state"], "PENDING")
        self.assertEqual(self.store.process("P-IVM-001")["state_version"], 0)
        self.assertIsNone(self.store.event("E-IVM-001"))
        self.assertEqual(self.store.history("P-IVM-001"), [])
        with self.connect() as connection:
            row = connection.execute(
                "SELECT highest_token,highest_epoch FROM ivm_execution_fences "
                "WHERE tenant_id=%s AND deployment_id=%s",
                ("tenant-ug", "MINAH-UG-01"),
            ).fetchone()
        self.assertIsNone(row)
        self.assertEqual(self.store.execute(self.event(execution_fence=fence)).status, "ACCEPTED")

    def test_execution_fence_is_monotonic_and_rejects_stale_tokens(self):
        self.create_process("P-FENCE-1")
        first = self.event(
            event_id="E-FENCE-1", process_id="P-FENCE-1",
            execution_fence={"tenant_id":"tenant-ug","deployment_id":"MINAH-UG-01",
                             "execution_epoch":4,"fencing_token":12},
        )
        self.assertEqual(self.store.execute(first).status, "ACCEPTED")
        self.create_process("P-FENCE-2")
        stale = self.event(
            event_id="E-FENCE-2", process_id="P-FENCE-2",
            execution_fence={"tenant_id":"tenant-ug","deployment_id":"MINAH-UG-01",
                             "execution_epoch":3,"fencing_token":13},
        )
        from transition import ProtocolError
        with self.assertRaisesRegex(ProtocolError, "EXECUTION_FENCE_STALE"):
            self.store.execute(stale)

    def test_concurrent_identical_events_commit_once_across_independent_adapters(self):
        self.create_process()
        def submit(_):
            return type(self.store)(self.connect).execute(self.event())
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(submit, range(8)))
        self.assertTrue(all(item.status == "ACCEPTED" for item in results))
        self.assertEqual(sum(item.duplicate for item in results), 7)
        self.assertEqual(self.store.process("P-IVM-001")["state_version"], 1)
        self.assertEqual(len(self.store.history("P-IVM-001")), 1)

    def test_concurrent_distinct_events_for_one_process_commit_only_one_transition(self):
        self.create_process()
        def submit(event_id):
            return type(self.store)(self.connect).execute(
                self.event(event_id=event_id)
            ).status
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(submit, ["E-RACE-A", "E-RACE-B"]))
        self.assertEqual(sorted(outcomes), ["ACCEPTED", "REJECTED"])
        self.assertEqual(self.store.process("P-IVM-001")["state_version"], 1)
        self.assertEqual(len(self.store.history("P-IVM-001")), 1)

    def test_transition_history_is_append_only(self):
        self.create_process()
        self.store.execute(self.event())
        with self.assertRaisesRegex(self.psycopg.errors.RaiseException, "IVM_HISTORY_APPEND_ONLY"):
            with self.connect() as connection:
                connection.execute(
                    "UPDATE ivm_transition_history SET to_state='ALTERED' WHERE event_id=%s",
                    ("E-IVM-001",),
                )
        with self.assertRaisesRegex(self.psycopg.errors.RaiseException, "IVM_HISTORY_APPEND_ONLY"):
            with self.connect() as connection:
                connection.execute(
                    "DELETE FROM ivm_transition_history WHERE event_id=%s",
                    ("E-IVM-001",),
                )

    def test_pipeline_runs_with_independent_postgresql_cee_and_ivm_adapters(self):
        from postgresql_cee_journal import PostgreSQLCEEJournal
        from protocol_pipeline import KinSysProtocolPipeline

        cee = PostgreSQLCEEJournal(self.connect)
        pipeline = KinSysProtocolPipeline(cee_journal=cee, ivm_store=self.store)
        request = {
            "operation_id": "OP-POSTGRES-PAIR-001",
            "event_id": "EV-POSTGRES-PAIR-001",
            "process_id": "PROC-POSTGRES-PAIR-001",
            "actor_id": "AUTHORITY-001",
            "event_type": "ACTIVATE",
            "jurisdiction": "UG-DEMO",
            "rule_set": "KIN-SYS-DEMO-RULES",
            "rule_version": "1.0",
            "evidence": {
                "consignment_id": "PROC-POSTGRES-PAIR-001",
                "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
                "rule_refs": ["SYNTHETIC-RULE-1"],
            },
        }
        try:
            result = pipeline.execute(request)
            self.assertEqual(result["status"], "COMMITTED")
            self.assertEqual(pipeline.ivm.process("PROC-POSTGRES-PAIR-001")["state"], "ACTIVE")
            self.assertEqual(pipeline.ivm.process("PROC-POSTGRES-PAIR-001")["state_version"], 1)
            self.assertEqual(pipeline.cee.get("OP-POSTGRES-PAIR-001")["phase"], "FINALIZED")
            pipeline._assert_store_integrity()
        finally:
            pipeline.close()

    def test_pipeline_accepts_injected_postgresql_ivm_store_and_checks_integrity(self):
        from protocol_pipeline import KinSysProtocolPipeline
        pipeline = KinSysProtocolPipeline(ivm_store=self.store)
        try:
            pipeline._assert_store_integrity()
        finally:
            pipeline.close()
        # Pipeline owns the injected store lifecycle; adapter close is safe
        # because the store holds no persistent connection.


    def test_postgresql_cee_ivm_crash_window_recovers_after_independent_adapter_restart(self):
        from postgresql_cee_journal import PostgreSQLCEEJournal
        from postgresql_protocol_store import PostgreSQLProtocolStore
        from protocol_pipeline import KinSysProtocolPipeline

        operation_id = "OP-POSTGRES-CRASH-001"
        process_id = "PROC-POSTGRES-CRASH-001"
        event_id = "EV-POSTGRES-CRASH-001"
        request = {
            "operation_id": operation_id,
            "event_id": event_id,
            "process_id": process_id,
            "actor_id": "AUTHORITY-001",
            "event_type": "ACTIVATE",
            "jurisdiction": "UG-DEMO",
            "rule_set": "KIN-SYS-DEMO-RULES",
            "rule_version": "1.0",
            "evidence": {
                "consignment_id": process_id,
                "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
                "rule_refs": ["SYNTHETIC-RULE-1"],
            },
        }
        self.store.create_process(process_id=process_id)
        first = KinSysProtocolPipeline(
            cee_journal=PostgreSQLCEEJournal(self.connect),
            ivm_store=PostgreSQLProtocolStore(self.connect),
        )
        try:
            with self.assertRaisesRegex(
                RuntimeError, "INJECTED_FAILURE_AFTER_IVM_COMMIT"
            ):
                first.execute(request, fail_after_ivm_commit=True)
            self.assertEqual(first.cee.get(operation_id)["phase"], "COMMITTING")
            self.assertEqual(first.ivm.process(process_id)["state"], "ACTIVE")
            self.assertEqual(len(first.ivm.history(process_id)), 1)
        finally:
            first.close()

        # Reconstruct both adapters over the same durable schema to model a
        # process restart; recovery must reconcile the existing IVM commit.
        resumed_ivm = PostgreSQLProtocolStore(self.connect)
        resumed = KinSysProtocolPipeline(
            cee_journal=PostgreSQLCEEJournal(self.connect),
            ivm_store=resumed_ivm,
        )
        try:
            result = resumed.recover(operation_id, expected_request=request)
            self.assertEqual(result["status"], "COMMITTED")
            self.assertTrue(result["duplicate"])
            self.assertEqual(resumed.cee.get(operation_id)["phase"], "FINALIZED")
            self.assertEqual(resumed.ivm.process(process_id)["state_version"], 1)
            self.assertEqual(len(resumed.ivm.history(process_id)), 1)
            self.assertEqual(resumed.ivm.event(event_id)["outcome"], "ACCEPTED")
            resumed._assert_store_integrity()
        finally:
            resumed.close()


if __name__ == "__main__":
    unittest.main()
