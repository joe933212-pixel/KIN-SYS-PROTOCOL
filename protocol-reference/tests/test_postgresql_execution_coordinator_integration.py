# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Real PostgreSQL tests for shared institutional execution coordination."""
from __future__ import annotations

import multiprocessing
import os
import sys
import unittest
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DSN = os.environ.get("KIN_SYS_POSTGRES_TEST_DSN")
from postgresql_migrations import apply_migrations
from postgresql_execution_coordinator import PostgreSQLInstitutionalExecutionCoordinator
from postgresql_minah_deployment_registry import PostgreSQLInstitutionalMinahDeploymentRegistry
from institutional_minah_deployment import MinahDeploymentError


def _acquire_execution_lease_in_process(arguments):
    """Acquire through a fresh OS process and an independently opened DB connection."""
    dsn, schema, tenant_id, deployment_id = arguments
    import psycopg
    from psycopg import sql

    def connect():
        connection = psycopg.connect(dsn)
        connection.execute(
            sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
        )
        connection.commit()
        return connection

    coordinator = PostgreSQLInstitutionalExecutionCoordinator(connect)
    lease = coordinator.acquire_execution_lease(
        tenant_id=tenant_id, deployment_id=deployment_id
    )
    return lease["fencing_token"]


def _validate_execution_lease_in_process(arguments):
    """Validate a lease issued elsewhere using a fresh process and DB connection."""
    dsn, schema, lease = arguments
    import psycopg
    from psycopg import sql

    def connect():
        connection = psycopg.connect(dsn)
        connection.execute(
            sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
        )
        connection.commit()
        return connection

    coordinator = PostgreSQLInstitutionalExecutionCoordinator(connect)
    result = coordinator.validate_execution_lease(lease=lease)
    return result["status"], result["fencing_token"]


@unittest.skipUnless(DSN, "KIN_SYS_POSTGRES_TEST_DSN is not configured")
class PostgreSQLExecutionCoordinatorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        cls.psycopg, cls.sql = psycopg, sql

    def setUp(self):
        self.schema = "kin_sys_coord_" + uuid.uuid4().hex
        self.admin = self.psycopg.connect(DSN, autocommit=True)
        self.admin.execute(
            self.sql.SQL("CREATE SCHEMA {}").format(self.sql.Identifier(self.schema))
        )
        self.tenant = "tenant_" + uuid.uuid4().hex[:12]
        self.deployment = "deployment_" + uuid.uuid4().hex[:12]

        def connect():
            connection = self.psycopg.connect(DSN)
            connection.execute(
                self.sql.SQL("SET search_path TO {}").format(
                    self.sql.Identifier(self.schema)
                )
            )
            # Leave the fresh connection idle. The production adapters own
            # their transactions; an uncommitted SET search_path would make
            # connection.transaction() a nested savepoint whose outer
            # transaction is rolled back when the adapter closes the connection.
            connection.commit()
            return connection

        self.connect = connect
        with self.connect() as connection:
            apply_migrations(connection)
            connection.execute(
                """INSERT INTO minah_deployment_versions
                   (tenant_id,deployment_id,version,status,config_json,digest,created_at)
                   VALUES (%s,%s,'v1','ACTIVE','{}'::jsonb,'sha256:test',CURRENT_TIMESTAMP)""",
                (self.tenant, self.deployment),
            )
            connection.execute(
                "INSERT INTO minah_deployment_heads VALUES (%s,%s,'v1')",
                (self.tenant, self.deployment),
            )
            connection.execute(
                "INSERT INTO minah_deployment_epochs VALUES (%s,%s,1)",
                (self.tenant, self.deployment),
            )
            connection.execute(
                "INSERT INTO minah_deployment_fence_sequences VALUES (%s,%s,0)",
                (self.tenant, self.deployment),
            )
        self.coordinator_a = PostgreSQLInstitutionalExecutionCoordinator(self.connect)
        self.coordinator_b = PostgreSQLInstitutionalExecutionCoordinator(self.connect)

    def tearDown(self):
        self.admin.execute(
            self.sql.SQL("DROP SCHEMA {} CASCADE").format(self.sql.Identifier(self.schema))
        )
        self.admin.close()

    def test_independent_coordinators_share_durable_monotonic_fencing_sequence(self):
        first = self.coordinator_a.acquire_execution_lease(
            tenant_id=self.tenant, deployment_id=self.deployment
        )
        self.assertEqual(self.coordinator_b.validate_execution_lease(lease=first)["status"], "VALID")
        second = self.coordinator_b.acquire_execution_lease(
            tenant_id=self.tenant, deployment_id=self.deployment
        )
        self.assertGreater(second["fencing_token"], first["fencing_token"])
        self.assertEqual(self.coordinator_a.validate_execution_lease(lease=first)["status"], "VALID")

    def test_concurrent_workers_receive_distinct_monotonic_fencing_tokens(self):
        def acquire(_):
            return self.coordinator_a.acquire_execution_lease(
                tenant_id=self.tenant, deployment_id=self.deployment
            )["fencing_token"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            tokens = list(pool.map(acquire, range(8)))
        self.assertEqual(len(set(tokens)), 8)
        self.assertEqual(sorted(tokens), list(range(min(tokens), min(tokens) + 8)))

    def test_independent_os_processes_share_postgresql_fencing_authority(self):
        # Spawn real worker processes rather than threads. Each worker imports
        # its own coordinator instance and opens its own PostgreSQL connection;
        # only the database rows coordinate the monotonic fencing sequence.
        arguments = (DSN, self.schema, self.tenant, self.deployment)
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=6, mp_context=context) as pool:
            tokens = list(pool.map(
                _acquire_execution_lease_in_process,
                [arguments] * 6,
            ))
        self.assertEqual(len(set(tokens)), 6)
        self.assertEqual(sorted(tokens), list(range(min(tokens), min(tokens) + 6)))


    def test_lease_issued_in_one_process_is_validated_in_another(self):
        lease = self.coordinator_a.acquire_execution_lease(
            tenant_id=self.tenant, deployment_id=self.deployment
        )
        arguments = (DSN, self.schema, lease)
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=3, mp_context=context) as pool:
            validations = list(pool.map(
                _validate_execution_lease_in_process,
                [arguments] * 3,
            ))
        self.assertEqual(
            validations,
            [("VALID", lease["fencing_token"])] * 3,
        )

    def test_lifecycle_epoch_change_fences_existing_lease(self):
        lease = self.coordinator_a.acquire_execution_lease(
            tenant_id=self.tenant, deployment_id=self.deployment
        )
        with self.connect() as connection:
            connection.execute(
                "UPDATE minah_deployment_epochs SET epoch=epoch+1 WHERE tenant_id=%s AND deployment_id=%s",
                (self.tenant, self.deployment),
            )
        with self.assertRaisesRegex(MinahDeploymentError, "LEASE_FENCED"):
            self.coordinator_b.validate_execution_lease(lease=lease)

    def test_postgresql_lifecycle_transition_and_epoch_fencing_share_authority(self):
        lease = self.coordinator_a.acquire_execution_lease(
            tenant_id=self.tenant, deployment_id=self.deployment
        )
        registry = PostgreSQLInstitutionalMinahDeploymentRegistry(self.connect)
        result = registry.transition(
            tenant_id=self.tenant,
            deployment_id=self.deployment,
            expected_version="v1",
            new_version="v2",
            new_status="SUSPENDED",
            actor_ref="operator_1",
            event_id="event_suspend_1",
            evidence_ref="approval_1",
        )
        self.assertEqual(result["status"], "SUSPENDED")
        self.assertEqual(result["execution_epoch"], 2)
        current = registry.get(tenant_id=self.tenant, deployment_id=self.deployment)
        self.assertEqual(current["version"], "v2")
        self.assertEqual(current["status"], "SUSPENDED")
        self.assertEqual(len(registry.list_events(
            tenant_id=self.tenant, deployment_id=self.deployment
        )), 1)
        with self.assertRaisesRegex(MinahDeploymentError, "LEASE_FENCED"):
            self.coordinator_b.validate_execution_lease(lease=lease)

    def test_guarded_callback_runs_and_release_is_idempotent(self):
        lease = self.coordinator_a.acquire_execution_lease(
            tenant_id=self.tenant, deployment_id=self.deployment
        )
        self.assertEqual(
            self.coordinator_b.execute_under_execution_lease(
                lease=lease, action=lambda: "committed-under-coordinator-lock"
            ),
            "committed-under-coordinator-lock",
        )
        self.coordinator_a.release_execution_lease(lease=lease)
        self.coordinator_b.release_execution_lease(lease=lease)
        with self.assertRaisesRegex(MinahDeploymentError, "LEASE_RELEASED"):
            self.coordinator_a.validate_execution_lease(lease=lease)


if __name__ == "__main__":
    unittest.main()
