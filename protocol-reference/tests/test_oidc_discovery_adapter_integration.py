# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import json
import time
import unittest

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from institutional_identity import IdentityResolutionError
from oidc_discovery import OIDCDiscoveryJWKSLoader
from oidc_identity_adapter import OIDCIdentityProviderAdapter


ISSUER = "https://identity.example.ug/tenant-a"
JWKS_URI = "https://keys.example.ug/tenant-a/jwks"
CLIENT = "kin-sys-api"


class OIDCDiscoveryAdapterIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.now = int(time.time())
        self.private_one = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.private_two = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.active_jwks = [self.public_jwk(self.private_one, "key-one")]
        self.calls = []
        self.metadata = {
            "issuer": ISSUER, "jwks_uri": JWKS_URI,
            "response_types_supported": ["code"],
            "id_token_signing_alg_values_supported": ["RS256"],
        }
        self.loader = OIDCDiscoveryJWKSLoader(
            issuer=ISSUER,
            allowed_hosts=("identity.example.ug", "keys.example.ug"),
            fetch_json=self.fetch_json,
            clock=lambda: self.now,
            cache_ttl_seconds=300,
        )
        self.adapter = OIDCIdentityProviderAdapter(
            issuer=ISSUER, client_id=CLIENT, jwks_loader=self.loader,
            clock=lambda: self.now,
        )

    @staticmethod
    def public_jwk(private_key, kid):
        value = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
        value.update({"kid": kid, "use": "sig", "alg": "RS256"})
        return value

    def fetch_json(self, url, *, timeout_seconds, max_bytes):
        self.calls.append((url, timeout_seconds, max_bytes))
        if url == ISSUER + "/.well-known/openid-configuration":
            return self.metadata
        if url == JWKS_URI:
            return {"keys": self.active_jwks}
        raise AssertionError("unexpected provider URL")

    def token(self, private_key, kid):
        claims = {
            "iss": ISSUER, "sub": "opaque-subject/123", "aud": CLIENT,
            "iat": self.now - 5, "exp": self.now + 180,
            "auth_time": self.now - 3, "nonce": "nonce-integration-0001",
            "acr": "urn:loa:2", "amr": ["pwd", "otp"],
        }
        return jwt.encode(claims, private_key, algorithm="RS256",
                          headers={"kid": kid})

    def verify(self, token):
        return self.adapter.verify_credential(
            token, tenant_id="tenant-ug-bank",
            expected_audience=CLIENT, expected_nonce="nonce-integration-0001")

    def test_discovery_jwks_and_signed_id_token_work_together(self):
        result = self.verify(self.token(self.private_one, "key-one"))
        self.assertEqual(result.issuer, ISSUER)
        self.assertEqual(result.subject, "opaque-subject/123")
        self.assertEqual(result.tenant_id, "tenant-ug-bank")
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(call[1] == 5 and call[2] == 1_048_576
                            for call in self.calls))

    def test_unknown_key_refresh_recovers_after_rotation(self):
        self.verify(self.token(self.private_one, "key-one"))
        self.active_jwks = [self.public_jwk(self.private_two, "key-two")]
        result = self.verify(self.token(self.private_two, "key-two"))
        self.assertEqual(result.subject, "opaque-subject/123")
        self.assertEqual(len(self.calls), 6)


if __name__ == "__main__":
    unittest.main()
