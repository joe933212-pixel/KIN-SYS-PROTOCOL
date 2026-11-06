# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import os
import tempfile
import time
import unittest

from institutional_identity_provider_administration import (
    IdentityProviderAdministrationError,
    InstitutionalIdentityProviderAdministration,
)
from institutional_identity_provider_registry import InstitutionalIdentityProviderRegistry


class StubAuthorityResolver:
    def __init__(self, principal=None, error=None):
        self.principal = principal or {
            "authenticated": True, "actor_ref": "actor:verified-admin",
            "roles": ["identity_provider_registrar", "identity_provider_approver"],
        }
        self.error = error
        self.calls = []

    def resolve(self, assertion, *, tenant_id, resource_type, resource_id):
        self.calls.append((assertion, tenant_id, resource_type, resource_id))
        if self.error:
            raise self.error
        return self.principal


class IdentityProviderAdministrationTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.registry = InstitutionalIdentityProviderRegistry(self.path)
        self.addCleanup(self.registry.close)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))
        self.authority = StubAuthorityResolver()
        self.admin = InstitutionalIdentityProviderAdministration(
            registry=self.registry, authority_resolver=self.authority)
        now = int(time.time())
        self.config = {
            "tenant_id": "tenant-bank-ug", "provider_id": "national-idp",
            "version": "1", "protocol": "OIDC",
            "issuer": "https://identity.example.ug/tenant-a",
            "client_id": "kin-sys-api", "status": "PENDING",
            "allowed_hosts": ["identity.example.ug", "keys.example.ug"],
            "allowed_algorithms": ["RS256"], "audiences": ["kin-sys-api"],
            "allowed_acr": ["urn:loa:2"], "required_amr": ["otp"],
            "valid_from": now - 60, "valid_until": now + 3600,
            "cache_ttl_seconds": 120,
        }

    def register(self):
        return self.admin.register_provider(
            tenant_id="tenant-bank-ug", config=self.config,
            actor_assertion={"signed": "assertion"}, event_id="register-event-1",
            evidence_ref="approval-evidence-1")

    def test_register_uses_verified_actor_and_tenant_scoped_authority(self):
        self.register()
        call = self.authority.calls[-1]
        self.assertEqual(call[1:], (
            "tenant-bank-ug", "identity_provider", "national-idp"))
        events = self.registry.list_events(
            tenant_id="tenant-bank-ug", provider_id="national-idp")
        self.assertEqual(events[0]["actor_ref"], "actor:verified-admin")

    def test_missing_role_cannot_register_provider(self):
        self.authority.principal["roles"] = ["identity_provider_approver"]
        with self.assertRaisesRegex(
            IdentityProviderAdministrationError, "PROVIDER_ADMIN_ROLE_INSUFFICIENT"):
            self.register()
        self.assertIsNone(self.registry.get_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp"))

    def test_untrusted_assertion_fails_closed_without_registry_mutation(self):
        self.authority.error = ValueError("bad signature")
        with self.assertRaisesRegex(
            IdentityProviderAdministrationError, "PROVIDER_ADMIN_AUTHORITY_REJECTED"):
            self.register()
        self.assertIsNone(self.registry.get_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp"))

    def test_caller_cannot_choose_audit_actor(self):
        self.register()
        event = self.registry.list_events(
            tenant_id="tenant-bank-ug", provider_id="national-idp")[0]
        self.assertNotEqual(event["actor_ref"], "caller-supplied-admin")

    def test_activation_requires_distinct_approval_role(self):
        self.register()
        self.authority.principal["roles"] = ["identity_provider_registrar"]
        with self.assertRaisesRegex(
            IdentityProviderAdministrationError, "PROVIDER_ADMIN_ROLE_INSUFFICIENT"):
            self.admin.transition_provider(
                tenant_id="tenant-bank-ug", provider_id="national-idp",
                expected_version="1", new_version="2", new_status="ACTIVE",
                actor_assertion={"signed": "assertion"},
                event_id="activate-event-1", evidence_ref="approval-evidence-2")
        self.assertEqual(self.registry.get_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp")["status"], "PENDING")

    def test_approved_role_can_activate_and_suspension_requires_its_own_role(self):
        self.register()
        self.admin.transition_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp",
            expected_version="1", new_version="2", new_status="ACTIVE",
            actor_assertion={"signed": "assertion"},
            event_id="activate-event-1", evidence_ref="approval-evidence-2")
        self.authority.principal["roles"] = ["identity_provider_approver"]
        with self.assertRaisesRegex(
            IdentityProviderAdministrationError, "PROVIDER_ADMIN_ROLE_INSUFFICIENT"):
            self.admin.transition_provider(
                tenant_id="tenant-bank-ug", provider_id="national-idp",
                expected_version="2", new_version="3", new_status="SUSPENDED",
                actor_assertion={"signed": "assertion"},
                event_id="suspend-event-1", evidence_ref="suspension-evidence-1")

    def test_revocation_is_authority_gated_and_revoked_is_not_reactivatable(self):
        self.register()
        self.authority.principal["roles"] = ["identity_provider_revocation_authority"]
        self.admin.transition_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp",
            expected_version="1", new_version="2", new_status="REVOKED",
            actor_assertion={"signed": "assertion"},
            event_id="revoke-event-1", evidence_ref="revocation-evidence-1")
        self.authority.principal["roles"] = ["identity_provider_approver"]
        with self.assertRaisesRegex(
            IdentityProviderAdministrationError, "PROVIDER_ADMIN_ROLE_INSUFFICIENT"):
            self.admin.transition_provider(
                tenant_id="tenant-bank-ug", provider_id="national-idp",
                expected_version="2", new_version="3", new_status="ACTIVE",
                actor_assertion={"signed": "assertion"},
                event_id="reactivate-event-1", evidence_ref="approval-evidence-3")

    def test_revision_requires_configuration_role_and_starts_pending(self):
        self.register()
        self.authority.principal["roles"] = ["identity_provider_configurator"]
        revised = dict(self.config, version="2", status="PENDING")
        self.admin.revise_provider_config(
            tenant_id="tenant-bank-ug", provider_id="national-idp",
            expected_version="1", config=revised,
            actor_assertion={"signed": "assertion"},
            event_id="revise-event-1", evidence_ref="revision-evidence-1")
        self.assertEqual(self.registry.get_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp")["status"], "PENDING")

    def test_registration_cannot_smuggle_another_tenant(self):
        self.config["tenant_id"] = "tenant-other"
        with self.assertRaisesRegex(
            IdentityProviderAdministrationError, "PROVIDER_TENANT_CONTEXT_MISMATCH"):
            self.register()
        self.assertEqual(self.authority.calls, [])


if __name__ == "__main__":
    unittest.main()
