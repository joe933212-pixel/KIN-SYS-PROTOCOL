# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import os
import tempfile
import unittest

from institutional_trust_registry import (
    InstitutionalTrustRegistry, TrustRegistryConflict, TrustRegistryError,
)
from institutional_authority import (
    InMemoryNonceGuard, InstitutionalAuthorityResolver, sign_actor_claims,
    sign_federated_actor_claims,
)
from institutional_grant_provenance import sign_authority_grant
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class InstitutionalTrustRegistryTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        self.registry = InstitutionalTrustRegistry(self.path)
        self.addCleanup(self.registry.close)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))
        self.receiver_private_key = Ed25519PrivateKey.generate()
        self.receiver_public_bytes = self.receiver_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        self.receiver_key_record = {
            "tenant_id": "tenant-a", "receiver_institution_id": "BANK-UG-01",
            "key_id": "receiver-acceptance-key", "algorithm": "Ed25519",
            "status": "ACTIVE", "public_key_ref": "vault://public/receiver-acceptance-key",
            "valid_from": 100, "valid_until": 2000,
        }
        self.grant = {
            "grant_id": "GRANT-01", "tenant_id": "tenant-a", "status": "ACTIVE",
            "actor_ref": "actor:registrar", "institution_id": "BANK-UG-01",
            "authority_ref": "authority:registry", "roles": ["institution_verifier"],
            "resource_types": ["institution"], "resource_ids": ["BANK-UG-01"],
            "valid_from": 100, "valid_until": 200, "version": "1",
            "issuer_institution_id": "NATIONAL-REGISTRY", "approval_ref": "approval:01",
        }

    def create(self):
        return self.registry.create_grant_version(
            tenant_id="tenant-a", grant=self.grant, event_id="grant-event-1",
            actor_ref="actor:approver", evidence_ref="evidence:approval-1")


    def configure_issuer(self, issuer_id, key_id, public_key_ref, private_key,
                         *, valid_from=100, valid_until=2000):
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        self.registry.register_trusted_issuer(
            tenant_id="tenant-a", issuer_institution_id=issuer_id,
            trust_basis_ref="trust-agreement:" + issuer_id.lower(),
            valid_from=valid_from, valid_until=valid_until,
            actor_ref="actor:trust-admin", evidence_ref="evidence:trust-approval")
        self.registry.register_trust_key(
            tenant_id="tenant-a", issuer_institution_id=issuer_id,
            key_id=key_id, algorithm="Ed25519", public_key_ref=public_key_ref,
            valid_from=valid_from, valid_until=valid_until,
            actor_ref="actor:trust-admin", evidence_ref="evidence:key-approval")
        return public_bytes

    def receiver_signing_key_loader(self, tenant_id, institution_id, key_id):
        if (tenant_id, institution_id, key_id) == (
            "tenant-a", "BANK-UG-01", "receiver-acceptance-key"
        ):
            return self.receiver_private_key
        return None

    def receiver_acceptance_key_loader(self, tenant_id, institution_id, key_id):
        if (tenant_id, institution_id, key_id) == (
            "tenant-a", "BANK-UG-01", "receiver-acceptance-key"
        ):
            return dict(self.receiver_key_record)
        return None

    def receiver_acceptance_public_key_loader(self, public_key_ref):
        return (self.receiver_public_bytes
                if public_key_ref == "vault://public/receiver-acceptance-key" else None)

    def accept_federated_grant(self, issuer_id, key_id, public_key_ref,
                               private_key, public_bytes):
        pending = dict(self.grant, status="PENDING",
                       issuer_institution_id=issuer_id)
        envelope = sign_authority_grant(
            pending, key_id=key_id, private_key=private_key)
        approval = {
            "actor_ref": "actor:receiver-approver",
            "approval_ref": "approval:receiver-accept",
        }
        return self.registry.accept_signed_grant(
            tenant_id="tenant-a", envelope=envelope,
            receiver_institution_id="BANK-UG-01",
            receiver_approval=approval,
            verify_receiver_approval=lambda grant, supplied: (
                supplied == approval and grant["grant_id"] == pending["grant_id"]),
            public_key_loader=lambda ref: (
                public_bytes if ref == public_key_ref else None),
            event_id="grant-accept-event",
            actor_ref=approval["actor_ref"],
            evidence_ref=approval["approval_ref"],
            receiver_signing_key_id="receiver-acceptance-key",
            receiver_signing_key_loader=self.receiver_signing_key_loader,
            clock=lambda: 150,
        )

    def test_grant_is_persisted_and_resolver_compatible(self):
        self.create()
        self.assertEqual(self.registry.load_grant("tenant-a", "GRANT-01"), self.grant)
        self.assertIsNone(self.registry.load_grant("tenant-b", "GRANT-01"))

    def test_grant_versions_are_immutable(self):
        self.create()
        changed = dict(self.grant, authority_ref="authority:changed")
        with self.assertRaisesRegex(TrustRegistryConflict, "IMMUTABLE_GRANT_VERSION_CONFLICT"):
            self.registry.create_grant_version(
                tenant_id="tenant-a", grant=changed, event_id="grant-event-2",
                actor_ref="actor:approver", evidence_ref="evidence:approval-2")

    def test_revisions_require_current_version_and_advance_head(self):
        self.create()
        revised = dict(self.grant, version="2", roles=["institution_verifier", "reviewer"])
        self.registry.create_grant_revision(
            tenant_id="tenant-a", grant_id="GRANT-01", expected_version="1",
            grant=revised, event_id="grant-event-2", actor_ref="actor:approver",
            evidence_ref="evidence:approval-2")
        self.assertEqual(self.registry.load_grant("tenant-a", "GRANT-01"), revised)
        with self.assertRaisesRegex(TrustRegistryConflict, "STALE_GRANT_VERSION"):
            self.registry.create_grant_revision(
                tenant_id="tenant-a", grant_id="GRANT-01", expected_version="1",
                grant=dict(revised, version="3"), event_id="grant-event-3",
                actor_ref="actor:approver", evidence_ref="evidence:approval-3")

    def test_revocation_advances_head_and_invalidates_old_assertion_version(self):
        self.create()
        self.registry.revoke_grant(
            tenant_id="tenant-a", grant_id="GRANT-01", expected_version="1",
            new_version="2", event_id="grant-revoked-1",
            actor_ref="actor:authority-admin", evidence_ref="approval:revoke-1")
        current = self.registry.load_grant("tenant-a", "GRANT-01")
        self.assertEqual(current["status"], "REVOKED")
        self.assertEqual(current["version"], "2")
        self.assertNotEqual(current["version"], "1")
        self.assertEqual(self.registry.list_grant_events(
            tenant_id="tenant-a", grant_id="GRANT-01")[-1]["event_type"], "GRANT_REVOKED")

    def test_persistent_verified_grant_store_integrates_with_signed_authority_resolver(self):
        self.create()
        key = b"reference-test-signing-key-32-bytes-minimum"
        resolver = InstitutionalAuthorityResolver(
            verification_keys={"issuer-key-1": key},
            grant_store=self.registry,
            consume_nonce=InMemoryNonceGuard().consume,
            clock=lambda: 150,
        )
        claims = {
            "key_id": "issuer-key-1", "tenant_id": "tenant-a",
            "actor_ref": "actor:registrar", "grant_id": "GRANT-01",
            "grant_version": "1", "issued_at": 140, "expires_at": 160,
            "nonce": "nonce-persistent-000001",
        }
        assertion = {"claims": claims, "signature": sign_actor_claims(claims, key)}
        resolved = resolver.resolve(
            assertion, tenant_id="tenant-a",
            resource_type="institution", resource_id="BANK-UG-01")
        self.assertEqual(resolved["grant_ref"], "GRANT-01@1")
        self.assertEqual(resolved["grant_id"], "GRANT-01")
        self.assertEqual(resolved["grant_version"], "1")
        self.registry.revoke_grant(
            tenant_id="tenant-a", grant_id="GRANT-01", expected_version="1",
            new_version="2", event_id="grant-revoked-1",
            actor_ref="actor:authority-admin", evidence_ref="approval:revoke-1")
        claims["nonce"] = "nonce-persistent-000002"
        assertion = {"claims": claims, "signature": sign_actor_claims(claims, key)}
        with self.assertRaisesRegex(ValueError, "AUTHORITY_GRANT_SCOPE_MISMATCH"):
            resolver.resolve(
                assertion, tenant_id="tenant-a",
                resource_type="institution", resource_id="BANK-UG-01")

    def test_revocation_in_one_tenant_does_not_mutate_another_tenants_grant(self):
        # Two independent institutions may use the same local grant identifier.
        # A revocation in tenant-a must not advance or invalidate tenant-b's head.
        self.create()
        tenant_b_grant = dict(
            self.grant,
            tenant_id="tenant-b",
            actor_ref="actor:registrar-b",
            institution_id="BANK-KE-01",
            resource_ids=["BANK-KE-01"],
            authority_ref="authority:ke-registry",
            approval_ref="approval:ke-01",
        )
        self.registry.create_grant_version(
            tenant_id="tenant-b", grant=tenant_b_grant,
            event_id="tenant-b-grant-event-1",
            actor_ref="actor:ke-approver", evidence_ref="evidence:ke-approval",
        )

        key = b"tenant-isolation-test-signing-key-32-bytes"
        resolver_b = InstitutionalAuthorityResolver(
            verification_keys={"issuer-key-b": key},
            grant_store=self.registry,
            consume_nonce=InMemoryNonceGuard().consume,
            clock=lambda: 150,
        )

        def resolve_tenant_b(nonce):
            claims = {
                "key_id": "issuer-key-b", "tenant_id": "tenant-b",
                "actor_ref": "actor:registrar-b", "grant_id": "GRANT-01",
                "grant_version": "1", "issued_at": 140, "expires_at": 160,
                "nonce": nonce,
            }
            assertion = {
                "claims": claims,
                "signature": sign_actor_claims(claims, key),
            }
            return resolver_b.resolve(
                assertion, tenant_id="tenant-b",
                resource_type="institution", resource_id="BANK-KE-01",
            )

        self.assertEqual(resolve_tenant_b("tenant-b-nonce-before-0001")["grant_ref"],
                         "GRANT-01@1")

        self.registry.revoke_grant(
            tenant_id="tenant-a", grant_id="GRANT-01", expected_version="1",
            new_version="2", event_id="tenant-a-grant-revoked",
            actor_ref="actor:authority-admin", evidence_ref="approval:revoke-tenant-a",
        )

        tenant_a_current = self.registry.load_grant("tenant-a", "GRANT-01")
        tenant_b_current = self.registry.load_grant("tenant-b", "GRANT-01")
        self.assertEqual(tenant_a_current["status"], "REVOKED")
        self.assertEqual(tenant_a_current["version"], "2")
        self.assertEqual(tenant_b_current, tenant_b_grant)
        self.assertEqual(resolve_tenant_b("tenant-b-nonce-after-0002")["grant_ref"],
                         "GRANT-01@1")
        self.assertEqual(
            [event["event_type"] for event in self.registry.list_grant_events(
                tenant_id="tenant-b", grant_id="GRANT-01")],
            ["GRANT_VERSION_CREATED"],
        )

    def test_tenant_context_and_grant_scope_are_enforced(self):
        with self.assertRaisesRegex(TrustRegistryError, "TENANT_CONTEXT_REQUIRED"):
            self.registry.load_grant("", "GRANT-01")
        with self.assertRaisesRegex(TrustRegistryError, "GRANT_TENANT_MISMATCH"):
            self.registry.create_grant_version(
                tenant_id="tenant-b", grant=self.grant, event_id="event-b",
                actor_ref="actor:approver", evidence_ref="evidence:approval")

    def test_trust_key_registration_rotation_and_revocation_are_audited(self):
        self.registry.register_trust_key(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            key_id="issuer-key-1", algorithm="Ed25519",
            public_key_ref="vault://trust/issuer-key-1/public",
            valid_from=100, valid_until=1000,
            actor_ref="actor:trust-admin", evidence_ref="evidence:key-approval")
        self.registry.register_trust_key(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            key_id="issuer-key-2", algorithm="Ed25519",
            public_key_ref="vault://trust/issuer-key-2/public",
            valid_from=900, valid_until=1800,
            actor_ref="actor:trust-admin", evidence_ref="evidence:key-rotation")
        self.assertEqual(self.registry.get_trust_key(
            tenant_id="tenant-a", key_id="issuer-key-1")["status"], "ACTIVE")
        self.registry.revoke_trust_key(
            tenant_id="tenant-a", key_id="issuer-key-1",
            actor_ref="actor:trust-admin", evidence_ref="evidence:key-revoke",
            event_id="key-revoked-1")
        self.assertEqual(self.registry.get_trust_key(
            tenant_id="tenant-a", key_id="issuer-key-1")["status"], "REVOKED")
        self.assertEqual(len(self.registry.list_trust_key_events(
            tenant_id="tenant-a", key_id="issuer-key-1")), 2)
        self.assertIsNone(self.registry.get_trust_key(
            tenant_id="tenant-b", key_id="issuer-key-1"))

    def test_ed25519_federation_requires_trusted_issuer_and_valid_key(self):
        private_key = Ed25519PrivateKey.generate()
        public_key_ref = "vault://public/ed25519-issuer-key-1"
        public_bytes = self.configure_issuer(
            "NATIONAL-REGISTRY", "ed25519-issuer-key-1",
            public_key_ref, private_key)
        self.accept_federated_grant(
            "NATIONAL-REGISTRY", "ed25519-issuer-key-1",
            public_key_ref, private_key, public_bytes)
        resolver = InstitutionalAuthorityResolver(
            grant_store=self.registry,
            consume_nonce=InMemoryNonceGuard().consume,
            trust_key_loader=self.registry.load_trust_key,
            public_key_loader=lambda ref: public_bytes if ref == "vault://public/ed25519-issuer-key-1" else None,
            receiver_acceptance_key_loader=self.receiver_acceptance_key_loader,
            receiver_acceptance_public_key_loader=self.receiver_acceptance_public_key_loader,
            clock=lambda: 150)
        claims = {
            "key_id": "ed25519-issuer-key-1", "tenant_id": "tenant-a",
            "actor_ref": "actor:registrar", "grant_id": "GRANT-01",
            "grant_version": "1", "issued_at": 140, "expires_at": 160,
            "nonce": "federated-nonce-000001",
            "issuer_institution_id": "NATIONAL-REGISTRY",
        }
        assertion = {"claims": claims,
                     "signature": sign_federated_actor_claims(claims, private_key)}
        result = resolver.resolve(
            assertion, tenant_id="tenant-a",
            resource_type="institution", resource_id="BANK-UG-01")
        self.assertEqual(result["assertion_algorithm"], "Ed25519")
        self.assertEqual(result["assertion_issuer_institution_id"], "NATIONAL-REGISTRY")

        self.registry.revoke_trust_key(
            tenant_id="tenant-a", key_id="ed25519-issuer-key-1",
            actor_ref="actor:trust-admin", evidence_ref="evidence:key-revocation",
            event_id="ed25519-key-revoked")
        claims["nonce"] = "federated-nonce-000002"
        assertion = {"claims": claims,
                     "signature": sign_federated_actor_claims(claims, private_key)}
        with self.assertRaisesRegex(ValueError, "TRUST_KEY_NOT_ACTIVE"):
            resolver.resolve(
                assertion, tenant_id="tenant-a",
                resource_type="institution", resource_id="BANK-UG-01")

    def test_expired_issuer_trust_relationship_fails_closed(self):
        self.create()
        self.registry.register_trusted_issuer(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            trust_basis_ref="trust-agreement:expired", valid_from=10, valid_until=149,
            actor_ref="actor:trust-admin", evidence_ref="evidence:trust-approval")
        self.registry.register_trust_key(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            key_id="expired-root-key", algorithm="Ed25519",
            public_key_ref="vault://public/expired-root-key",
            valid_from=100, valid_until=2000,
            actor_ref="actor:trust-admin", evidence_ref="evidence:key-approval")
        private_key = Ed25519PrivateKey.generate()
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        resolver = InstitutionalAuthorityResolver(
            grant_store=self.registry,
            consume_nonce=InMemoryNonceGuard().consume,
            trust_key_loader=self.registry.load_trust_key,
            public_key_loader=lambda ref: public_bytes,
            clock=lambda: 150)
        claims = {
            "key_id": "expired-root-key", "tenant_id": "tenant-a",
            "actor_ref": "actor:registrar", "grant_id": "GRANT-01",
            "grant_version": "1", "issued_at": 140, "expires_at": 160,
            "nonce": "expired-root-nonce-001",
            "issuer_institution_id": "NATIONAL-REGISTRY",
        }
        assertion = {"claims": claims,
                     "signature": sign_federated_actor_claims(claims, private_key)}
        with self.assertRaisesRegex(ValueError, "ISSUER_TRUST_OUTSIDE_VALIDITY"):
            resolver.resolve(
                assertion, tenant_id="tenant-a",
                resource_type="institution", resource_id="BANK-UG-01")

    def test_revoking_issuer_trust_disables_all_its_registered_keys(self):
        self.create()
        self.registry.register_trusted_issuer(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            trust_basis_ref="trust-agreement:ug-01", valid_from=100, valid_until=2000,
            actor_ref="actor:trust-admin", evidence_ref="evidence:trust-approval")
        self.registry.register_trust_key(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            key_id="issuer-key-under-root", algorithm="Ed25519",
            public_key_ref="vault://public/key-under-root",
            valid_from=100, valid_until=2000,
            actor_ref="actor:trust-admin", evidence_ref="evidence:key-approval")
        self.assertIsNotNone(self.registry.load_trust_key("tenant-a", "issuer-key-under-root"))
        self.registry.revoke_trusted_issuer(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            actor_ref="actor:trust-admin", evidence_ref="evidence:trust-revocation",
            event_id="issuer-trust-revoked")
        self.assertIsNone(self.registry.load_trust_key("tenant-a", "issuer-key-under-root"))
        self.assertEqual(len(self.registry.list_trusted_issuer_events(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY")), 2)

    def test_federated_assertion_issuer_must_match_authority_grant_issuer(self):
        national_private_key = Ed25519PrivateKey.generate()
        national_ref = "vault://public/national-grant-key"
        national_public = self.configure_issuer(
            "NATIONAL-REGISTRY", "national-grant-key",
            national_ref, national_private_key)
        self.accept_federated_grant(
            "NATIONAL-REGISTRY", "national-grant-key",
            national_ref, national_private_key, national_public)

        other_private_key = Ed25519PrivateKey.generate()
        other_ref = "vault://public/other-issuer-key"
        other_public = self.configure_issuer(
            "OTHER-ISSUER", "other-issuer-key", other_ref, other_private_key)
        resolver = InstitutionalAuthorityResolver(
            grant_store=self.registry,
            consume_nonce=InMemoryNonceGuard().consume,
            trust_key_loader=self.registry.load_trust_key,
            public_key_loader=lambda ref: {
                national_ref: national_public,
                other_ref: other_public,
            }.get(ref),
            receiver_acceptance_key_loader=self.receiver_acceptance_key_loader,
            receiver_acceptance_public_key_loader=self.receiver_acceptance_public_key_loader,
            clock=lambda: 150)
        claims = {
            "key_id": "other-issuer-key", "tenant_id": "tenant-a",
            "actor_ref": "actor:registrar", "grant_id": "GRANT-01",
            "grant_version": "1", "issued_at": 140, "expires_at": 160,
            "nonce": "issuer-mismatch-nonce-01",
            "issuer_institution_id": "OTHER-ISSUER",
        }
        assertion = {"claims": claims,
                     "signature": sign_federated_actor_claims(claims, other_private_key)}
        with self.assertRaisesRegex(ValueError, "GRANT_ISSUER_MISMATCH"):
            resolver.resolve(
                assertion, tenant_id="tenant-a",
                resource_type="institution", resource_id="BANK-UG-01")

    def test_ed25519_key_is_not_accepted_without_explicit_issuer_trust(self):
        self.create()
        private_key = Ed25519PrivateKey.generate()
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        self.registry.register_trust_key(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            key_id="untrusted-issuer-key", algorithm="Ed25519",
            public_key_ref="vault://public/untrusted", valid_from=100, valid_until=2000,
            actor_ref="actor:trust-admin", evidence_ref="evidence:key-registration")
        resolver = InstitutionalAuthorityResolver(
            grant_store=self.registry,
            consume_nonce=InMemoryNonceGuard().consume,
            trust_key_loader=self.registry.load_trust_key,
            public_key_loader=lambda ref: public_bytes,
            clock=lambda: 150)
        claims = {
            "key_id": "untrusted-issuer-key", "tenant_id": "tenant-a",
            "actor_ref": "actor:registrar", "grant_id": "GRANT-01",
            "grant_version": "1", "issued_at": 140, "expires_at": 160,
            "nonce": "untrusted-nonce-000001",
            "issuer_institution_id": "NATIONAL-REGISTRY",
        }
        assertion = {"claims": claims,
                     "signature": sign_federated_actor_claims(claims, private_key)}
        with self.assertRaisesRegex(ValueError, "UNTRUSTED_SIGNING_KEY"):
            resolver.resolve(
                assertion, tenant_id="tenant-a",
                resource_type="institution", resource_id="BANK-UG-01")


    def test_resolver_rejects_a_bare_grant_callback_instead_of_trusting_it(self):
        with self.assertRaisesRegex(ValueError, "VERIFIED_GRANT_STORE_REQUIRED"):
            InstitutionalAuthorityResolver(
                grant_store=lambda tenant, grant_id: self.grant,
                verification_keys={"issuer-key-1": b"x" * 32},
                consume_nonce=InMemoryNonceGuard().consume,
            )

    def test_duplicate_key_id_and_unknown_algorithm_fail_closed(self):
        args = dict(tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
                    key_id="issuer-key-1", algorithm="Ed25519",
                    public_key_ref="vault://trust/key/public", valid_from=100,
                    valid_until=1000, actor_ref="actor:trust-admin",
                    evidence_ref="evidence:key-approval")
        self.registry.register_trust_key(**args)
        with self.assertRaisesRegex(TrustRegistryConflict, "TRUST_KEY_ID_ALREADY_REGISTERED"):
            self.registry.register_trust_key(**args)
        args["key_id"] = "issuer-key-2"
        for unsupported in ("homemade-signature", "HMAC-SHA256"):
            args["algorithm"] = unsupported
            with self.assertRaisesRegex(TrustRegistryError, "UNSUPPORTED_TRUST_KEY_ALGORITHM"):
                self.registry.register_trust_key(**args)




    def test_federated_resolver_requires_receiver_acceptance_trust_adapter(self):
        issuer_private = Ed25519PrivateKey.generate()
        issuer_ref = "vault://public/receiver-acceptance-required-issuer"
        issuer_public = self.configure_issuer(
            "NATIONAL-REGISTRY", "acceptance-required-issuer",
            issuer_ref, issuer_private)
        self.accept_federated_grant(
            "NATIONAL-REGISTRY", "acceptance-required-issuer",
            issuer_ref, issuer_private, issuer_public)
        resolver = InstitutionalAuthorityResolver(
            grant_store=self.registry,
            consume_nonce=InMemoryNonceGuard().consume,
            trust_key_loader=self.registry.load_trust_key,
            public_key_loader=lambda ref: issuer_public if ref == issuer_ref else None,
            clock=lambda: 150)
        claims = {
            "key_id": "acceptance-required-issuer", "tenant_id": "tenant-a",
            "actor_ref": "actor:registrar", "grant_id": "GRANT-01",
            "grant_version": "1", "issued_at": 140, "expires_at": 160,
            "nonce": "receiver-acceptance-required-01",
            "issuer_institution_id": "NATIONAL-REGISTRY",
        }
        assertion = {
            "claims": claims,
            "signature": sign_federated_actor_claims(claims, issuer_private),
        }
        with self.assertRaisesRegex(
            ValueError, "RECEIVER_ACCEPTANCE_TRUST_CONFIGURATION_INCOMPLETE"
        ):
            resolver.resolve(
                assertion, tenant_id="tenant-a",
                resource_type="institution", resource_id="BANK-UG-01")


if __name__ == "__main__":
    unittest.main()
