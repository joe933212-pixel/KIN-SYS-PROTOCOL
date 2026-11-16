# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""PostgreSQL integration tests for the durable CEE lifecycle journal."""
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
class PostgreSQLCEEJournalIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        cls.psycopg, cls.sql = psycopg, sql

    def setUp(self):
        from postgresql_migrations import apply_migrations

        self.schema = "kin_sys_cee_" + uuid.uuid4().hex
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

        from postgresql_cee_journal import PostgreSQLCEEJournal
        self.journal = PostgreSQLCEEJournal(self.connect)

    def tearDown(self):
        self.journal.close()
        self.admin.execute(
            self.sql.SQL("DROP SCHEMA {} CASCADE").format(self.sql.Identifier(self.schema))
        )
        self.admin.close()

    def test_operation_and_lifecycle_history_survive_adapter_replacement(self):
        request = {
            "operation_id": "OP-RESTART-001",
            "_institutional_runtime_binding": {"tenant_id": "tenant-ug"},
            "instruction": {"action": "TRANSFER"},
        }
        started = self.journal.start("OP-RESTART-001", request)
        self.assertEqual(started["phase"], "REQUESTED")
        self.journal.advance(
            "OP-RESTART-001", "MINAH_REVIEWED",
            detail={"advisory_only": True},
            minah={"status": "ADVISORY_ONLY", "may_authorise": False},
        )
        self.journal.record_observation(
            "OP-RESTART-001", "RECOVERY_CHECK", "VERIFIED",
            {"source": "integration-test"},
        )

        # A new adapter has no process-local state; all evidence is read from
        # the same PostgreSQL schema through a new connection factory call.
        restarted = type(self.journal)(self.connect)
        recovered = restarted.get("OP-RESTART-001")
        self.assertEqual(recovered["phase"], "MINAH_REVIEWED")
        self.assertEqual(recovered["request_json"], request)
        self.assertEqual(recovered["minah_json"]["may_authorise"], False)
        self.assertEqual(
            [event["phase"] for event in restarted.events("OP-RESTART-001")],
            ["REQUESTED", "MINAH_REVIEWED"],
        )
        self.assertEqual(
            restarted.observations("OP-RESTART-001")[0]["detail"],
            {"source": "integration-test"},
        )

    def test_operation_id_reuse_with_different_request_fails_closed(self):
        self.journal.start("OP-CONFLICT-001", {"operation_id": "OP-CONFLICT-001", "value": 1})
        from protocol_pipeline import ExecutionConflict
        with self.assertRaisesRegex(ExecutionConflict, "OPERATION_ID_REUSED"):
            self.journal.start(
                "OP-CONFLICT-001",
                {"operation_id": "OP-CONFLICT-001", "value": 2},
            )

    def test_concurrent_identical_starts_create_one_operation_and_one_event(self):
        request = {"operation_id": "OP-CONCURRENT-001", "value": "same"}
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(
                lambda _: self.journal.start("OP-CONCURRENT-001", request), range(8)
            ))
        self.assertTrue(all(row["request_digest"] == rows[0]["request_digest"] for row in rows))
        self.assertEqual(len(self.journal.events("OP-CONCURRENT-001")), 1)

    def test_lifecycle_transition_and_event_are_atomic_and_ordered(self):
        self.journal.start("OP-PHASE-001", {"operation_id": "OP-PHASE-001"})
        self.journal.advance("OP-PHASE-001", "MINAH_REVIEWED")
        self.journal.advance("OP-PHASE-001", "IDENTITY_VERIFIED")
        self.assertEqual(
            [event["phase"] for event in self.journal.events("OP-PHASE-001")],
            ["REQUESTED", "MINAH_REVIEWED", "IDENTITY_VERIFIED"],
        )
        from protocol_pipeline import LifecycleViolation
        with self.assertRaisesRegex(LifecycleViolation, "ILLEGAL_CEE_TRANSITION"):
            self.journal.advance("OP-PHASE-001", "FINALIZED", outcome="PASSED")

    def test_lifecycle_events_and_observations_are_append_only(self):
        self.journal.start("OP-IMMUTABLE-001", {"operation_id": "OP-IMMUTABLE-001"})
        with self.assertRaisesRegex(self.psycopg.errors.RaiseException, "CEE_APPEND_ONLY"):
            with self.connect() as connection:
                connection.execute(
                    "UPDATE cee_events SET status='ALTERED' WHERE operation_id=%s",
                    ("OP-IMMUTABLE-001",),
                )
        self.journal.record_observation(
            "OP-IMMUTABLE-001", "AUDIT", "RECORDED", {"ref": "evidence:1"}
        )
        with self.assertRaisesRegex(self.psycopg.errors.RaiseException, "CEE_APPEND_ONLY"):
            with self.connect() as connection:
                connection.execute(
                    "DELETE FROM cee_observations WHERE operation_id=%s",
                    ("OP-IMMUTABLE-001",),
                )


if __name__ == "__main__":
    unittest.main()
