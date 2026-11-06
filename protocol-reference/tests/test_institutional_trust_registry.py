# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import os
import tempfile
import unittest

from institutional_trust_registry import (
    InstitutionalTrustRegistry, TrustRegistryConflict, TrustRegistryError,
)
from institutional_authority import (
    InMemoryNonceGuard, InstitutionalAuthorityResolver, sign_actor_claims,
)


class InstitutionalTrustRegistryTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        self.registry = InstitutionalTrustRegistry(self.path)
        self.addCleanup(self.registry.close)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))
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

    def test_persistent_grant_loader_integrates_with_signed_authority_resolver(self):
        self.create()
        key = b"reference-test-signing-key-32-bytes-minimum"
        resolver = InstitutionalAuthorityResolver(
            verification_keys={"issuer-key-1": key},
            grant_loader=self.registry.load_grant,
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
        args["algorithm"] = "homemade-signature"
        with self.assertRaisesRegex(TrustRegistryError, "UNSUPPORTED_TRUST_KEY_ALGORITHM"):
            self.registry.register_trust_key(**args)


if __name__ == "__main__":
    unittest.main()
