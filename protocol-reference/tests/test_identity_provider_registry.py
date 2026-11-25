# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from institutional_identity import IdentityResolutionError, InstitutionalIdentityResolver
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
        self.assertIsInstance(adapter._delegate, OIDCIdentityProviderAdapter)
        self.assertEqual(adapter._delegate.issuer, self.config["issuer"])
        self.assertEqual(adapter._delegate.client_id, self.config["client_id"])
        self.assertEqual(adapter._delegate._algorithms, ("RS256",))
        self.assertIsNone(factory("tenant-other", "national-idp"))
        self.assertIsNone(factory("tenant-bank-ug", "unknown-idp"))

    def test_suspension_invalidates_runtime_selection(self):
        self.activate()
        factory = OIDCInstitutionalAdapterFactory(
            registry=self.registry, clock=lambda: self.now
        )
        adapter = factory("tenant-bank-ug", "national-idp")
        self.assertIsNotNone(adapter)
        self.registry.transition_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp",
            expected_version="2", new_version="3", new_status="SUSPENDED",
            event_id="event-suspend-1", actor_ref="actor-admin",
            evidence_ref="approval-ref-3",
        )
        with self.assertRaisesRegex(IdentityResolutionError,
                                    "OIDC_PROVIDER_CONFIGURATION_NO_LONGER_ACTIVE"):
            adapter.verify_credential(
                "invalid-token", tenant_id="tenant-bank-ug",
                expected_audience="kin-sys-api", expected_nonce="nonce",
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



    def test_factory_evicts_old_adapter_after_config_version_changes(self):
        self.activate()
        factory = OIDCInstitutionalAdapterFactory(
            registry=self.registry, clock=lambda: self.now
        )
        first = factory("tenant-bank-ug", "national-idp")
        revised = dict(self.config)
        revised.update({"version": "3", "status": "PENDING",
                        "allowed_algorithms": ["RS256", "ES256"]})
        self.registry.revise_provider_config(
            tenant_id="tenant-bank-ug", provider_id="national-idp",
            expected_version="2", config=revised, event_id="event-revise-factory",
            actor_ref="actor-admin", evidence_ref="approval-ref-3",
        )
        self.assertIsNone(factory("tenant-bank-ug", "national-idp"))
        with self.assertRaisesRegex(IdentityResolutionError,
                                    "OIDC_PROVIDER_CONFIGURATION_NO_LONGER_ACTIVE"):
            first.verify_credential(
                "invalid-token", tenant_id="tenant-bank-ug",
                expected_audience="kin-sys-api", expected_nonce="nonce",
            )
        self.registry.transition_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp",
            expected_version="3", new_version="4", new_status="ACTIVE",
            event_id="event-reactivate-factory", actor_ref="actor-admin",
            evidence_ref="approval-ref-4",
        )
        second = factory("tenant-bank-ug", "national-idp")
        self.assertIsNot(first, second)
        self.assertEqual(second._delegate._algorithms, ("RS256", "ES256"))


    def test_registry_factory_discovery_and_signed_token_end_to_end(self):
        self.activate()
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
        jwk.update({"kid": "provider-key-1", "use": "sig", "alg": "RS256"})
        issuer = self.config["issuer"]
        jwks_uri = "https://keys.example.ug/tenant-a/jwks"
        documents = {
            issuer + "/.well-known/openid-configuration": {
                "issuer": issuer, "jwks_uri": jwks_uri,
                "id_token_signing_alg_values_supported": ["RS256"],
            },
            jwks_uri: {"keys": [jwk]},
        }
        class FakeJSONTransport:
            def __init__(self, allowed_hosts):
                self.allowed_hosts = allowed_hosts
                self.requests = []
            def __call__(self, url, *, timeout_seconds, max_bytes):
                self.requests.append((url, timeout_seconds, max_bytes))
                return documents[url]
        transports = []
        def transport_factory(*, allowed_hosts):
            value = FakeJSONTransport(allowed_hosts)
            transports.append(value)
            return value
        factory = OIDCInstitutionalAdapterFactory(
            registry=self.registry, clock=lambda: self.now
        )
        with patch("oidc_institutional_adapter_factory.OIDCHTTPSJSONTransport",
                   side_effect=transport_factory):
            adapter = factory("tenant-bank-ug", "national-idp")
            claims = {
                "iss": issuer, "sub": "opaque-subject-01",
                "aud": self.config["client_id"],
                "iat": self.now - 10, "exp": self.now + 180,
                "auth_time": self.now - 15, "nonce": "nonce-live-flow-0001",
                "acr": "urn:loa:2", "amr": ["pwd", "otp"],
            }
            token = jwt.encode(
                claims, private_key, algorithm="RS256",
                headers={"kid": "provider-key-1"},
            )
            identity_resolver = InstitutionalIdentityResolver(
                adapter_loader=factory,
                issuer_policy_loader=self.registry.resolve_active_issuer_policy,
                subject_binding_loader=lambda tenant, iss, subject: {
                    "actor_ref": "actor:alice", "status": "ACTIVE",
                    "binding_version": "subject-binding-v1",
                } if tenant == "tenant-bank-ug" and subject == "opaque-subject-01" else None,
                consume_nonce=lambda tenant, nonce, *, expires_at: True,
                clock=lambda: self.now,
            )
            principal = identity_resolver.authenticate(
                token, tenant_id="tenant-bank-ug", provider_id="national-idp",
                expected_audience=self.config["client_id"],
                expected_nonce="nonce-live-flow-0001",
            )
        self.assertTrue(principal["authenticated"])
        self.assertEqual(principal["actor_ref"], "actor:alice")
        self.assertEqual(principal["identity_issuer"], issuer)
        self.assertEqual(principal["identity_subject"], "opaque-subject-01")
        self.assertEqual(principal["identity_binding_version"], "subject-binding-v1")
        self.assertTrue(principal["authority_grant_required"])
        self.assertEqual(len(transports[0].requests), 2)
        self.assertEqual(
            transports[0].allowed_hosts,
            ("identity.example.ug", "keys.example.ug"),
        )



if __name__ == "__main__":
    unittest.main()
