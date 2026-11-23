# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""PostgreSQL integration tests for durable institutional domain records."""
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
from postgresql_migrations import apply_migrations
from postgresql_institutional_registry import PostgreSQLInstitutionalRegistry
from institutional_registry import RegistryConflict, RegistryError
from institutional_onboarding_administration import InstitutionalOnboardingAdministration


@unittest.skipUnless(DSN, "KIN_SYS_POSTGRES_TEST_DSN is not configured")
class PostgreSQLInstitutionalRegistryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        cls.psycopg, cls.sql = psycopg, sql

    def setUp(self):
        self.schema = "kin_sys_domain_" + uuid.uuid4().hex
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
        self.registry = PostgreSQLInstitutionalRegistry(self.connect)
        self.other_worker = PostgreSQLInstitutionalRegistry(self.connect)

    def tearDown(self):
        self.admin.execute(
            self.sql.SQL("DROP SCHEMA {} CASCADE").format(self.sql.Identifier(self.schema))
        )
        self.admin.close()

    def record(self, *, legal_name="Example Bank"):
        return {"institution_id": "BANK-UG-01", "legal_name": legal_name}

    def create(self, *, tenant_id="tenant-a", event_id="event-1"):
        return self.registry.create_record_with_event(
            tenant_id=tenant_id, resource_type="institution",
            resource_id="BANK-UG-01", version="1.0",
            status="PENDING_VERIFICATION", payload=self.record(),
            event_id=event_id, event_type="INSTITUTION_REGISTERED",
            actor_ref="actor:registrar", evidence_ref="evidence:incorporation",
            event_payload={"registration": "received"},
        )

    def transition(self, *, new_version="2.0", new_status="VERIFIED",
                   event_id="event-transition-1"):
        return {
            "record": {"institution_id": "BANK-UG-01",
                       "legal_name": "Example Bank", "status": new_status,
                       "verification_ref": "approval:verify-1"},
            "event": {"event_type": "INSTITUTION_VERIFIED",
                      "resource_type": "institution",
                      "resource_id": "BANK-UG-01",
                      "from_status": "PENDING_VERIFICATION",
                      "to_status": new_status,
                      "actor_ref": "actor:registrar",
                      "authority_ref": "authority:verify",
                      "evidence_ref": "evidence:verification",
                      "approval_ref": "approval:verify-1",
                      "expected_version": "1.0"},
        }

    def test_record_and_event_are_atomic_idempotent_and_tenant_scoped(self):
        self.assertTrue(self.create()["created"])
        self.assertFalse(self.create()["created"])
        self.assertIsNotNone(self.other_worker.get_record(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01", version="1.0"))
        self.assertIsNone(self.other_worker.get_current_record(
            tenant_id="tenant-b", resource_type="institution",
            resource_id="BANK-UG-01"))
        self.assertEqual(len(self.registry.list_events(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01")), 1)
        with self.assertRaisesRegex(RegistryConflict, "EVENT_ID_REUSE_CONFLICT"):
            self.registry.create_record_with_event(
                tenant_id="tenant-a", resource_type="institution",
                resource_id="BANK-UG-01", version="1.0",
                status="PENDING_VERIFICATION",
                payload=self.record(legal_name="Altered Bank"),
                event_id="event-1", event_type="INSTITUTION_REGISTERED",
                actor_ref="actor:registrar", evidence_ref="evidence:incorporation",
                event_payload={"registration": "received"},
            )

    def test_lifecycle_snapshot_and_event_commit_together_and_retry(self):
        self.create()
        args = dict(tenant_id="tenant-a", resource_type="institution",
                    resource_id="BANK-UG-01", expected_version="1.0",
                    new_version="2.0", event_id="event-transition-1",
                    transition=self.transition())
        self.assertTrue(self.registry.transition_record(**args)["created"])
        self.assertFalse(self.other_worker.transition_record(**args)["created"])
        current = self.registry.get_current_record(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01")
        self.assertEqual((current["version"], current["status"]), ("2.0", "VERIFIED"))
        self.assertEqual(len(self.registry.list_events(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01")), 2)

    def test_concurrent_stale_transitions_are_serialized_to_one_winner(self):
        self.create()
        def attempt(index):
            new_version = f"v{index + 2}"
            transition = self.transition(new_version=new_version,
                                         event_id=f"event-race-{index}")
            transition["record"]["status"] = "VERIFIED"
            transition["event"]["to_status"] = "VERIFIED"
            return self.registry.transition_record(
                tenant_id="tenant-a", resource_type="institution",
                resource_id="BANK-UG-01", expected_version="1.0",
                new_version=new_version, event_id=f"event-race-{index}",
                transition=transition,
            )
        def safely_attempt(index):
            try:
                return attempt(index)["created"]
            except RegistryConflict as exc:
                if str(exc) != "STALE_EXPECTED_VERSION":
                    raise
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(safely_attempt, (0, 1)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 1)
        self.assertEqual(len(self.registry.list_records(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01")), 2)
        self.assertEqual(len(self.registry.list_events(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01")), 2)

    def test_failed_audit_insert_rolls_back_registration_snapshot(self):
        with self.connect() as connection:
            connection.execute("""
                CREATE FUNCTION reject_test_institutional_event()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'TEST_AUDIT_INSERT_REJECTED'; END;
                $$;
                CREATE TRIGGER reject_test_institutional_event
                BEFORE INSERT ON institutional_events
                FOR EACH ROW EXECUTE FUNCTION reject_test_institutional_event();
            """)
        with self.assertRaisesRegex(self.psycopg.Error, "TEST_AUDIT_INSERT_REJECTED"):
            self.create()
        self.assertIsNone(self.registry.get_current_record(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01"))

    def test_database_rejects_mutation_of_records_and_audit_events(self):
        self.create()
        with self.assertRaisesRegex(self.psycopg.Error, "KIN_SYS_INSTITUTIONAL_RECORD_APPEND_ONLY"):
            with self.connect() as connection:
                with connection.transaction():
                    connection.execute(
                        "SELECT set_config('kin_sys.tenant_id', %s, true)",
                        ("tenant-a",),
                    )
                    connection.execute(
                        """UPDATE institutional_records SET status='ALTERED'
                           WHERE tenant_id='tenant-a' AND resource_id='BANK-UG-01'"""
                    )
        with self.assertRaisesRegex(self.psycopg.Error, "KIN_SYS_INSTITUTIONAL_EVENT_APPEND_ONLY"):
            with self.connect() as connection:
                with connection.transaction():
                    connection.execute(
                        "SELECT set_config('kin_sys.tenant_id', %s, true)",
                        ("tenant-a",),
                    )
                    connection.execute(
                        "DELETE FROM institutional_events WHERE tenant_id='tenant-a'"
                    )

    def test_onboarding_administration_accepts_postgresql_registry(self):
        class Authority:
            def resolve(self, assertion, *, tenant_id, resource_type, resource_id):
                return {"authenticated": True, "verified": True,
                        "institution_id": "BANK-UG-01",
                        "actor_ref": "actor:registrar",
                        "authority_ref": "authority:registry",
                        "grant_ref": "grant:registrar",
                        "roles": ["institution_registrar"]}
        class Evidence:
            def verify(self, evidence_ref, *, context):
                import hashlib
                digest = hashlib.sha256(evidence_ref.encode()).hexdigest()
                return {"verified": True, "evidence_ref": evidence_ref,
                        "verification_ref": "verification:receipt",
                        "evidence_digest": "sha256:" + digest}
        admin = InstitutionalOnboardingAdministration(
            self.registry, Authority(), Evidence())
        result = admin.register(
            tenant_id="tenant-a", resource_type="institution", version="1.0",
            event_id="onboarding-1",
            record={"institution_id": "BANK-UG-01", "legal_name": "Example Bank",
                    "jurisdiction_ref": "UG", "status": "PENDING_VERIFICATION",
                    "verification_ref": None},
            actor_assertion={"trusted-test-assertion": True},
            evidence_ref="evidence:incorporation",
        )
        self.assertTrue(result["created"])
        stored = admin.get(tenant_id="tenant-a", resource_type="institution",
                           resource_id="BANK-UG-01")
        self.assertEqual(stored["status"], "PENDING_VERIFICATION")
        self.assertEqual(len(admin.events(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01")), 1)

    
    def test_domain_registry_rls_fails_closed_without_or_with_wrong_tenant_context(self):
        self.create()
        with self.connect() as connection:
            # An unset transaction-local tenant must see no institutional rows.
            rows = connection.execute(
                "SELECT tenant_id FROM institutional_records WHERE tenant_id='tenant-a'"
            ).fetchall()
            self.assertEqual(rows, [])

            # A different tenant cannot read or write the target tenant's rows.
            with connection.transaction():
                connection.execute(
                    "SELECT set_config('kin_sys.tenant_id', %s, true)",
                    ("tenant-b",),
                )
                rows = connection.execute(
                    "SELECT tenant_id FROM institutional_records WHERE tenant_id='tenant-a'"
                ).fetchall()
                self.assertEqual(rows, [])
                with self.assertRaises(self.psycopg.Error):
                    connection.execute(
                        """
                        INSERT INTO institutional_records
                            (tenant_id,resource_type,resource_id,version,status,
                             payload_json,payload_digest,created_at)
                        VALUES
                            ('tenant-a','institution','BANK-UG-02','1.0',
                             'PENDING_VERIFICATION','{}'::jsonb,%s,now())
                        """,
                        ("sha256:" + "a" * 64,),
                    )

    def test_domain_registry_rls_is_enabled_and_forced_on_both_tables(self):
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                       p.polname, pg_get_expr(p.polqual, p.polrelid),
                       pg_get_expr(p.polwithcheck, p.polrelid)
                  FROM pg_class c
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                  LEFT JOIN pg_policy p ON p.polrelid = c.oid
                 WHERE n.nspname = current_schema()
                   AND c.relname = ANY(%s)
                 ORDER BY c.relname
                """,
                (["institutional_records", "institutional_events"],),
            ).fetchall()
        self.assertEqual(len(rows), 2)
        for table, enabled, forced, policy, using, with_check in rows:
            with self.subTest(table=table):
                self.assertTrue(enabled)
                self.assertTrue(forced)
                self.assertEqual(policy, "kin_sys_tenant_isolation")
                self.assertIn("kin_sys.tenant_id", using or "")
                self.assertIn("kin_sys.tenant_id", with_check or "")


if __name__ == "__main__":
    unittest.main()
