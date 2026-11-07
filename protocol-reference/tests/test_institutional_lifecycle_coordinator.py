# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import os
import tempfile
import unittest

from institutional_authority import (
    InMemoryNonceGuard, InstitutionalAuthorityResolver, sign_actor_claims,
)
from institutional_lifecycle_coordinator import (
    InstitutionalLifecycleCoordinator, LifecycleCoordinatorError,
)
from institutional_registry import InstitutionalRegistry


class InstitutionalLifecycleCoordinatorTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        self.registry = InstitutionalRegistry(self.path)
        self.addCleanup(self.registry.close)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))
        self.key = b"reference-test-signing-key-32-bytes-minimum"
        self.grant = {
            "grant_id": "GRANT-REGISTRAR-01",
            "tenant_id": "tenant-a",
            "status": "ACTIVE",
            "actor_ref": "actor:registrar",
            "institution_id": "REGULATOR-UG",
            "authority_ref": "authority:institution-register",
            "roles": ["institution_verifier", "binding_activation_authority"],
            "resource_types": ["institution", "binding"],
            "resource_ids": ["BANK-UG-01", "BIND-1", "BIND-2"],
            "valid_from": 900,
            "valid_until": 1100,
            "version": "3",
            "issuer_institution_id": "NATIONAL-REGISTRY",
            "approval_ref": "approval:grant-3",
        }
        self.nonces = InMemoryNonceGuard()
        self.resolver = InstitutionalAuthorityResolver(
            verification_keys={"issuer-key-1": self.key},
            grant_loader=lambda tenant, grant_id: (
                self.grant if tenant == self.grant["tenant_id"]
                and grant_id == self.grant["grant_id"] else None
            ),
            consume_nonce=self.nonces.consume,
            clock=lambda: 1000,
        )
        self.coordinator = InstitutionalLifecycleCoordinator(self.registry, self.resolver)
        self.institution = {
            "institution_id": "BANK-UG-01",
            "legal_name": "Example Bank",
            "jurisdiction_ref": "UG",
            "status": "PENDING_VERIFICATION",
            "verification_ref": None,
        }
        self.registry.create_record(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01", version="1.0",
            status="PENDING_VERIFICATION", payload=self.institution,
        )
        self.nonce_counter = 0

    def assertion(self, *, issued_at=990, expires_at=1010, tenant_id="tenant-a",
                  grant_version="3", grant_id="GRANT-REGISTRAR-01",
                  actor_ref="actor:registrar"):
        self.nonce_counter += 1
        claims = {
            "key_id": "issuer-key-1",
            "tenant_id": tenant_id,
            "actor_ref": actor_ref,
            "grant_id": grant_id,
            "grant_version": grant_version,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "nonce": f"nonce-test-{self.nonce_counter:08d}",
        }
        return {"claims": claims, "signature": sign_actor_claims(claims, self.key)}

    def call_transition(self, **overrides):
        args = dict(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01", expected_version="1.0",
            new_version="2.0", event_id="evt-verify-1",
            target_status="VERIFIED", actor_assertion=self.assertion(),
            evidence_ref="evidence:registry-check",
            approval_ref="approval:verify-1",
        )
        args.update(overrides)
        return self.coordinator.transition(**args)

    def test_authorised_transition_persists_snapshot_and_event(self):
        result = self.call_transition()
        self.assertTrue(result["created"])
        snapshot = self.registry.get_record(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01", version="2.0",
        )
        self.assertEqual(snapshot["status"], "VERIFIED")
        self.assertEqual(snapshot["payload"]["verification_ref"], "approval:verify-1")
        events = self.registry.list_events(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "INSTITUTION_VERIFIED")

    def test_wrong_granted_role_fails_without_writing(self):
        self.grant["roles"] = ["institution_submitter"]
        with self.assertRaisesRegex(ValueError, "REQUIRED_AUTHORITY_ROLE_MISSING"):
            self.call_transition()
        self.assertIsNone(self.registry.get_record(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01", version="2.0",
        ))
        self.assertEqual(self.registry.list_events(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01",
        ), [])

    def test_tenant_context_does_not_allow_cross_tenant_read_or_write(self):
        with self.assertRaisesRegex(LifecycleCoordinatorError, "CURRENT_RECORD_VERSION_NOT_FOUND"):
            self.call_transition(tenant_id="tenant-b")
        self.assertEqual(self.registry.list_events(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01",
        ), [])

    def test_stale_expected_version_fails_without_partial_write(self):
        self.call_transition()
        with self.assertRaisesRegex(ValueError, "STALE_EXPECTED_VERSION"):
            self.call_transition(new_version="3.0", event_id="evt-stale")
        self.assertIsNone(self.registry.get_record(
            tenant_id="tenant-a", resource_type="institution",
            resource_id="BANK-UG-01", version="3.0",
        ))

    def test_binding_activation_requires_complete_activation_context(self):
        binding = {
            "binding_id": "BIND-1",
            "owner_institution_id": "BANK-UG-01",
            "jurisdiction_package_id": "UG-PACK",
            "jurisdiction_package_version": "1.0",
            "minah_deployment_id": "MINAH-1",
            "policy_pack_version": "1.0",
            "source_pack_version": "1.0",
            "authority_profile_version": "1.0",
            "status": "APPROVED",
            "approval_ref": "approval:binding",
        }
        self.registry.create_record(
            tenant_id="tenant-a", resource_type="binding",
            resource_id="BIND-1", version="1.0", status="APPROVED",
            payload=binding,
        )
        with self.assertRaisesRegex(LifecycleCoordinatorError, "ACTIVATION_CONTEXT_REQUIRED"):
            self.coordinator.transition(
                tenant_id="tenant-a", resource_type="binding",
                resource_id="BIND-1", expected_version="1.0",
                new_version="2.0", event_id="evt-activate",
                target_status="ACTIVE", actor_assertion=self.assertion(),
                evidence_ref="evidence:activation",
                approval_ref="approval:activation",
            )
        self.assertIsNone(self.registry.get_record(
            tenant_id="tenant-a", resource_type="binding",
            resource_id="BIND-1", version="2.0",
        ))

    def test_binding_activation_loads_exact_related_records_from_tenant_registry(self):
        institution = {
            "institution_id": "BANK-UG-01", "legal_name": "Example Bank",
            "jurisdiction_ref": "UG", "status": "VERIFIED",
            "verification_ref": "approval:institution",
        }
        jurisdiction = {
            "package_id": "UG-PACK", "version": "1.0",
            "owner_institution_id": "BANK-UG-01", "jurisdiction_ref": "UG",
            "status": "APPROVED", "approval_ref": "approval:jurisdiction",
            "sources": [{
                "source_ref": "source:law-1", "source_type": "STATUTE",
                "title": "Example statute", "publisher_ref": "publisher:ug",
                "content_digest": "sha256:" + "a" * 64,
                "provenance_ref": "provenance:law-1", "effective_from": "2026-01-01",
                "effective_to": None, "status": "APPROVED",
                "approval_ref": "approval:source-1",
            }],
        }
        minah = {
            "deployment_id": "MINAH-1", "owner_institution_id": "BANK-UG-01",
            "adapter_mode": "NATIVE", "adapter_ref": "adapter:minah-1",
            "model_ref": "model:minah-v1", "interface_version": "0.1",
            "status": "CONFORMANCE_PASSED",
        }
        binding = {
            "binding_id": "BIND-1", "owner_institution_id": "BANK-UG-01",
            "jurisdiction_package_id": "UG-PACK",
            "jurisdiction_package_version": "1.0",
            "minah_deployment_id": "MINAH-1", "policy_pack_version": "1.0",
            "source_pack_version": "1.0", "authority_profile_version": "1.0",
            "status": "APPROVED", "approval_ref": "approval:binding",
        }
        for kind, resource_id, version, payload in (
            ("institution", "BANK-UG-01", "2.0", institution),
            ("jurisdiction", "UG-PACK", "1.0", jurisdiction),
            ("minah", "MINAH-1", "1.0", minah),
            ("binding", "BIND-1", "1.0", binding),
        ):
            self.registry.create_record(tenant_id="tenant-a", resource_type=kind,
                resource_id=resource_id, version=version, status=payload["status"],
                payload=payload)
        result = self.coordinator.transition(
            tenant_id="tenant-a", resource_type="binding", resource_id="BIND-1",
            expected_version="1.0", new_version="2.0", event_id="evt-binding-active",
            target_status="ACTIVE", actor_assertion=self.assertion(),
            evidence_ref="evidence:activation", approval_ref="approval:activation",
            activation_context={"institution_version": "1.0", "minah_version": "1.0",
                                "conformance_evidence_ref": "evidence:minah-conformance"},
        )
        self.assertTrue(result["created"])
        active = self.registry.get_record(tenant_id="tenant-a", resource_type="binding",
            resource_id="BIND-1", version="2.0")
        self.assertEqual(active["status"], "ACTIVE")

    def test_binding_activation_rejects_caller_supplied_domain_records(self):
        binding = {
            "binding_id": "BIND-2", "owner_institution_id": "BANK-UG-01",
            "jurisdiction_package_id": "UG-PACK",
            "jurisdiction_package_version": "1.0",
            "minah_deployment_id": "MINAH-1", "policy_pack_version": "1.0",
            "source_pack_version": "1.0", "authority_profile_version": "1.0",
            "status": "APPROVED", "approval_ref": "approval:binding",
        }
        self.registry.create_record(tenant_id="tenant-a", resource_type="binding",
            resource_id="BIND-2", version="1.0", status="APPROVED", payload=binding)
        with self.assertRaisesRegex(LifecycleCoordinatorError, "INVALID_ACTIVATION_CONTEXT"):
            self.coordinator.transition(
                tenant_id="tenant-a", resource_type="binding", resource_id="BIND-2",
                expected_version="1.0", new_version="2.0", event_id="evt-forged-context",
                target_status="ACTIVE", actor_assertion=self.assertion(),
                evidence_ref="evidence:activation", approval_ref="approval:activation",
                activation_context={"institution": {"status": "VERIFIED"},
                    "jurisdiction": {"status": "APPROVED"}, "minah": {"status": "CONFORMANCE_PASSED"},
                    "conformance_evidence_ref": "evidence:forged"},
            )
        self.assertIsNone(self.registry.get_record(tenant_id="tenant-a",
            resource_type="binding", resource_id="BIND-2", version="2.0"))

    def test_invalid_signature_is_rejected(self):
        assertion = self.assertion()
        assertion["claims"]["actor_ref"] = "actor:attacker"
        with self.assertRaisesRegex(ValueError, "ASSERTION_SIGNATURE_INVALID"):
            self.call_transition(actor_assertion=assertion)

    def test_expired_assertion_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "ASSERTION_EXPIRED_OR_NOT_YET_VALID"):
            self.call_transition(actor_assertion=self.assertion(issued_at=900, expires_at=999))

    def test_assertion_cannot_be_replayed(self):
        assertion = self.assertion()
        self.call_transition(actor_assertion=assertion)
        with self.assertRaisesRegex(ValueError, "ASSERTION_REPLAY_DETECTED"):
            self.coordinator.transition(
                tenant_id="tenant-a", resource_type="institution",
                resource_id="BANK-UG-01", expected_version="2.0",
                new_version="3.0", event_id="evt-replay",
                target_status="SUSPENDED", actor_assertion=assertion,
                evidence_ref="evidence:suspension",
            )

    def test_assertion_is_bound_to_exact_authority_grant_version(self):
        with self.assertRaisesRegex(ValueError, "AUTHORITY_GRANT_SCOPE_MISMATCH"):
            self.call_transition(actor_assertion=self.assertion(grant_version="2"))

    def test_grant_must_cover_requested_resource(self):
        self.grant["resource_ids"] = ["OTHER-INSTITUTION"]
        with self.assertRaisesRegex(ValueError, "AUTHORITY_RESOURCE_ID_OUT_OF_SCOPE"):
            self.call_transition()

    def test_revoked_grant_cannot_authorise_transition(self):
        self.grant["status"] = "REVOKED"
        with self.assertRaisesRegex(ValueError, "AUTHORITY_GRANT_NOT_ACTIVE"):
            self.call_transition()


if __name__ == "__main__":
    unittest.main()
