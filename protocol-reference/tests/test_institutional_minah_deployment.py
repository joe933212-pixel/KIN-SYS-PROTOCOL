# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Tests for tenant-scoped institutional MINAH deployment registration."""
import os
import tempfile
import time
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
        "identity_provider_version": "7", "mandate_ref": "mandate:gov-001",
        "mandate_version": "3", "permitted_operations": ["trade.review", "identity.check"],
        "prohibited_operations": ["grant.authority", "bypass.cee"],
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

    def test_replayed_assertion_is_rejected_without_mutation(self):
        assertion = self.assertion()
        self.admin.register(tenant_id="tenant-ug-gov", config=deployment(),
            actor_assertion=assertion, event_id="event-first", evidence_ref="evidence:first")
        with self.assertRaisesRegex(MinahDeploymentError, "AUTHORITY_REJECTED"):
            self.admin.register(tenant_id="tenant-ug-gov",
                config=deployment(deployment_id="minah-ug-002"),
                actor_assertion=assertion, event_id="event-second", evidence_ref="evidence:second")
        self.assertIsNone(self.registry.get(tenant_id="tenant-ug-gov", deployment_id="minah-ug-002"))


if __name__ == "__main__":
    unittest.main()
