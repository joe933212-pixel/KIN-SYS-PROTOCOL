# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Real PostgreSQL tests for shared institutional execution coordination."""
from __future__ import annotations

import os
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DSN = os.environ.get("KIN_SYS_POSTGRES_TEST_DSN")
from postgresql_migrations import apply_migrations
from postgresql_execution_coordinator import PostgreSQLInstitutionalExecutionCoordinator
from institutional_minah_deployment import MinahDeploymentError


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
