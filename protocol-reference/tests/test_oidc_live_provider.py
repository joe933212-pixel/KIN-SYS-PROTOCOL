# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Opt-in live OIDC integration against an institution-controlled public HTTPS issuer.

This suite is deliberately separate from deterministic unit tests. It exercises
real DNS, TLS, discovery, JWKS retrieval and signature verification. It never
contacts an issuer unless all required environment variables are supplied.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest

from institutional_identity import IdentityResolutionError, InstitutionalIdentityResolver
from institutional_identity_provider_registry import InstitutionalIdentityProviderRegistry
from oidc_institutional_adapter_factory import OIDCInstitutionalAdapterFactory
from oidc_discovery import OIDCDiscoveryJWKSLoader
from oidc_https_transport import OIDCHTTPSJSONTransport
from oidc_identity_adapter import OIDCIdentityProviderAdapter


_REQUIRED = (
    "KIN_SYS_OIDC_LIVE_ISSUER",
    "KIN_SYS_OIDC_LIVE_CLIENT_ID",
    "KIN_SYS_OIDC_LIVE_ALLOWED_HOSTS",
    "KIN_SYS_OIDC_LIVE_ID_TOKEN",
    "KIN_SYS_OIDC_LIVE_NONCE",
    "KIN_SYS_OIDC_LIVE_TENANT_ID",
)


@unittest.skipUnless(
    all(os.environ.get(name) for name in _REQUIRED),
    "live OIDC profile not configured; see docs/OIDC_LIVE_INTEGRATION.md",
)
class LiveOIDCProviderIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.issuer = os.environ["KIN_SYS_OIDC_LIVE_ISSUER"]
        cls.client_id = os.environ["KIN_SYS_OIDC_LIVE_CLIENT_ID"]
        cls.allowed_hosts = tuple(
            item.strip().lower()
            for item in os.environ["KIN_SYS_OIDC_LIVE_ALLOWED_HOSTS"].split(",")
            if item.strip()
        )
        if not cls.allowed_hosts:
            raise AssertionError("live OIDC host allowlist must not be empty")
        cls.tenant_id = os.environ["KIN_SYS_OIDC_LIVE_TENANT_ID"]
        cls.token = os.environ["KIN_SYS_OIDC_LIVE_ID_TOKEN"]
        cls.nonce = os.environ["KIN_SYS_OIDC_LIVE_NONCE"]

        transport = OIDCHTTPSJSONTransport(
            allowed_hosts=cls.allowed_hosts,
            connect_timeout_seconds=3,
            read_timeout_seconds=5,
            total_timeout_seconds=10,
        )
        loader = OIDCDiscoveryJWKSLoader(
            issuer=cls.issuer,
            allowed_hosts=cls.allowed_hosts,
            fetch_json=transport,
            cache_ttl_seconds=60,
            timeout_seconds=5,
        )
        cls.adapter = OIDCIdentityProviderAdapter(
            issuer=cls.issuer,
            client_id=cls.client_id,
            jwks_loader=loader,
        )

    def test_live_discovery_jwks_tls_and_signed_id_token(self):
        principal = self.adapter.verify_credential(
            self.token,
            tenant_id=self.tenant_id,
            expected_audience=self.client_id,
            expected_nonce=self.nonce,
        )
        self.assertEqual(principal.issuer, self.issuer)
        self.assertEqual(principal.tenant_id, self.tenant_id)
        self.assertTrue(principal.subject)

    def test_live_token_cannot_be_replayed_with_a_different_nonce(self):
        with self.assertRaises(IdentityResolutionError):
            self.adapter.verify_credential(
                self.token,
                tenant_id=self.tenant_id,
                expected_audience=self.client_id,
                expected_nonce=self.nonce + "-wrong",
            )

    def test_tampered_live_token_is_rejected(self):
        parts = self.token.split(".")
        self.assertEqual(len(parts), 3, "expected compact JWT ID token")
        replacement = ("A" if parts[1][0] != "A" else "B") + parts[1][1:]
        tampered = parts[0] + "." + replacement + "." + parts[2]
        with self.assertRaises(IdentityResolutionError):
            self.adapter.verify_credential(
                tampered,
                tenant_id=self.tenant_id,
                expected_audience=self.client_id,
                expected_nonce=self.nonce,
            )


    def test_live_provider_runs_through_tenant_registry_and_identity_resolver(self):
        """Exercise real provider IO behind the institution's active registry policy."""
        handle, database = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        registry = InstitutionalIdentityProviderRegistry(database)
        try:
            now = int(time.time())
            provider_config = {
                "tenant_id": self.tenant_id,
                "provider_id": "live-oidc-provider",
                "version": "1",
                "protocol": "OIDC",
                "issuer": self.issuer,
                "client_id": self.client_id,
                "status": "PENDING",
                "allowed_hosts": list(self.allowed_hosts),
                "allowed_algorithms": ["RS256", "ES256", "EdDSA"],
                "audiences": [self.client_id],
                "allowed_acr": ["urn:loa:2"],
                "required_amr": ["otp"],
                "valid_from": now - 60,
                "valid_until": now + 1800,
                "cache_ttl_seconds": 60,
            }
            registry.register_provider(
                tenant_id=self.tenant_id, config=provider_config,
                event_id="live-register-1", actor_ref="test-institution-admin",
                evidence_ref="controlled-live-test-approval",
            )
            registry.transition_provider(
                tenant_id=self.tenant_id, provider_id="live-oidc-provider",
                expected_version="1", new_version="2", new_status="ACTIVE",
                event_id="live-activate-1", actor_ref="test-institution-admin",
                evidence_ref="controlled-live-test-activation",
            )
            factory = OIDCInstitutionalAdapterFactory(registry=registry)
            consumed_nonces = set()

            def consume_nonce(tenant_id, nonce, *, expires_at):
                key = (tenant_id, nonce)
                if key in consumed_nonces or expires_at <= int(time.time()):
                    return False
                consumed_nonces.add(key)
                return True

            resolver = InstitutionalIdentityResolver(
                adapter_loader=factory,
                issuer_policy_loader=registry.resolve_active_issuer_policy,
                subject_binding_loader=lambda tenant, issuer, subject: {
                    "actor_ref": "actor:controlled-live-test",
                    "status": "ACTIVE",
                    "binding_version": "live-subject-binding-v1",
                } if tenant == self.tenant_id and issuer == self.issuer else None,
                consume_nonce=consume_nonce,
            )
            principal = resolver.authenticate(
                self.token, tenant_id=self.tenant_id,
                provider_id="live-oidc-provider",
                expected_audience=self.client_id,
                expected_nonce=self.nonce,
            )
            self.assertTrue(principal["authenticated"])
            self.assertEqual(principal["actor_ref"], "actor:controlled-live-test")
            self.assertEqual(principal["roles"], [])
            self.assertTrue(principal["authority_grant_required"])

            # A previously acquired adapter must stop authenticating immediately
            # after the institution suspends the provider under a new version.
            stale_adapter = factory(self.tenant_id, "live-oidc-provider")
            registry.transition_provider(
                tenant_id=self.tenant_id, provider_id="live-oidc-provider",
                expected_version="2", new_version="3", new_status="SUSPENDED",
                event_id="live-suspend-1", actor_ref="test-institution-admin",
                evidence_ref="controlled-live-test-suspension",
            )
            with self.assertRaisesRegex(
                IdentityResolutionError,
                "OIDC_PROVIDER_CONFIGURATION_NO_LONGER_ACTIVE",
            ):
                stale_adapter.verify_credential(
                    "not-a-token", tenant_id=self.tenant_id,
                    expected_audience=self.client_id,
                    expected_nonce="unused-suspension-check",
                )
        finally:
            registry.close()
            if os.path.exists(database):
                os.unlink(database)


if __name__ == "__main__":
    unittest.main()
