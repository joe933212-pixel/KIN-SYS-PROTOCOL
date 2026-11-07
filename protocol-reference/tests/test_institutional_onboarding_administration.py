# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import os
import tempfile
import unittest

from institutional_authority import InMemoryNonceGuard, InstitutionalAuthorityResolver, sign_actor_claims
from institutional_onboarding_administration import (
    InstitutionalOnboardingAdministration, InstitutionalOnboardingError,
)
from institutional_registry import InstitutionalRegistry, RegistryConflict


class EvidenceVerifier:
    def __init__(self, rejected_refs=()):
        self.rejected_refs = set(rejected_refs)
        self.calls = []

    def verify(self, evidence_ref, *, context):
        self.calls.append((evidence_ref, dict(context)))
        if evidence_ref in self.rejected_refs:
            return {"verified": False, "evidence_ref": evidence_ref}
        import hashlib
        digest = hashlib.sha256(evidence_ref.encode()).hexdigest()
        return {"verified": True, "evidence_ref": evidence_ref,
                "verification_ref": "verification:" + digest[:16],
                "evidence_digest": "sha256:" + digest}


class InstitutionalOnboardingAdministrationTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.registry = InstitutionalRegistry(self.path)
        self.addCleanup(self.registry.close)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))
        self.key = b"onboarding-test-signing-key-at-least-32-bytes"
        self.grant = {
            "grant_id": "GRANT-1", "tenant_id": "tenant-a", "status": "ACTIVE",
            "actor_ref": "actor:registrar", "institution_id": "BANK-1",
            "authority_ref": "authority:registry", "roles": ["institution_registrar"],
            "resource_types": ["institution"], "resource_ids": ["BANK-1"],
            "valid_from": 900, "valid_until": 1100, "version": "1",
            "issuer_institution_id": "REGISTRY-UG", "approval_ref": "approval:grant",
        }
        self.nonces = InMemoryNonceGuard()
        resolver = InstitutionalAuthorityResolver(
            verification_keys={"key-1": self.key},
            grant_loader=lambda tenant, grant: self.grant if tenant == "tenant-a" and grant == "GRANT-1" else None,
            consume_nonce=self.nonces.consume, clock=lambda: 1000,
        )
        self.verifier = EvidenceVerifier()
        self.admin = InstitutionalOnboardingAdministration(
            self.registry, resolver, self.verifier)
        self.counter = 0

    def assertion(self):
        self.counter += 1
        claims = {"key_id":"key-1","tenant_id":"tenant-a","actor_ref":"actor:registrar",
                  "grant_id":"GRANT-1","grant_version":"1","issued_at":990,
                  "expires_at":1010,"nonce":f"onboard-nonce-{self.counter:08d}"}
        return {"claims":claims,"signature":sign_actor_claims(claims,self.key)}

    def record(self):
        return {"institution_id":"BANK-1","legal_name":"Example Bank",
                "jurisdiction_ref":"UG","status":"PENDING_VERIFICATION",
                "verification_ref":None}

    def test_registration_creates_record_and_audit_event_atomically(self):
        result = self.admin.register(
            tenant_id="tenant-a", resource_type="institution", version="1.0",
            event_id="evt-register-1", record=self.record(),
            actor_assertion=self.assertion(), evidence_ref="evidence:incorporation",
        )
        self.assertTrue(result["created"])
        current = self.admin.get(tenant_id="tenant-a",resource_type="institution",resource_id="BANK-1")
        self.assertEqual(current["status"],"PENDING_VERIFICATION")
        events = self.admin.events(tenant_id="tenant-a",resource_type="institution",resource_id="BANK-1")
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]["event_type"],"INSTITUTION_REGISTERED")
        receipt = events[0]["payload"]["evidence_verification"]
        self.assertTrue(receipt["verification_ref"].startswith("verification:"))
        self.assertTrue(receipt["evidence_digest"].startswith("sha256:"))
        self.assertEqual(self.verifier.calls[0][1]["purpose"], "INSTITUTIONAL_REGISTRATION")

    def test_unverified_registration_evidence_fails_before_any_record_or_event(self):
        self.verifier.rejected_refs.add("evidence:incorporation")
        with self.assertRaisesRegex(
            InstitutionalOnboardingError, "REGISTRATION_EVIDENCE_NOT_VERIFIED"):
            self.admin.register(
                tenant_id="tenant-a", resource_type="institution", version="1.0",
                event_id="evt-unverified", record=self.record(),
                actor_assertion=self.assertion(), evidence_ref="evidence:incorporation")
        self.assertIsNone(self.admin.get(
            tenant_id="tenant-a", resource_type="institution", resource_id="BANK-1"))
        self.assertEqual(self.admin.events(
            tenant_id="tenant-a", resource_type="institution", resource_id="BANK-1"), [])

    def test_registration_rejects_receipt_for_another_evidence_reference(self):
        class MismatchedVerifier:
            def verify(self, evidence_ref, *, context):
                return {"verified": True, "evidence_ref": "evidence:other",
                        "verification_ref": "verification:wrong",
                        "evidence_digest": "sha256:" + "a" * 64}
        admin = InstitutionalOnboardingAdministration(
            self.registry, self.admin.authority_resolver, MismatchedVerifier())
        with self.assertRaisesRegex(
            InstitutionalOnboardingError, "REGISTRATION_EVIDENCE_REFERENCE_MISMATCH"):
            admin.register(
                tenant_id="tenant-a", resource_type="institution", version="1.0",
                event_id="evt-mismatch", record=self.record(),
                actor_assertion=self.assertion(), evidence_ref="evidence:incorporation")
        self.assertIsNone(self.admin.get(
            tenant_id="tenant-a", resource_type="institution", resource_id="BANK-1"))

    def test_registration_is_tenant_scoped(self):
        with self.assertRaisesRegex(InstitutionalOnboardingError,"REGISTRATION_AUTHORITY_REJECTED"):
            self.admin.register(tenant_id="tenant-b",resource_type="institution",version="1.0",
                event_id="evt-cross-tenant",record=self.record(),
                actor_assertion=self.assertion(),evidence_ref="evidence:incorporation")
        self.assertIsNone(self.admin.get(tenant_id="tenant-b",resource_type="institution",resource_id="BANK-1"))

    def test_registration_requires_registrar_role(self):
        self.grant["roles"] = ["institution_verifier"]
        with self.assertRaisesRegex(InstitutionalOnboardingError,"REGISTRATION_ROLE_REQUIRED"):
            self.admin.register(tenant_id="tenant-a",resource_type="institution",version="1.0",
                event_id="evt-no-role",record=self.record(),
                actor_assertion=self.assertion(),evidence_ref="evidence:incorporation")
        self.assertIsNone(self.admin.get(tenant_id="tenant-a",resource_type="institution",resource_id="BANK-1"))

    def test_registration_cannot_start_in_active_or_verified_state(self):
        record = self.record()
        record["status"] = "VERIFIED"
        record["verification_ref"] = "approval:fake"
        with self.assertRaisesRegex(InstitutionalOnboardingError,"INVALID_INITIAL_LIFECYCLE_STATE"):
            self.admin.register(tenant_id="tenant-a",resource_type="institution",version="1.0",
                event_id="evt-direct-active",record=record,
                actor_assertion=self.assertion(),evidence_ref="evidence:incorporation")

    def test_replayed_assertion_nonce_is_rejected(self):
        args = dict(tenant_id="tenant-a",resource_type="institution",version="1.0",
            event_id="evt-register-1",record=self.record(),actor_assertion=self.assertion(),
            evidence_ref="evidence:incorporation")
        self.admin.register(**args)
        # Reuse is rejected at the authority boundary because the assertion nonce is single-use.
        with self.assertRaisesRegex(InstitutionalOnboardingError,"REGISTRATION_AUTHORITY_REJECTED"):
            self.admin.register(**args)

    def test_same_registration_event_is_idempotent_with_fresh_authority_assertion(self):
        first = self.admin.register(
            tenant_id="tenant-a", resource_type="institution", version="1.0",
            event_id="evt-idempotent", record=self.record(),
            actor_assertion=self.assertion(), evidence_ref="evidence:incorporation")
        second = self.admin.register(
            tenant_id="tenant-a", resource_type="institution", version="1.0",
            event_id="evt-idempotent", record=self.record(),
            actor_assertion=self.assertion(), evidence_ref="evidence:incorporation")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(len(self.admin.events(
            tenant_id="tenant-a", resource_type="institution", resource_id="BANK-1")), 1)

    def test_owned_resource_requires_existing_tenant_institution(self):
        source = {
            "source_ref": "law:ug:1", "source_type": "STATUTE",
            "title": "Example Act", "publisher_ref": "publisher:ug",
            "content_digest": "sha256:" + "a" * 64,
            "provenance_ref": "source:official-gazette",
            "effective_from": "2026-01-01", "effective_to": None,
            "status": "SUBMITTED", "approval_ref": None,
        }
        record = {
            "package_id": "UG-PACK-1", "version": "1.0",
            "owner_institution_id": "UNKNOWN-BANK", "jurisdiction_ref": "UG",
            "status": "DRAFT", "sources": [source], "approval_ref": None,
        }
        with self.assertRaisesRegex(InstitutionalOnboardingError,
                                    "RESOURCE_OWNER_INSTITUTION_NOT_REGISTERED"):
            self.admin.register(
                tenant_id="tenant-a", resource_type="jurisdiction", version="1.0",
                event_id="evt-owner-missing", record=record,
                actor_assertion=self.assertion(), evidence_ref="evidence:package")


if __name__ == "__main__":
    unittest.main()
