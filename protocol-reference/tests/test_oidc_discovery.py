# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import unittest

from oidc_discovery import OIDCDiscoveryJWKSLoader
from institutional_identity import IdentityResolutionError


ISSUER = "https://identity.example.ug/tenant-a"
DISCOVERY = ISSUER + "/.well-known/openid-configuration"


class OIDCDiscoveryLoaderTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.metadata = {
            "issuer": ISSUER,
            "jwks_uri": "https://keys.example.ug/tenant-a/jwks",
            "response_types_supported": ["code"],
            "id_token_signing_alg_values_supported": ["RS256", "ES256"],
        }
        self.jwks = {"keys": [
            {"kty": "RSA", "kid": "key-1", "use": "sig", "alg": "RS256",
             "n": "AQAB", "e": "AQAB"}
        ]}
        self.calls = []
        self.loader = self.make_loader()

    def fetch(self, url, *, timeout_seconds, max_bytes):
        self.calls.append((url, timeout_seconds, max_bytes))
        if url == DISCOVERY:
            return self.metadata
        if url == self.metadata["jwks_uri"]:
            return self.jwks
        raise AssertionError("unexpected URL")

    def make_loader(self, **kwargs):
        options = {
            "issuer": ISSUER, "allowed_hosts": ("identity.example.ug", "keys.example.ug"),
            "fetch_json": self.fetch, "clock": lambda: self.now,
            "cache_ttl_seconds": 60,
        }
        options.update(kwargs)
        return OIDCDiscoveryJWKSLoader(**options)

    def test_loads_exact_issuer_and_jwks_uri(self):
        self.assertEqual(self.loader()["keys"][0]["kid"], "key-1")
        self.assertEqual([call[0] for call in self.calls], [
            DISCOVERY, self.metadata["jwks_uri"]])

    def test_code_flow_provider_is_supported(self):
        self.metadata["response_types_supported"] = ["code"]
        self.assertEqual(len(self.loader()["keys"]), 1)

    def test_cache_is_reused_until_expiry(self):
        self.loader()
        self.loader()
        self.assertEqual(len(self.calls), 2)
        self.now += 61
        self.loader()
        self.assertEqual(len(self.calls), 4)

    def test_forced_refresh_reloads_metadata_and_jwks(self):
        self.loader()
        self.loader(force_refresh=True)
        self.assertEqual(len(self.calls), 4)

    def test_invalidate_drops_cached_jwks(self):
        self.loader()
        self.loader.invalidate()
        self.loader()
        self.assertEqual(len(self.calls), 4)

    def test_metadata_issuer_must_match_exactly(self):
        self.metadata["issuer"] = ISSUER + "/"
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_DISCOVERY_ISSUER_MISMATCH"):
            self.loader()

    def test_jwks_host_must_be_allowlisted(self):
        self.metadata["jwks_uri"] = "https://attacker.example/jwks"
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_PROVIDER_HOST_NOT_ALLOWLISTED"):
            self.loader()

    def test_non_https_or_credentialed_or_query_urls_are_rejected(self):
        for uri in (
            "http://keys.example.ug/jwks",
            "https://user:pass@keys.example.ug/jwks",
            "https://keys.example.ug/jwks?redirect=https://attacker.example",
            "https://keys.example.ug:8443/jwks",
        ):
            with self.subTest(uri=uri):
                self.metadata["jwks_uri"] = uri
                with self.assertRaises(IdentityResolutionError):
                    self.loader()
        self.metadata["jwks_uri"] = "https://keys.example.ug/tenant-a/jwks"

    def test_discovery_algorithm_must_intersect_receiver_allowlist(self):
        self.metadata["id_token_signing_alg_values_supported"] = ["HS256"]
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_DISCOVERY_ALGORITHM_UNSUPPORTED"):
            self.loader()

    def test_duplicate_key_ids_and_private_material_are_rejected(self):
        self.jwks["keys"].append(dict(self.jwks["keys"][0]))
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_JWKS_KEY_ID_INVALID"):
            self.loader()
        self.jwks["keys"] = [dict(self.jwks["keys"][0], d="private")]
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_PRIVATE_JWK_MATERIAL_REJECTED"):
            self.loader()

    def test_key_count_and_document_shape_are_bounded(self):
        self.jwks = {"keys": []}
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_JWKS_DOCUMENT_INVALID"):
            self.loader()
        self.jwks = {"keys": [{"kid": "x", "kty": "RSA"}] * 3}
        loader = self.make_loader(max_jwks_keys=2)
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_JWKS_DOCUMENT_INVALID"):
            loader()

    def test_transport_failure_is_normalised(self):
        def failed(*args, **kwargs):
            raise TimeoutError("network unavailable")
        loader = self.make_loader(fetch_json=failed)
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_PROVIDER_DOCUMENT_UNAVAILABLE"):
            loader()

    def test_issuer_host_must_be_allowlisted(self):
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_ISSUER_HOST_NOT_ALLOWLISTED"):
            self.make_loader(allowed_hosts=("keys.example.ug",))


if __name__ == "__main__":
    unittest.main()
