# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import os
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from institutional_authority import (
    InMemoryNonceGuard, InstitutionalAuthorityResolver, sign_actor_claims,
)
from institutional_lifecycle_administration import (
    InstitutionalLifecycleAdministration, InstitutionalLifecycleAdministrationError,
)
from institutional_registry import InstitutionalRegistry


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
        return {
            "verified": True,
            "evidence_ref": evidence_ref,
            "verification_ref": "verification:" + digest[:16],
            "evidence_digest": "sha256:" + digest,
        }


class InstitutionalLifecycleAdministrationTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        self.registry = InstitutionalRegistry(self.path)
        self.addCleanup(self.registry.close)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))
        self.key = b"lifecycle-administration-test-key-32-bytes"
        self.grant = {
            "grant_id": "GRANT-LIFECYCLE-01", "tenant_id": "tenant-lifecycle",
            "status": "ACTIVE", "actor_ref": "actor:external-verifier",
            "institution_id": "REGULATOR-UG", "authority_ref": "authority:verification",
            "roles": ["institution_verifier"],
            "resource_types": ["institution"], "resource_ids": ["BANK-LIFE-01"],
            "valid_from": 900, "valid_until": 1100, "version": "1",
            "issuer_institution_id": "NATIONAL-REGISTRY",
            "approval_ref": "approval:grant",
        }
        self.nonces = InMemoryNonceGuard()
        self.resolver = InstitutionalAuthorityResolver(
            verification_keys={"lifecycle-key": self.key},
            grant_loader=lambda tenant, grant: (
                self.grant if tenant == "tenant-lifecycle"
                and grant == "GRANT-LIFECYCLE-01" else None
            ),
            consume_nonce=self.nonces.consume, clock=lambda: 1000,
        )
        self.verifier = EvidenceVerifier()
        self.administration = InstitutionalLifecycleAdministration(
            self.registry, self.resolver, self.verifier)
        self.registry.create_record(
            tenant_id="tenant-lifecycle", resource_type="institution",
            resource_id="BANK-LIFE-01", version="1.0",
            status="PENDING_VERIFICATION",
            payload={
                "institution_id": "BANK-LIFE-01", "legal_name": "Lifecycle Bank",
                "jurisdiction_ref": "UG", "status": "PENDING_VERIFICATION",
                "verification_ref": None,
            },
        )
        self.nonce_counter = 0

    def assertion(self):
        self.nonce_counter += 1
        claims = {
            "key_id": "lifecycle-key", "tenant_id": "tenant-lifecycle",
            "actor_ref": "actor:external-verifier", "grant_id": "GRANT-LIFECYCLE-01",
            "grant_version": "1", "issued_at": 990, "expires_at": 1010,
            "nonce": f"lifecycle-admin-{self.nonce_counter:08d}",
        }
        return {"claims": claims, "signature": sign_actor_claims(claims, self.key)}

    def transition(self, **overrides):
        args = {
            "tenant_id": "tenant-lifecycle", "resource_type": "institution",
            "resource_id": "BANK-LIFE-01", "expected_version": "1.0",
            "new_version": "2.0", "event_id": "evt-lifecycle-verify",
            "target_status": "VERIFIED", "actor_assertion": self.assertion(),
            "evidence_ref": "evidence:registry-review",
            "approval_ref": "approval:verification",
        }
        args.update(overrides)
        return self.administration.transition(**args)

    def test_verified_evidence_and_approval_are_recorded_atomically(self):
        result = self.transition()
        self.assertTrue(result["created"])
        self.assertEqual(len(self.verifier.calls), 2)
        events = self.registry.list_events(
            tenant_id="tenant-lifecycle", resource_type="institution",
            resource_id="BANK-LIFE-01")
        self.assertEqual(len(events), 1)
        receipt = events[0]["payload"]["evidence_verification"]
        self.assertEqual(set(receipt), {"evidence", "approval"})
        self.assertTrue(receipt["evidence"]["verification_ref"].startswith("verification:"))
        self.assertTrue(receipt["approval"]["evidence_digest"].startswith("sha256:"))

    def test_unverified_evidence_fails_closed_without_snapshot_or_event(self):
        self.verifier.rejected_refs.add("evidence:registry-review")
        with self.assertRaisesRegex(
            InstitutionalLifecycleAdministrationError, "EVIDENCE_NOT_VERIFIED"):
            self.transition()
        self.assertIsNone(self.registry.get_record(
            tenant_id="tenant-lifecycle", resource_type="institution",
            resource_id="BANK-LIFE-01", version="2.0"))
        self.assertEqual(self.registry.list_events(
            tenant_id="tenant-lifecycle", resource_type="institution",
            resource_id="BANK-LIFE-01"), [])

    def test_verifier_cannot_return_receipt_for_different_evidence(self):
        class MismatchedVerifier:
            def verify(self, evidence_ref, *, context):
                return {
                    "verified": True, "evidence_ref": "evidence:other",
                    "verification_ref": "verification:wrong",
                    "evidence_digest": "sha256:" + "a" * 64,
                }
        admin = InstitutionalLifecycleAdministration(
            self.registry, self.resolver, MismatchedVerifier())
        with self.assertRaisesRegex(
            InstitutionalLifecycleAdministrationError, "EVIDENCE_REFERENCE_MISMATCH"):
            admin.transition(
                tenant_id="tenant-lifecycle", resource_type="institution",
                resource_id="BANK-LIFE-01", expected_version="1.0",
                new_version="2.0", event_id="evt-mismatch",
                target_status="VERIFIED", actor_assertion=self.assertion(),
                evidence_ref="evidence:registry-review",
                approval_ref="approval:verification")
        self.assertEqual(self.registry.list_events(
            tenant_id="tenant-lifecycle", resource_type="institution",
            resource_id="BANK-LIFE-01"), [])


if __name__ == "__main__":
    unittest.main()
