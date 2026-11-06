# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import json
import unittest
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from oidc_identity_adapter import OIDCIdentityProviderAdapter
from institutional_identity import IdentityResolutionError


class OIDCAdapterTests(unittest.TestCase):
    def setUp(self):
        self.now = int(time.time())
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key()))
        self.jwk.update({"kid": "key-1", "use": "sig", "alg": "RS256"})
        self.calls = []
        self.keys = [self.jwk]
        self.adapter = self.make_adapter()

    def loader(self, *, force_refresh):
        self.calls.append(force_refresh)
        return {"keys": self.keys}

    def make_adapter(self, **kwargs):
        options = {"issuer": "https://identity.example.ug",
                   "client_id": "kin-sys-api", "jwks_loader": self.loader,
                   "clock": lambda: self.now}
        options.update(kwargs)
        return OIDCIdentityProviderAdapter(**options)

    def claims(self, **changes):
        value = {"iss": "https://identity.example.ug", "sub": "opaque|subject/01",
                 "aud": "kin-sys-api", "iat": self.now - 20,
                 "exp": self.now + 180, "auth_time": self.now - 10,
                 "nonce": "nonce-oidc-000001", "acr": "urn:loa:2",
                 "amr": ["pwd", "otp"]}
        value.update(changes)
        return value

    def token(self, claims=None, *, key=None, alg="RS256", kid="key-1"):
        return jwt.encode(claims or self.claims(), key or self.private,
                          algorithm=alg, headers={"kid": kid})

    def verify(self, token=None):
        return self.adapter.verify_credential(
            token or self.token(), tenant_id="tenant-bank-ug",
            expected_audience="kin-sys-api", expected_nonce="nonce-oidc-000001")

    def test_valid_signed_token_returns_typed_assertion_and_opaque_subject(self):
        result = self.verify()
        self.assertEqual(result.issuer, "https://identity.example.ug")
        self.assertEqual(result.subject, "opaque|subject/01")
        self.assertEqual(result.tenant_id, "tenant-bank-ug")
        self.assertEqual(result.amr, ("pwd", "otp"))
        self.assertTrue(result.credential_ref.startswith("sha256:"))

    def test_signature_tampering_and_wrong_signing_key_fail(self):
        bad = self.token(key=self.other_private)
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_ID_TOKEN_REJECTED"):
            self.verify(bad)

    def test_wrong_issuer_audience_and_nonce_fail(self):
        for claims, code in (
            (self.claims(iss="https://attacker.example"), "OIDC_ID_TOKEN_REJECTED"),
            (self.claims(aud="another-client"), "OIDC_ID_TOKEN_REJECTED"),
            (self.claims(nonce="wrong-nonce"), "OIDC_REQUIRED_CLAIMS_INVALID"),
        ):
            with self.subTest(code=code):
                with self.assertRaisesRegex(IdentityResolutionError, code):
                    self.verify(self.token(claims))

    def test_expiry_future_iat_and_missing_required_claim_fail(self):
        for claims in (
            self.claims(exp=self.now - 1),
            self.claims(iat=self.now + 100),
            {k: v for k, v in self.claims().items() if k != "auth_time"},
        ):
            with self.subTest(claims=claims):
                with self.assertRaises(IdentityResolutionError):
                    self.verify(self.token(claims))

    def test_validation_uses_injected_clock_not_host_clock(self):
        self.now = int(time.time()) + 3600
        self.assertEqual(self.verify().subject, "opaque|subject/01")

    def test_future_not_before_and_authentication_after_issue_are_rejected(self):
        for claims in (
            self.claims(nbf=self.now + 60),
            self.claims(auth_time=self.now - 20, iat=self.now - 30),
        ):
            with self.subTest(claims=claims):
                with self.assertRaisesRegex(IdentityResolutionError, "OIDC_TIME_CLAIMS_INVALID"):
                    self.verify(self.token(claims))

    def test_token_lifetime_is_bounded(self):
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_TIME_CLAIMS_INVALID"):
            self.verify(self.token(self.claims(exp=self.now + 1000)))

    def test_multiple_audiences_require_matching_azp(self):
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_AUTHORIZED_PARTY_MISMATCH"):
            self.verify(self.token(self.claims(aud=["kin-sys-api", "other-client"])))
        self.verify(self.token(self.claims(
            aud=["kin-sys-api", "other-client"], azp="kin-sys-api")))

    def test_disallowed_algorithm_is_rejected_before_key_lookup(self):
        adapter = self.make_adapter(allowed_algorithms=("ES256",))
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_SIGNING_HEADER_REJECTED"):
            adapter.verify_credential(self.token(), tenant_id="tenant-bank-ug",
                expected_audience="kin-sys-api", expected_nonce="nonce-oidc-000001")

    def test_unknown_kid_refreshes_once_for_key_rotation(self):
        replacement = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        new_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(replacement.public_key()))
        new_jwk.update({"kid": "key-2", "use": "sig", "alg": "RS256"})
        token = jwt.encode(self.claims(), replacement, algorithm="RS256",
                           headers={"kid": "key-2"})
        def rotating_loader(*, force_refresh):
            self.calls.append(force_refresh)
            return {"keys": [self.jwk] if not force_refresh else [self.jwk, new_jwk]}
        adapter = self.make_adapter(jwks_loader=rotating_loader)
        result = adapter.verify_credential(token, tenant_id="tenant-bank-ug",
            expected_audience="kin-sys-api", expected_nonce="nonce-oidc-000001")
        self.assertEqual(result.subject, "opaque|subject/01")
        self.assertEqual(self.calls[-2:], [False, True])

    def test_unknown_kid_after_refresh_fails_closed(self):
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_SIGNING_KEY_NOT_FOUND"):
            self.verify(self.token(kid="unknown-key"))

    def test_duplicate_kid_and_key_algorithm_mismatch_fail(self):
        self.keys = [self.jwk, self.jwk]
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_JWKS_DUPLICATE_KEY_ID"):
            self.verify()
        self.keys = [dict(self.jwk, alg="RS384")]
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_SIGNING_KEY_POLICY_MISMATCH"):
            self.verify()

    def test_token_controlled_key_urls_and_unsupported_jose_headers_fail(self):
        token = jwt.encode(self.claims(), self.private, algorithm="RS256",
                           headers={"kid": "key-1", "jku": "https://attacker.example/jwks"})
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_SIGNING_HEADER_REJECTED"):
            self.verify(token)

    def test_private_jwk_material_is_rejected(self):
        self.keys = [dict(self.jwk, d="not-public-material")]
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_PRIVATE_JWK_MATERIAL_REJECTED"):
            self.verify()

    def test_jwks_failure_and_invalid_token_fail_closed(self):
        def broken(*, force_refresh):
            raise OSError("network failure")
        adapter = self.make_adapter(jwks_loader=broken)
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_JWKS_UNAVAILABLE"):
            adapter.verify_credential(self.token(), tenant_id="tenant-bank-ug",
                expected_audience="kin-sys-api", expected_nonce="nonce-oidc-000001")
        with self.assertRaisesRegex(IdentityResolutionError, "OIDC_ID_TOKEN_INVALID"):
            self.verify("not-a-jwt")


if __name__ == "__main__":
    unittest.main()
