# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""PostgreSQL integration tests for durable federation inbox transitions."""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DSN = os.environ.get("KIN_SYS_POSTGRES_TEST_DSN")
from postgresql_migrations import apply_migrations
from postgresql_federation_inbox import (
    FederationInboxError,
    PostgreSQLFederationInbox,
)


@unittest.skipUnless(DSN, "KIN_SYS_POSTGRES_TEST_DSN is not configured")
class PostgreSQLFederationInboxIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        cls.psycopg, cls.sql = psycopg, sql

    def setUp(self):
        self.schema = "kin_sys_inbox_" + uuid.uuid4().hex
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
        self.inbox_a = PostgreSQLFederationInbox(self.connect)
        self.inbox_b = PostgreSQLFederationInbox(self.connect)
        self.message = {
            "claims": {
                "message_id": "MSG-UG-RW-001",
                "operation_id": "OP-UG-RW-001",
                "issuer_installation_id": "INSTALL-UG-01",
            },
            "signature": "synthetic-test-envelope",
        }
    def tearDown(self):
        self.admin.execute(
            self.sql.SQL("DROP SCHEMA {} CASCADE").format(self.sql.Identifier(self.schema))
        )
        self.admin.close()

    def accept(self, envelope=None):
        return self.inbox_a.accept_verified(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id="MSG-UG-RW-001",
            operation_id="OP-UG-RW-001",
            idempotency_key="IDEM-UG-RW-001",
            envelope=self.message if envelope is None else envelope,
        )

    def claim(self, worker):
        self.accept()
        return self.inbox_a.claim(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id="MSG-UG-RW-001",
            worker_id=worker,
            lease_seconds=30,
        )


    def accept_authenticated(self, *, message_id="MSG-UG-RW-001",
                              operation_id="OP-UG-RW-001",
                              idempotency_key="IDEM-UG-RW-001",
                              nonce="NONCE-UG-RW-001", envelope=None):
        return self.inbox_a.accept_authenticated(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id=message_id,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            nonce=nonce,
            expires_at=1_800_000_120,
            envelope=self.message if envelope is None else envelope,
            clock=lambda: 1_800_000_000,
        )

    def test_authenticated_acceptance_atomically_persists_inbox_and_nonce(self):
        result = self.accept_authenticated()
        self.assertTrue(result["inserted"])
        self.assertEqual(result["status"], "RECEIVED")
        duplicate = self.accept_authenticated()
        self.assertFalse(duplicate["inserted"])
        with self.connect() as connection:
            inbox_count = connection.execute(
                "SELECT count(*) FROM federation_inbox"
            ).fetchone()[0]
            nonce_count = connection.execute(
                "SELECT count(*) FROM consumed_federation_nonces"
            ).fetchone()[0]
        self.assertEqual(inbox_count, 1)
        self.assertEqual(nonce_count, 1)

    def test_replay_conflict_rolls_back_new_inbox_row(self):
        self.accept_authenticated()
        with self.assertRaisesRegex(FederationInboxError, "MESSAGE_REPLAYED"):
            self.accept_authenticated(
                message_id="MSG-UG-RW-002",
                operation_id="OP-UG-RW-002",
                idempotency_key="IDEM-UG-RW-002",
            )
        self.assertIsNone(self.inbox_a.get(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id="MSG-UG-RW-002",
        ))

    def test_accept_is_idempotent_for_identical_verified_delivery(self):
        first = self.accept()
        second = self.accept()
        self.assertTrue(first["inserted"])
        self.assertFalse(second["inserted"])
        self.assertEqual(second["status"], "RECEIVED")

    def test_message_or_idempotency_reuse_with_different_content_is_rejected(self):
        self.accept()
        altered = {**self.message, "claims": {**self.message["claims"], "operation_id": "OP-OTHER"}}
        with self.assertRaisesRegex(FederationInboxError, "IDEMPOTENCY_CONFLICT"):
            self.accept(altered)

    def test_only_one_concurrent_worker_can_claim_a_delivery(self):
        with ThreadPoolExecutor(max_workers=10) as pool:
            claims = list(pool.map(self.claim, [f"worker-{i}" for i in range(10)]))
        self.assertEqual(sum(item is not None for item in claims), 1)

    def test_only_current_lease_owner_can_complete(self):
        claim = self.claim("worker-a")
        self.assertIsNotNone(claim)
        wrong_owner = self.inbox_b.complete(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id="MSG-UG-RW-001",
            worker_id="worker-b",
            decision_ref="DECISION-RW-001",
            response={"protocol_decision": "PASSED"},
        )
        self.assertFalse(wrong_owner)
        completed = self.inbox_a.complete(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id="MSG-UG-RW-001",
            worker_id="worker-a",
            decision_ref="DECISION-RW-001",
            response={"protocol_decision": "PASSED"},
        )
        self.assertTrue(completed)
        stored = self.inbox_b.get(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id="MSG-UG-RW-001",
        )
        self.assertEqual(stored["status"], "COMPLETED")
        self.assertEqual(stored["receiver_decision_ref"], "DECISION-RW-001")
        self.assertEqual(stored["response"], {"protocol_decision": "PASSED"})
        self.assertIsNone(self.claim("worker-c"))

    def test_retryable_failure_can_be_reclaimed(self):
        self.assertIsNotNone(self.claim("worker-a"))
        self.assertTrue(self.inbox_a.fail(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id="MSG-UG-RW-001",
            worker_id="worker-a",
            error_code="RECEIVER_TEMPORARILY_UNAVAILABLE",
            retryable=True,
        ))
        retry = self.claim("worker-b")
        self.assertIsNotNone(retry)
        self.assertEqual(retry["attempt_count"], 2)

    def test_expired_lease_can_be_reclaimed_by_another_worker(self):
        self.accept()
        first = self.inbox_a.claim(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id="MSG-UG-RW-001",
            worker_id="worker-a",
            lease_seconds=1,
        )
        self.assertIsNotNone(first)
        import time
        time.sleep(1.1)
        second = self.claim("worker-b")
        self.assertIsNotNone(second)
        self.assertEqual(second["attempt_count"], 2)

    def test_process_restart_recovers_committed_cee_ivm_result_before_inbox_completion(self):
        """Recover the receiver's committed IVM event after process restart."""
        from governed_contracts import compute_payload_digest
        from institutional_instruction_execution import (
            InstitutionalInstructionExecutionError,
            InstitutionalInstructionExecutionService,
        )
        from protocol_pipeline import KinSysProtocolPipeline
        from test_institutional_instruction_execution import (
            ActiveMinahResolver, RuntimeResolver, instruction,
        )

        contract = instruction()
        contract["payload_digest"] = compute_payload_digest(contract)
        evidence = contract["evidence"]
        event_id = "EV-" + hashlib.sha256(
            contract["idempotency_key"].encode("utf-8")
        ).hexdigest()[:24]

        self.inbox_a.accept_verified(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id="MSG-UG-RW-001",
            operation_id=contract["operation_id"],
            idempotency_key=contract["idempotency_key"],
            envelope={
                "claims": {
                    "message_id": "MSG-UG-RW-001",
                    "operation_id": contract["operation_id"],
                    "issuer_installation_id": "INSTALL-UG-01",
                },
                "signature": "synthetic-process-restart-test-envelope",
            },
        )
        first_claim = self.inbox_a.claim(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id="MSG-UG-RW-001",
            worker_id="worker-before-crash",
            lease_seconds=1,
        )
        self.assertIsNotNone(first_claim)

        resolver = RuntimeResolver()
        profile = {
            "profile_version": "kin-sys-minah-profile-0.1",
            "deployment_id": "MINAH-UG-01",
            "owner_scope": "INSTITUTION",
            "owner_ref": "tenant-ug",
            "use_case": "POSTGRES_FEDERATION_CRASH_RECOVERY_TEST",
            "jurisdictions": ["UG"],
            "policy_pack_version": "BANK-RULES:1.0",
            "source_pack_version": "UG-SOURCES:2.0",
            "model_ref": "model:bank/minah-v1",
            "deployment_mode": "SELF_HOSTED",
            "may_authorise": False,
        }
        resolver.context["minah_config"] = {
            "deployment_id": "MINAH-UG-01",
            "owner_ref": "tenant-ug",
            "model_ref": "model:bank/minah-v1",
            "jurisdictions": ["UG"],
            "policy_pack_version": "BANK-RULES:1.0",
            "source_pack_version": "UG-SOURCES:2.0",
        }
        active_minah = ActiveMinahResolver({
            "tenant_id": "tenant-ug",
            "deployment_id": "MINAH-UG-01",
            "version": "9.0",
            "status": "ACTIVE",
            "config": dict(profile),
        })

        with tempfile.TemporaryDirectory(prefix="kin-sys-federation-recovery-") as directory:
            cee_db = str(Path(directory) / "receiver-cee.sqlite")
            ivm_db = str(Path(directory) / "receiver-ivm.sqlite")

            def new_pipeline():
                return KinSysProtocolPipeline(
                    cee_database=cee_db,
                    ivm_database=ivm_db,
                    minah_profile=profile,
                    identity_provider_id="IDP-UG-01",
                    identity_provider_version="2.0",
                    jurisdiction_package_id="JUR-UG-01",
                    jurisdiction_package_version="4.0",
                    authority_profile_version="BANK-AUTHORITY:1.0",
                )

            first_pipeline = new_pipeline()
            first_service = InstitutionalInstructionExecutionService(
                runtime_binding_resolver=resolver,
                pipeline_factory=lambda runtime_context: first_pipeline,
                minah_deployment_resolver=active_minah,
            )
            original_execute = first_pipeline.execute
            injected = {"crashed": False}

            def commit_then_crash(request, *, execution_guard=None):
                if not injected["crashed"]:
                    injected["crashed"] = True
                    return original_execute(
                        request, fail_after_ivm_commit=True,
                        execution_guard=execution_guard,
                    )
                return original_execute(request, execution_guard=execution_guard)

            first_pipeline.execute = commit_then_crash
            try:
                with self.assertRaises(InstitutionalInstructionExecutionError):
                    first_service.execute(
                        tenant_id="tenant-ug",
                        institution_id="BANK-UG-01",
                        binding_id="BIND-UG-01",
                        instruction=contract,
                        evidence=evidence,
                    )
                committed_event = first_pipeline.ivm.event(event_id)
                self.assertIsNotNone(committed_event)
                self.assertEqual(committed_event["outcome"], "ACCEPTED")
                self.assertEqual(
                    first_pipeline.cee.get(contract["operation_id"])["phase"],
                    "COMMITTING",
                )
            finally:
                # Closing both adapters models loss of all process-local caches.
                first_service.close()

            time.sleep(1.1)
            second_claim = self.inbox_b.claim(
                receiver_tenant_id="tenant-rw",
                issuer_installation_id="INSTALL-UG-01",
                message_id="MSG-UG-RW-001",
                worker_id="worker-after-restart",
                lease_seconds=30,
            )
            self.assertIsNotNone(second_claim)
            self.assertEqual(second_claim["attempt_count"], 2)

            restarted_pipeline = new_pipeline()
            second_service = InstitutionalInstructionExecutionService(
                runtime_binding_resolver=resolver,
                pipeline_factory=lambda runtime_context: restarted_pipeline,
                operation_pipeline_resolver=lambda **kwargs: restarted_pipeline,
                minah_deployment_resolver=active_minah,
            )
            try:
                recovered = second_service.recover(
                    tenant_id="tenant-ug",
                    institution_id="BANK-UG-01",
                    operation_id=contract["operation_id"],
                    request_id="federation-restart-recovery",
                    instruction=contract,
                )
                self.assertEqual(
                    recovered["decision"]["protocol_decision"], "PASSED"
                )
                self.assertTrue(recovered["result"]["duplicate"])
                self.assertEqual(
                    restarted_pipeline.cee.get(contract["operation_id"])["phase"],
                    "FINALIZED",
                )
                self.assertEqual(
                    restarted_pipeline.ivm.event(event_id)["outcome"], "ACCEPTED"
                )
                history = restarted_pipeline.ivm.history(contract["target_ref"])
                self.assertEqual(
                    [item["event_id"] for item in history], [event_id]
                )

                response = {
                    "decision": recovered["decision"],
                    "receiver_runtime_pins": recovered["runtime_pins"],
                    "receiver_result": recovered["result"],
                }
                self.assertTrue(self.inbox_b.complete(
                    receiver_tenant_id="tenant-rw",
                    issuer_installation_id="INSTALL-UG-01",
                    message_id="MSG-UG-RW-001",
                    worker_id="worker-after-restart",
                    decision_ref="cee-operation:" + contract["operation_id"],
                    response=response,
                ))
                stored = self.inbox_a.get(
                    receiver_tenant_id="tenant-rw",
                    issuer_installation_id="INSTALL-UG-01",
                    message_id="MSG-UG-RW-001",
                )
                self.assertEqual(stored["status"], "COMPLETED")
                self.assertEqual(stored["response"], response)
            finally:
                second_service.close()



if __name__ == "__main__":
    unittest.main()
