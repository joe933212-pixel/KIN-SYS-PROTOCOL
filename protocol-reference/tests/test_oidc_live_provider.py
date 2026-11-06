# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Opt-in live OIDC integration against an institution-controlled public HTTPS issuer.

This suite is deliberately separate from deterministic unit tests. It exercises
real DNS, TLS, discovery, JWKS retrieval and signature verification. It never
contacts an issuer unless all required environment variables are supplied.
"""
from __future__ import annotations

import os
import unittest

from institutional_identity import IdentityResolutionError
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


if __name__ == "__main__":
    unittest.main()
