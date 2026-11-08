# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Tests for tenant-scoped institutional MINAH deployment registration."""
import json
import os
import subprocess
import tempfile
import time
import threading
import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from institutional_minah_deployment import (
    InstitutionalMinahDeploymentAdministration,
    InstitutionalMinahDeploymentRegistry,
    MinahDeploymentConflict,
    MinahDeploymentError,
)


class Authority:
    def __init__(self):
        self.roles = [
            "minah_deployment_registrar", "minah_deployment_configurator",
            "minah_deployment_approver", "minah_deployment_suspension_authority",
            "minah_deployment_revocation_authority",
        ]
        self.seen = set()

    def resolve(self, assertion, *, tenant_id, resource_type, resource_id):
        if assertion["nonce"] in self.seen:
            raise ValueError("replayed assertion")
        self.seen.add(assertion["nonce"])
        if resource_type != "minah_deployment":
            raise ValueError("wrong resource type")
        return {"authenticated": True, "actor_ref": "actor:institution-admin",
                "roles": list(self.roles)}


class Providers:
    def __init__(self):
        self.config = {
            "provider_id": "idp-ug", "version": "7", "status": "ACTIVE",
        }

    def get_provider(self, *, tenant_id, provider_id):
        if provider_id == self.config["provider_id"]:
            return dict(self.config)
        return None

    def resolve_active_provider(self, tenant_id, provider_id):
        provider = self.get_provider(tenant_id=tenant_id, provider_id=provider_id)
        if provider is None or provider["status"] != "ACTIVE":
            return None
        return provider


def deployment(**changes):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    value = {
        "deployment_id": "minah-ug-001", "version": "1", "status": "PENDING",
        "agent_kind": "INTERNAL", "agent_ref": "agent:ug-minah",
        "interface_version": "minah-review-0.1", "deployment_mode": "ON_PREMISES",
        "model_ref": "model:institution/minah-v1", "endpoint_ref": None,
        "secret_ref": "secret://institution/minah/credential",
        "owner_scope": "NATIONAL_SOVEREIGN", "owner_ref": "tenant-ug-gov",
        "jurisdictions": ["UG"], "identity_provider_id": "idp-ug",
        "identity_provider_version": "7", "jurisdiction_package_id": "UG-JURISDICTION",
        "jurisdiction_package_version": "4", "mandate_ref": "mandate:gov-001",
        "mandate_version": "3", "permitted_operations": ["trade.review", "identity.check"],
        "prohibited_operations": ["authority.grant", "bypass.cee"],
        "decision_boundaries": ["MINAH_RECOMMENDATION_ONLY", "CEE_FINAL_ADMISSION"],
        "policy_pack_version": "policy-4", "source_pack_version": "sources-2",
        "authority_profile_version": "authority-5", "created_at": now,
    }
    value.update(changes)
    return value


class InstitutionalMinahDeploymentTests(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.registry = InstitutionalMinahDeploymentRegistry(self.db)
        self.authority = Authority()
        self.providers = Providers()
        self.admin = InstitutionalMinahDeploymentAdministration(
            registry=self.registry, authority_resolver=self.authority,
            identity_provider_registry=self.providers)
        self.addCleanup(self.registry.close)
        self.addCleanup(lambda: os.path.exists(self.db) and os.unlink(self.db))
        self.counter = 0

    def assertion(self):
        self.counter += 1
        return {"nonce": "fresh-authority-nonce-" + str(self.counter)}

    def register(self, config=None):
        return self.admin.register(
            tenant_id="tenant-ug-gov", config=config or deployment(),
            actor_assertion=self.assertion(), event_id="event-register-1",
            evidence_ref="evidence:mandate-approval-1")

    def test_registration_pins_active_provider_and_records_actor_evidence(self):
        result = self.register()
        self.assertEqual(result["status"], "PENDING")
        stored = self.registry.get(tenant_id="tenant-ug-gov", deployment_id="minah-ug-001")
        self.assertEqual(stored["config"]["identity_provider_version"], "7")
        events = self.registry.list_events(tenant_id="tenant-ug-gov", deployment_id="minah-ug-001")
        self.assertEqual(events[0]["actor_ref"], "actor:institution-admin")
        self.assertEqual(events[0]["evidence_ref"], "evidence:mandate-approval-1")

    def test_inactive_or_wrong_version_identity_provider_blocks_registration(self):
        self.providers.config["status"] = "SUSPENDED"
        with self.assertRaisesRegex(MinahDeploymentError, "IDENTITY_PROVIDER_NOT_ACTIVE"):
            self.register()
        self.providers.config.update(status="ACTIVE", version="8")
        with self.assertRaisesRegex(MinahDeploymentError, "IDENTITY_PROVIDER_NOT_ACTIVE"):
            self.register()

    def test_tenant_spoofing_and_owner_mismatch_fail_closed(self):
        with self.assertRaisesRegex(MinahDeploymentError, "OWNER_TENANT_MISMATCH"):
            self.register(deployment(owner_ref="tenant-other"))

    def test_namespaced_model_reference_accepts_path_segments_and_rejects_unsafe_values(self):
        self.register(deployment(model_ref="model:institution/minah/v1"))
        stored = self.registry.get(tenant_id="tenant-ug-gov", deployment_id="minah-ug-001")
        self.assertEqual(stored["config"]["model_ref"], "model:institution/minah/v1")
        with self.assertRaisesRegex(MinahDeploymentError, "MINAH_MODEL_REFERENCE_INVALID"):
            self.register(deployment(model_ref="model:institution/minah v1"))

    def test_agent_descriptor_supports_hosted_agent_without_secret_material(self):
        result = self.register(deployment(
            agent_kind="THIRD_PARTY_HOSTED", endpoint_ref="https://agent.example.org/minah"))
        self.assertEqual(result["status"], "PENDING")

    def test_hosted_agent_requires_endpoint_reference(self):
        with self.assertRaisesRegex(MinahDeploymentError, "HOSTED_MINAH_ENDPOINT_REFERENCE_REQUIRED"):
            self.register(deployment(agent_kind="THIRD_PARTY_HOSTED"))

    def test_secret_value_is_not_accepted_instead_of_secret_reference(self):
        with self.assertRaisesRegex(MinahDeploymentError, "MINAH_SECRET_REFERENCE_REQUIRED"):
            self.register(deployment(secret_ref="actual-secret-value"))

    def test_minah_must_preserve_cee_final_admission_boundary(self):
        with self.assertRaisesRegex(MinahDeploymentError, "MUST_NOT_BYPASS_CEE"):
            self.register(deployment(prohibited_operations=["authority.grant"]))
        with self.assertRaisesRegex(MinahDeploymentError, "CEE_FINAL_ADMISSION_BOUNDARY_REQUIRED"):
            self.register(deployment(decision_boundaries=["MINAH_RECOMMENDATION_ONLY"]))

    def test_minah_cannot_grant_authority_or_conflicting_operations(self):
        with self.assertRaisesRegex(MinahDeploymentError, "OPERATION_ALLOW_DENY_CONFLICT"):
            self.register(deployment(prohibited_operations=["trade.review"]))

    def test_tenant_isolation_and_expected_version(self):
        self.register()
        self.assertIsNone(self.registry.get(tenant_id="tenant-other", deployment_id="minah-ug-001"))
        with self.assertRaisesRegex(MinahDeploymentConflict, "STALE_MINAH_DEPLOYMENT_VERSION"):
            self.registry.transition(
                tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
                expected_version="wrong", new_version="2", new_status="ACTIVE",
                actor_ref="actor:admin", event_id="event-stale", evidence_ref="evidence:1")

    def test_activation_rechecks_provider_and_revocation_is_terminal(self):
        self.register()
        active = self.admin.transition(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
            expected_version="1", new_version="2", new_status="ACTIVE",
            actor_assertion=self.assertion(), event_id="event-active",
            evidence_ref="evidence:activation")
        self.assertEqual(active["status"], "ACTIVE")
        revoked = self.admin.transition(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
            expected_version="2", new_version="3", new_status="REVOKED",
            actor_assertion=self.assertion(), event_id="event-revoke",
            evidence_ref="evidence:revocation")
        self.assertEqual(revoked["status"], "REVOKED")
        with self.assertRaisesRegex(MinahDeploymentError, "TRANSITION_NOT_ALLOWED"):
            self.registry.transition(
                tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
                expected_version="3", new_version="4", new_status="ACTIVE",
                actor_ref="actor:admin", event_id="event-reactivate",
                evidence_ref="evidence:reactivation")

    def test_lifecycle_epoch_fences_old_execution_lease_after_reactivation(self):
        self.register()
        self.admin.transition(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
            expected_version="1", new_version="2", new_status="ACTIVE",
            actor_assertion=self.assertion(), event_id="event-active-lease",
            evidence_ref="evidence:activation")
        lease = self.admin.acquire_execution_lease(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001")
        self.assertEqual(lease["version"], "2")
        self.assertEqual(self.admin.validate_execution_lease(lease=lease)["status"], "VALID")

        self.admin.transition(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
            expected_version="2", new_version="3", new_status="SUSPENDED",
            actor_assertion=self.assertion(), event_id="event-suspend-lease",
            evidence_ref="evidence:suspension")
        self.admin.transition(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
            expected_version="3", new_version="4", new_status="ACTIVE",
            actor_assertion=self.assertion(), event_id="event-reactivate-lease",
            evidence_ref="evidence:reactivation")
        with self.assertRaisesRegex(MinahDeploymentError, "EXECUTION_LEASE_FENCED"):
            self.admin.validate_execution_lease(lease=lease)

    def test_execution_lease_critical_section_runs_action_only_while_epoch_is_valid(self):
        self.register()
        self.admin.transition(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
            expected_version="1", new_version="2", new_status="ACTIVE",
            actor_assertion=self.assertion(), event_id="event-active-critical",
            evidence_ref="evidence:activation")
        lease = self.admin.acquire_execution_lease(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001")
        result = self.admin.execute_under_execution_lease(
            lease=lease, action=lambda: "committed-under-current-epoch")
        self.assertEqual(result, "committed-under-current-epoch")
        self.admin.release_execution_lease(lease=lease)
        with self.assertRaisesRegex(MinahDeploymentError, "EXECUTION_LEASE_RELEASED"):
            self.admin.validate_execution_lease(lease=lease)

    def test_independent_registry_connections_share_fencing_and_commit_serialization(self):
        # Two independently opened connections model separate worker processes
        # using the same durable SQLite file. This is not a networked database
        # or a claim of multi-region distributed coordination.
        second = InstitutionalMinahDeploymentRegistry(self.db)
        self.addCleanup(second.close)
        self.register()
        self.admin.transition(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
            expected_version="1", new_version="2", new_status="ACTIVE",
            actor_assertion=self.assertion(), event_id="event-active-cross-connection",
            evidence_ref="evidence:activation")
        lease = self.admin.acquire_execution_lease(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001")

        entered = threading.Event()
        release = threading.Event()
        outcomes = []

        def protected_commit():
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError("test did not release protected commit")
            return "committed"

        def execute():
            try:
                outcomes.append(("execute", second.execute_under_execution_lease(
                    lease=lease, action=protected_commit)))
            except Exception as exc:
                outcomes.append(("execute_error", str(exc)))

        def suspend():
            try:
                outcomes.append(("transition", self.registry.transition(
                    tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
                    expected_version="2", new_version="3", new_status="SUSPENDED",
                    actor_ref="actor:admin", event_id="event-suspend-cross-connection",
                    evidence_ref="evidence:suspension")))
            except Exception as exc:
                outcomes.append(("transition_error", str(exc)))

        execution_thread = threading.Thread(target=execute)
        transition_thread = threading.Thread(target=suspend)
        execution_thread.start()
        self.assertTrue(entered.wait(timeout=5))
        transition_thread.start()
        time.sleep(0.1)
        self.assertTrue(transition_thread.is_alive(),
                        "lifecycle transition must wait for protected commit transaction")
        release.set()
        execution_thread.join(timeout=5)
        transition_thread.join(timeout=5)
        self.assertFalse(execution_thread.is_alive())
        self.assertFalse(transition_thread.is_alive())
        self.assertIn(("execute", "committed"), outcomes)
        self.assertTrue(any(item[0] == "transition" for item in outcomes))
        with self.assertRaisesRegex(MinahDeploymentError, "EXECUTION_LEASE_FENCED"):
            second.validate_execution_lease(lease=lease)

    def test_lease_acquired_on_one_connection_is_fenced_by_transition_on_another(self):
        second = InstitutionalMinahDeploymentRegistry(self.db)
        self.addCleanup(second.close)
        self.register()
        self.admin.transition(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
            expected_version="1", new_version="2", new_status="ACTIVE",
            actor_assertion=self.assertion(), event_id="event-active-shared-epoch",
            evidence_ref="evidence:activation")
        lease = self.admin.acquire_execution_lease(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001")
        second.transition(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
            expected_version="2", new_version="3", new_status="SUSPENDED",
            actor_ref="actor:admin", event_id="event-suspend-shared-epoch",
            evidence_ref="evidence:suspension")
        with self.assertRaisesRegex(MinahDeploymentError, "EXECUTION_LEASE_FENCED"):
            self.admin.validate_execution_lease(lease=lease)
        invoked = []
        with self.assertRaisesRegex(MinahDeploymentError, "EXECUTION_LEASE_FENCED"):
            self.admin.execute_under_execution_lease(
                lease=lease, action=lambda: invoked.append("must-not-run"))
        self.assertEqual(invoked, [])

    def test_concurrent_lease_acquisition_serializes_fencing_sequence_across_connections(self):
        # Separate registry connections model independent workers contending
        # for one durable coordinator database. Each acquisition must commit
        # one unique fencing token; no connection-local counter is sufficient.
        second = InstitutionalMinahDeploymentRegistry(self.db)
        self.addCleanup(second.close)
        self.register()
        self.admin.transition(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
            expected_version="1", new_version="2", new_status="ACTIVE",
            actor_assertion=self.assertion(), event_id="event-active-concurrent-leases",
            evidence_ref="evidence:activation")

        worker_count = 12
        start = threading.Barrier(worker_count)
        leases = []
        failures = []
        result_lock = threading.Lock()

        def acquire(index):
            registry = self.registry if index % 2 == 0 else second
            try:
                start.wait(timeout=5)
                lease = registry.acquire_execution_lease(
                    tenant_id="tenant-ug-gov", deployment_id="minah-ug-001")
                with result_lock:
                    leases.append(lease)
            except Exception as exc:
                with result_lock:
                    failures.append(exc)

        workers = [
            threading.Thread(target=acquire, args=(index,))
            for index in range(worker_count)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)

        self.assertTrue(all(not worker.is_alive() for worker in workers),
                        "concurrent lease acquisition workers did not finish")
        self.assertEqual(failures, [])
        self.assertEqual(len(leases), worker_count)
        tokens = [lease["fencing_token"] for lease in leases]
        self.assertEqual(len(set(tokens)), worker_count)
        self.assertEqual(sorted(tokens), list(range(min(tokens), min(tokens) + worker_count)))
        for lease in leases:
            self.assertEqual(
                second.validate_execution_lease(lease=lease)["status"], "VALID")
            self.registry.release_execution_lease(lease=lease)

    def test_fencing_sequence_survives_suspend_and_reactivation_epoch_change(self):
        self.register()
        self.admin.transition(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
            expected_version="1", new_version="2", new_status="ACTIVE",
            actor_assertion=self.assertion(), event_id="event-active-reactivation",
            evidence_ref="evidence:activation")
        original = self.admin.acquire_execution_lease(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001")

        self.admin.transition(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
            expected_version="2", new_version="3", new_status="SUSPENDED",
            actor_assertion=self.assertion(), event_id="event-suspend-reactivation",
            evidence_ref="evidence:suspension")
        with self.assertRaisesRegex(MinahDeploymentError, "EXECUTION_LEASE_FENCED"):
            self.admin.validate_execution_lease(lease=original)

        self.admin.transition(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
            expected_version="3", new_version="4", new_status="ACTIVE",
            actor_assertion=self.assertion(), event_id="event-reactivate",
            evidence_ref="evidence:reactivation")
        renewed = self.admin.acquire_execution_lease(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001")

        self.assertGreater(renewed["execution_epoch"], original["execution_epoch"])
        self.assertGreater(renewed["fencing_token"], original["fencing_token"])
        self.assertEqual(
            self.admin.validate_execution_lease(lease=renewed)["status"], "VALID")
        with self.assertRaisesRegex(MinahDeploymentError, "EXECUTION_LEASE_FENCED"):
            self.admin.execute_under_execution_lease(
                lease=original, action=lambda: self.fail("stale lease executed"))

    def test_execution_lease_fencing_tokens_are_monotonic_and_tamper_evident(self):
        self.register()
        self.admin.transition(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
            expected_version="1", new_version="2", new_status="ACTIVE",
            actor_assertion=self.assertion(), event_id="event-active-sequence",
            evidence_ref="evidence:activation")
        first = self.admin.acquire_execution_lease(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001")
        second = self.admin.acquire_execution_lease(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001")
        self.assertGreater(second["fencing_token"], first["fencing_token"])
        self.assertEqual(
            self.admin.validate_execution_lease(lease=second)["fencing_token"],
            second["fencing_token"])
        tampered = dict(second, fencing_token=first["fencing_token"])
        with self.assertRaisesRegex(
                MinahDeploymentError, "EXECUTION_LEASE_FENCE_TOKEN_MISMATCH"):
            self.admin.validate_execution_lease(lease=tampered)

    def test_execution_lease_and_fencing_sequence_survive_registry_reopen(self):
        fd, database = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        first = InstitutionalMinahDeploymentRegistry(database)
        reopened = None
        try:
            admin = InstitutionalMinahDeploymentAdministration(
                registry=first, authority_resolver=self.authority,
                identity_provider_registry=self.providers)
            admin.register(
                tenant_id="tenant-ug-gov", config=deployment(),
                actor_assertion=self.assertion(), event_id="event-reopen-register",
                evidence_ref="evidence:reopen-registration")
            admin.transition(
                tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
                expected_version="1", new_version="2", new_status="ACTIVE",
                actor_assertion=self.assertion(), event_id="event-reopen-active",
                evidence_ref="evidence:reopen-activation")
            original = admin.acquire_execution_lease(
                tenant_id="tenant-ug-gov", deployment_id="minah-ug-001")
            first.close()

            # A fresh registry process must recover the persisted capability
            # and continue the same deployment-scoped monotonic fence sequence.
            reopened = InstitutionalMinahDeploymentRegistry(database)
            self.assertEqual(
                reopened.validate_execution_lease(lease=original)["status"], "VALID"
            )
            following = reopened.acquire_execution_lease(
                tenant_id="tenant-ug-gov", deployment_id="minah-ug-001")
            self.assertGreater(following["fencing_token"], original["fencing_token"])
            reopened.release_execution_lease(lease=original)
            with self.assertRaisesRegex(
                MinahDeploymentError, "MINAH_EXECUTION_LEASE_RELEASED"
            ):
                reopened.validate_execution_lease(lease=original)
            self.assertEqual(
                reopened.validate_execution_lease(lease=following)["status"], "VALID"
            )
        finally:
            if reopened is not None:
                reopened.close()
            try:
                first.close()
            except Exception:
                pass
            if os.path.exists(database):
                os.unlink(database)

    def test_execution_lease_rejects_boolean_epoch_and_fencing_claims(self):
        self.register()
        self.admin.transition(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
            expected_version="1", new_version="2", new_status="ACTIVE",
            actor_assertion=self.assertion(), event_id="event-active-boolean-claims",
            evidence_ref="evidence:activation")
        lease = self.admin.acquire_execution_lease(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001")
        # The fencing sequence begins at 1. Python's bool/int equality
        # would otherwise allow True to impersonate that fencing token; the
        # epoch is independently required to be a genuine positive integer.
        self.assertGreaterEqual(lease["execution_epoch"], 1)
        self.assertEqual(lease["fencing_token"], 1)
        for field in ("execution_epoch", "fencing_token"):
            with self.subTest(field=field):
                malformed = dict(lease, **{field: True})
                with self.assertRaisesRegex(
                    MinahDeploymentError, "MINAH_EXECUTION_LEASE_INVALID"
                ):
                    self.admin.validate_execution_lease(lease=malformed)

    def test_process_death_after_ivm_commit_recovers_by_durable_event_identity(self):
        """A split-store crash after IVM commit must recover without a second transition."""
        self.register()
        self.admin.transition(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
            expected_version="1", new_version="2", new_status="ACTIVE",
            actor_assertion=self.assertion(), event_id="event-active-cross-store",
            evidence_ref="evidence:activation")

        fd, ivm_db = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        marker = ivm_db + ".committed"
        self.addCleanup(lambda: os.path.exists(ivm_db) and os.unlink(ivm_db))
        self.addCleanup(lambda: os.path.exists(marker) and os.unlink(marker))

        from transition import ProtocolStore
        store = ProtocolStore(database=ivm_db)
        store.create_process(process_id="P-CROSS-STORE")
        store.close()

        lease = self.admin.acquire_execution_lease(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001")
        script = r"""
import json, sys, time
from institutional_minah_deployment import InstitutionalMinahDeploymentRegistry
from transition import ProtocolStore
registry_path, ivm_path, lease_json, marker = sys.argv[1:]
lease = json.loads(lease_json)
registry = InstitutionalMinahDeploymentRegistry(registry_path)
store = ProtocolStore(database=ivm_path)
def commit_ivm():
    decision = store.execute({
        "event_id": "E-CROSS-STORE-001",
        "process_id": "P-CROSS-STORE",
        "actor_id": "AUTHORITY-001",
        "event_type": "ACTIVATE",
        "rule_set": "LICENCE-RULES",
        "rule_version": "1.0",
        "execution_fence": {
            "tenant_id": lease["tenant_id"],
            "deployment_id": lease["deployment_id"],
            "execution_epoch": lease["execution_epoch"],
            "fencing_token": lease["fencing_token"],
        },
    })
    assert decision.status == "ACCEPTED"
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write("IVM_COMMITTED")
    while True:
        time.sleep(1)
registry.execute_under_execution_lease(lease=lease, action=commit_ivm)
"""
        child = subprocess.Popen(
            [sys.executable, "-c", script, self.db, ivm_db,
             json.dumps(lease), marker],
            cwd=str(ROOT),
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not os.path.exists(marker):
                if child.poll() is not None:
                    self.fail("worker exited before committing the IVM event")
                time.sleep(0.02)
            self.assertTrue(os.path.exists(marker), "worker did not reach post-commit crash point")
        finally:
            if child.poll() is None:
                try:
                    child.terminate()
                except ProcessLookupError:
                    pass
            child.wait(timeout=5)

        recovered_store = ProtocolStore(database=ivm_db)
        try:
            committed = recovered_store.event("E-CROSS-STORE-001")
            self.assertIsNotNone(committed)
            self.assertEqual(committed["outcome"], "ACCEPTED")
            self.assertEqual(recovered_store.process("P-CROSS-STORE")["state_version"], 1)

            # The registry transaction died before its acknowledgement/commit.
            # A fresh lease plus the same durable instruction identity must
            # return the IVM result, not apply a second state transition.
            retry_lease = self.admin.acquire_execution_lease(
                tenant_id="tenant-ug-gov", deployment_id="minah-ug-001")
            retry = recovered_store.execute({
                "event_id": "E-CROSS-STORE-001",
                "process_id": "P-CROSS-STORE",
                "actor_id": "AUTHORITY-001",
                "event_type": "ACTIVATE",
                "rule_set": "LICENCE-RULES",
                "rule_version": "1.0",
                "execution_fence": {
                    "tenant_id": retry_lease["tenant_id"],
                    "deployment_id": retry_lease["deployment_id"],
                    "execution_epoch": retry_lease["execution_epoch"],
                    "fencing_token": retry_lease["fencing_token"],
                },
            })
            self.assertTrue(retry.duplicate)
            self.assertEqual(retry.status, "ACCEPTED")
            self.assertEqual(recovered_store.process("P-CROSS-STORE")["state_version"], 1)
            self.assertEqual(len(recovered_store.history("P-CROSS-STORE")), 1)
            self.admin.release_execution_lease(lease=retry_lease)
        finally:
            recovered_store.close()

    def test_runtime_resolution_fails_closed_when_pinned_provider_is_suspended(self):
        self.register()
        self.admin.transition(tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
            expected_version="1", new_version="2", new_status="ACTIVE",
            actor_assertion=self.assertion(), event_id="event-active-2",
            evidence_ref="evidence:activation")
        self.assertIsNotNone(self.admin.resolve_active(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001"))
        self.providers.config["status"] = "SUSPENDED"
        self.assertIsNone(self.admin.resolve_active(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001"))

    def test_replayed_assertion_is_rejected_without_mutation(self):
        assertion = self.assertion()
        self.admin.register(tenant_id="tenant-ug-gov", config=deployment(),
            actor_assertion=assertion, event_id="event-first", evidence_ref="evidence:first")
        with self.assertRaisesRegex(MinahDeploymentError, "AUTHORITY_REJECTED"):
            self.admin.register(tenant_id="tenant-ug-gov",
                config=deployment(deployment_id="minah-ug-002"),
                actor_assertion=assertion, event_id="event-second", evidence_ref="evidence:second")
        self.assertIsNone(self.registry.get(tenant_id="tenant-ug-gov", deployment_id="minah-ug-002"))

    def test_sqlite_registry_structurally_satisfies_coordinator_contract(self):
        from institutional_execution_coordination import (
            InstitutionalExecutionCoordinator, execution_scope,
        )

        self.assertIsInstance(self.registry, InstitutionalExecutionCoordinator)
        self.register()
        self.admin.transition(tenant_id="tenant-ug-gov", deployment_id="minah-ug-001",
            expected_version="1", new_version="2", new_status="ACTIVE",
            actor_assertion=self.assertion(), event_id="event-active-contract",
            evidence_ref="evidence:activation")

        # Exercise the service through the provider-neutral contract rather
        # than depending on SQLite-specific internals.
        coordinator: InstitutionalExecutionCoordinator = self.registry
        lease = coordinator.acquire_execution_lease(
            tenant_id="tenant-ug-gov", deployment_id="minah-ug-001")
        self.assertEqual(execution_scope(lease).tenant_id, "tenant-ug-gov")
        self.assertEqual(execution_scope(lease).deployment_id, "minah-ug-001")
        self.assertEqual(coordinator.validate_execution_lease(lease=lease)["status"], "VALID")
        observed = []
        result = coordinator.execute_under_execution_lease(
            lease=lease, action=lambda: observed.append("guarded") or "committed")
        self.assertEqual(result, "committed")
        self.assertEqual(observed, ["guarded"])
        coordinator.release_execution_lease(lease=lease)
        with self.assertRaisesRegex(MinahDeploymentError, "LEASE_RELEASED"):
            coordinator.validate_execution_lease(lease=lease)

    def test_execution_scope_rejects_missing_or_empty_isolation_keys(self):
        from institutional_execution_coordination import execution_scope

        for claims in (None, {}, {"tenant_id": "tenant-a"},
                       {"tenant_id": "", "deployment_id": "minah-a"}):
            with self.subTest(claims=claims):
                with self.assertRaises(ValueError):
                    execution_scope(claims)


if __name__ == "__main__":
    unittest.main()
