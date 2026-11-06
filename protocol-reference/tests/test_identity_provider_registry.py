# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import os
import tempfile
import time
import unittest

from institutional_identity_provider_registry import (
    IdentityProviderRegistryConflict, IdentityProviderRegistryError,
    InstitutionalIdentityProviderRegistry,
)
from oidc_institutional_adapter_factory import OIDCInstitutionalAdapterFactory
from oidc_identity_adapter import OIDCIdentityProviderAdapter


class IdentityProviderRegistryTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.registry = InstitutionalIdentityProviderRegistry(self.path)
        self.addCleanup(self.registry.close)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))
        self.now = int(time.time())
        self.config = {
            "tenant_id": "tenant-bank-ug", "provider_id": "national-idp",
            "version": "1", "protocol": "OIDC",
            "issuer": "https://identity.example.ug/tenant-a",
            "client_id": "kin-sys-api", "status": "PENDING",
            "allowed_hosts": ["identity.example.ug", "keys.example.ug"],
            "allowed_algorithms": ["RS256"], "audiences": ["kin-sys-api"],
            "allowed_acr": ["urn:loa:2"], "required_amr": ["otp"],
            "valid_from": self.now - 60, "valid_until": self.now + 3600,
            "cache_ttl_seconds": 120,
        }

    def register(self):
        return self.registry.register_provider(
            tenant_id="tenant-bank-ug", config=self.config,
            event_id="event-register-1", actor_ref="actor-admin",
            evidence_ref="approval-ref-1",
        )

    def activate(self):
        self.register()
        return self.registry.transition_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp",
            expected_version="1", new_version="2", new_status="ACTIVE",
            event_id="event-activate-1", actor_ref="actor-admin",
            evidence_ref="approval-ref-2",
        )

    def test_registers_pending_provider_then_activates_new_immutable_version(self):
        self.assertTrue(self.register()["created"])
        self.assertEqual(self.registry.get_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp")["status"], "PENDING")
        self.registry.transition_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp",
            expected_version="1", new_version="2", new_status="ACTIVE",
            event_id="event-activate-1", actor_ref="actor-admin",
            evidence_ref="approval-ref-2",
        )
        current = self.registry.resolve_active_provider(
            "tenant-bank-ug", "national-idp", now=self.now
        )
        self.assertEqual(current["version"], "2")
        self.assertEqual(len(self.registry.list_events(
            tenant_id="tenant-bank-ug", provider_id="national-idp")), 2)

    def test_tenant_boundary_and_unknown_provider_fail_closed(self):
        self.register()
        self.assertIsNone(self.registry.get_provider(
            tenant_id="tenant-other", provider_id="national-idp"))
        self.assertIsNone(self.registry.resolve_active_provider(
            "tenant-bank-ug", "unknown-idp", now=self.now))

    def test_provider_cannot_be_activated_outside_its_validity_window(self):
        self.config["valid_from"] = self.now + 100
        self.register()
        self.registry.transition_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp",
            expected_version="1", new_version="2", new_status="ACTIVE",
            event_id="event-activate-1", actor_ref="actor-admin",
            evidence_ref="approval-ref-2",
        )
        self.assertIsNone(self.registry.resolve_active_provider(
            "tenant-bank-ug", "national-idp", now=self.now))

    def test_revoked_provider_is_terminal(self):
        self.activate()
        self.registry.transition_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp",
            expected_version="2", new_version="3", new_status="REVOKED",
            event_id="event-revoke-1", actor_ref="actor-admin",
            evidence_ref="approval-ref-3",
        )
        with self.assertRaisesRegex(IdentityProviderRegistryError,
                                    "PROVIDER_TRANSITION_NOT_ALLOWED"):
            self.registry.transition_provider(
                tenant_id="tenant-bank-ug", provider_id="national-idp",
                expected_version="3", new_version="4", new_status="ACTIVE",
                event_id="event-reactivate-1", actor_ref="actor-admin",
                evidence_ref="approval-ref-4",
            )
        self.assertIsNone(self.registry.resolve_active_provider(
            "tenant-bank-ug", "national-idp", now=self.now))

    def test_stale_version_cannot_change_provider_status(self):
        self.activate()
        with self.assertRaisesRegex(IdentityProviderRegistryConflict,
                                    "STALE_PROVIDER_VERSION"):
            self.registry.transition_provider(
                tenant_id="tenant-bank-ug", provider_id="national-idp",
                expected_version="1", new_version="3", new_status="SUSPENDED",
                event_id="event-suspend-1", actor_ref="actor-admin",
                evidence_ref="approval-ref-3",
            )

    def test_invalid_host_algorithm_and_audience_policies_are_rejected(self):
        for field, value in (
            ("allowed_hosts", ["attacker.example"]),
            ("allowed_algorithms", ["none"]),
            ("audiences", ["other-client"]),
        ):
            config = dict(self.config)
            config[field] = value
            with self.subTest(field=field):
                with self.assertRaises(IdentityProviderRegistryError):
                    self.registry.register_provider(
                        tenant_id="tenant-bank-ug", config=config,
                        event_id="event-" + field, actor_ref="actor-admin",
                        evidence_ref="approval-ref",
                    )

    def test_provider_configuration_cannot_contain_secret_fields(self):
        config = dict(self.config)
        config["client_secret"] = "do-not-store"
        with self.assertRaisesRegex(IdentityProviderRegistryError,
                                    "PROVIDER_CONFIG_FIELDS_INVALID"):
            self.registry.register_provider(
                tenant_id="tenant-bank-ug", config=config,
                event_id="event-secret", actor_ref="actor-admin",
                evidence_ref="approval-ref",
            )

    def test_adapter_factory_uses_only_active_tenant_configuration(self):
        self.activate()
        factory = OIDCInstitutionalAdapterFactory(
            registry=self.registry, clock=lambda: self.now
        )
        adapter = factory("tenant-bank-ug", "national-idp")
        self.assertIsInstance(adapter, OIDCIdentityProviderAdapter)
        self.assertIsNone(factory("tenant-other", "national-idp"))
        self.assertIsNone(factory("tenant-bank-ug", "unknown-idp"))

    def test_suspension_invalidates_runtime_selection(self):
        self.activate()
        factory = OIDCInstitutionalAdapterFactory(
            registry=self.registry, clock=lambda: self.now
        )
        self.assertIsNotNone(factory("tenant-bank-ug", "national-idp"))
        self.registry.transition_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp",
            expected_version="2", new_version="3", new_status="SUSPENDED",
            event_id="event-suspend-1", actor_ref="actor-admin",
            evidence_ref="approval-ref-3",
        )
        self.assertIsNone(factory("tenant-bank-ug", "national-idp"))


    def test_provider_config_revision_moves_head_to_pending_until_reapproved(self):
        self.activate()
        revised = dict(self.config)
        revised["version"] = "3"
        revised["status"] = "PENDING"
        revised["allowed_algorithms"] = ["RS256", "ES256"]
        self.registry.revise_provider_config(
            tenant_id="tenant-bank-ug", provider_id="national-idp",
            expected_version="2", config=revised, event_id="event-revise-1",
            actor_ref="actor-admin", evidence_ref="approval-ref-3",
        )
        self.assertIsNone(self.registry.resolve_active_provider(
            "tenant-bank-ug", "national-idp", now=self.now))
        self.assertEqual(self.registry.get_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp")["version"], "3")
        self.registry.transition_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp",
            expected_version="3", new_version="4", new_status="ACTIVE",
            event_id="event-reactivate-1", actor_ref="actor-admin",
            evidence_ref="approval-ref-4",
        )
        active = self.registry.resolve_active_provider(
            "tenant-bank-ug", "national-idp", now=self.now)
        self.assertEqual(active["allowed_algorithms"], ["RS256", "ES256"])

    def test_issuer_policy_resolution_is_tenant_scoped_and_unique(self):
        self.activate()
        policy = self.registry.resolve_active_issuer_policy(
            "tenant-bank-ug", self.config["issuer"], now=self.now)
        self.assertEqual(policy["audiences"], ["kin-sys-api"])
        self.assertEqual(policy["required_amr"], ["otp"])
        self.assertIsNone(self.registry.resolve_active_issuer_policy(
            "tenant-other", self.config["issuer"], now=self.now))
        self.assertIsNone(self.registry.resolve_active_issuer_policy(
            "tenant-bank-ug", "https://other.example", now=self.now))



    def test_issuer_policy_lookup_fails_closed_on_ambiguous_active_issuer(self):
        self.activate()
        second = dict(self.config)
        second.update({"provider_id": "backup-idp", "version": "1"})
        self.registry.register_provider(
            tenant_id="tenant-bank-ug", config=second,
            event_id="event-register-backup", actor_ref="actor-admin",
            evidence_ref="approval-backup-1",
        )
        self.registry.transition_provider(
            tenant_id="tenant-bank-ug", provider_id="backup-idp",
            expected_version="1", new_version="2", new_status="ACTIVE",
            event_id="event-activate-backup", actor_ref="actor-admin",
            evidence_ref="approval-backup-2",
        )
        self.assertIsNone(self.registry.resolve_active_issuer_policy(
            "tenant-bank-ug", self.config["issuer"], now=self.now))

    def test_malformed_policy_collection_types_are_rejected_cleanly(self):
        for field, value in (
            ("allowed_algorithms", [["RS256"]]),
            ("required_amr", [["otp"]]),
            ("status", ["ACTIVE"]),
            ("allowed_hosts", ["identity.example.ug", []]),
        ):
            config = dict(self.config)
            config[field] = value
            with self.subTest(field=field), self.assertRaises(IdentityProviderRegistryError):
                self.registry.register_provider(
                    tenant_id="tenant-bank-ug", config=config,
                    event_id="event-malformed-" + field, actor_ref="actor-admin",
                    evidence_ref="approval-malformed",
                )

    def test_provider_revision_requires_new_version_and_fresh_approval(self):
        self.activate()
        revised = dict(self.config)
        revised.update({"version": "2", "status": "PENDING"})
        with self.assertRaises(IdentityProviderRegistryError):
            self.registry.revise_provider_config(
                tenant_id="tenant-bank-ug", provider_id="national-idp",
                expected_version="2", config=revised,
                event_id="event-invalid-revision", actor_ref="actor-admin",
                evidence_ref="approval-ref",
            )



if __name__ == "__main__":
    unittest.main()
