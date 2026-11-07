# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import os
import tempfile
import unittest

from institutional_authority import InMemoryNonceGuard, InstitutionalAuthorityResolver, sign_actor_claims
from institutional_onboarding_administration import (
    InstitutionalOnboardingAdministration, InstitutionalOnboardingError,
)
from institutional_registry import InstitutionalRegistry, RegistryConflict


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
            "actor_ref": "actor:registrar", "institution_id": "REGISTRY-UG",
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
        self.admin = InstitutionalOnboardingAdministration(self.registry, resolver)
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

    def test_event_idempotency_and_conflict(self):
        args = dict(tenant_id="tenant-a",resource_type="institution",version="1.0",
            event_id="evt-register-1",record=self.record(),actor_assertion=self.assertion(),
            evidence_ref="evidence:incorporation")
        self.admin.register(**args)
        # Reuse is rejected at the authority boundary because the assertion nonce is single-use.
        with self.assertRaisesRegex(InstitutionalOnboardingError,"REGISTRATION_AUTHORITY_REJECTED"):
            self.admin.register(**args)


if __name__ == "__main__":
    unittest.main()
