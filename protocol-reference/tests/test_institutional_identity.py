# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import unittest
from institutional_identity import IdentityResolutionError, InstitutionalIdentityResolver, VerifiedIdentityAssertion

class FakeAdapter:
    def __init__(self, assertion):
        self.assertion = assertion
    def verify_credential(self, credential, *, tenant_id, expected_audience, expected_nonce):
        if credential != "opaque-test-credential":
            raise ValueError("invalid credential")
        return self.assertion

class IdentityResolverTests(unittest.TestCase):
    def setUp(self):
        self.now = 1800000000
        self.assertion = VerifiedIdentityAssertion(
            "https://idp.example.test", "subject:alice", "tenant-bank-ug",
            "kin-sys-api", self.now-20, self.now+180, self.now-10,
            "urn:kin-sys:loa:2", ("pwd","otp"), "nonce-identity-000001",
            "credential-ref-001")
        self.adapter = FakeAdapter(self.assertion)
        self.policy = {"issuer":"https://idp.example.test","status":"ACTIVE",
            "audiences":["kin-sys-api"],"allowed_acr":["urn:kin-sys:loa:2"],
            "required_amr":["otp"],"valid_from":self.now-1000,"valid_until":self.now+1000}
        self.binding = {"actor_ref":"actor:alice","status":"ACTIVE","binding_version":"binding-v3"}
        self.nonces = set()
        self.resolver = self.make_resolver()

    def make_resolver(self, **overrides):
        options = {
            "adapter_loader":lambda tenant, provider: self.adapter if
                tenant=="tenant-bank-ug" and provider=="national-idp" else None,
            "issuer_policy_loader":lambda tenant, issuer: self.policy if
                tenant=="tenant-bank-ug" and issuer=="https://idp.example.test" else None,
            "subject_binding_loader":lambda tenant, issuer, subject: self.binding if
                tenant=="tenant-bank-ug" and subject=="subject:alice" else None,
            "consume_nonce":self.consume_nonce,"clock":lambda:self.now}
        options.update(overrides)
        return InstitutionalIdentityResolver(**options)

    def consume_nonce(self, tenant, nonce, *, expires_at):
        key=(tenant,nonce)
        if key in self.nonces or expires_at<=self.now: return False
        self.nonces.add(key)
        return True

    def auth(self, **overrides):
        args={"credential":"opaque-test-credential","tenant_id":"tenant-bank-ug",
            "provider_id":"national-idp","expected_audience":"kin-sys-api",
            "expected_nonce":"nonce-identity-000001"}
        args.update(overrides)
        return self.resolver.authenticate(**args)

    def replace_assertion(self, **changes):
        values=dict(self.assertion.__dict__)
        values.update(changes)
        self.assertion=VerifiedIdentityAssertion(**values)
        self.adapter.assertion=self.assertion

    def test_valid_identity_maps_to_local_actor_without_roles(self):
        p=self.auth()
        self.assertTrue(p["authenticated"] and p["verified"])
        self.assertEqual(p["actor_ref"],"actor:alice")
        self.assertEqual(p["roles"],[])
        self.assertTrue(p["authority_grant_required"])

    def test_receiver_selects_provider_from_configuration(self):
        with self.assertRaisesRegex(IdentityResolutionError,"IDENTITY_PROVIDER_NOT_CONFIGURED"):
            self.auth(provider_id="unconfigured-idp")

    def test_adapter_must_return_typed_verified_result(self):
        self.adapter.verify_credential=lambda *a,**k: {"verified":True}
        with self.assertRaisesRegex(IdentityResolutionError,"UNVERIFIED_IDENTITY_ADAPTER_RESULT"):
            self.auth()

    def test_tenant_audience_and_nonce_are_bound(self):
        self.replace_assertion(tenant_id="tenant-other")
        with self.assertRaisesRegex(IdentityResolutionError,"IDENTITY_TENANT_MISMATCH"): self.auth()
        self.setUp(); self.replace_assertion(audience="other-api")
        with self.assertRaisesRegex(IdentityResolutionError,"IDENTITY_AUDIENCE_MISMATCH"): self.auth()
        self.setUp(); self.replace_assertion(nonce="nonce-wrong-identity-000001")
        with self.assertRaisesRegex(IdentityResolutionError,"IDENTITY_NONCE_MISMATCH"): self.auth()

    def test_expired_stale_and_overlong_credentials_fail(self):
        for changes in ({"expires_at":self.now-1},{"auth_time":self.now-1000},
                        {"expires_at":self.now+1000}):
            self.setUp(); self.replace_assertion(**changes)
            with self.assertRaisesRegex(IdentityResolutionError,"IDENTITY_CREDENTIAL_EXPIRED_OR_STALE"):
                self.auth()

    def test_issuer_trust_and_validity_are_receiver_controlled(self):
        self.policy["status"]="REVOKED"
        with self.assertRaisesRegex(IdentityResolutionError,"IDENTITY_ISSUER_NOT_TRUSTED"): self.auth()
        self.setUp(); self.policy["valid_until"]=self.now
        with self.assertRaisesRegex(IdentityResolutionError,"IDENTITY_ISSUER_POLICY_OUTSIDE_VALIDITY"):
            self.auth()

    def test_audience_assurance_and_authentication_methods_are_enforced(self):
        self.policy["audiences"]=["other-api"]
        with self.assertRaisesRegex(IdentityResolutionError,"IDENTITY_AUDIENCE_NOT_ALLOWED"): self.auth()
        self.setUp(); self.policy["allowed_acr"]=["urn:kin-sys:loa:3"]
        with self.assertRaisesRegex(IdentityResolutionError,"IDENTITY_ASSURANCE_INSUFFICIENT"): self.auth()
        self.setUp(); self.policy["required_amr"]=["fido"]
        with self.assertRaisesRegex(IdentityResolutionError,"IDENTITY_AUTHENTICATION_METHOD_INSUFFICIENT"):
            self.auth()

    def test_subject_must_have_active_tenant_local_binding(self):
        self.binding["status"]="SUSPENDED"
        with self.assertRaisesRegex(IdentityResolutionError,"IDENTITY_SUBJECT_NOT_BOUND"): self.auth()

    def test_assertion_nonce_is_single_use(self):
        self.auth()
        with self.assertRaisesRegex(IdentityResolutionError,"IDENTITY_ASSERTION_REPLAY_DETECTED"):
            self.auth()

    def test_native_credential_verification_failure_fails_closed(self):
        with self.assertRaisesRegex(IdentityResolutionError,"IDENTITY_CREDENTIAL_VERIFICATION_FAILED"):
            self.auth(credential="invalid")

if __name__=="__main__": unittest.main()
