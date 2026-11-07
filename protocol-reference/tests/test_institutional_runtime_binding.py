# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import os
import tempfile
import time
import unittest

from institutional_registry import InstitutionalRegistry
from institutional_identity_provider_registry import InstitutionalIdentityProviderRegistry
from institutional_runtime_binding import (
    InstitutionalRuntimeBindingError, InstitutionalRuntimeBindingResolver,
)


class MinahRuntime:
    def __init__(self, config):
        self.config = config
        self.available = True

    def resolve_active(self, *, tenant_id, deployment_id):
        if not self.available:
            return None
        return {"deployment_id": deployment_id, "version": "9",
                "status": "ACTIVE", "config": dict(self.config)}


class InstitutionalRuntimeBindingTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.registry = InstitutionalRegistry(self.path)
        self.addCleanup(self.registry.close)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))
        self.idp_path = self.path + ".idp"
        self.identity_provider_registry = InstitutionalIdentityProviderRegistry(self.idp_path)
        self.addCleanup(self.identity_provider_registry.close)
        self.addCleanup(lambda: os.path.exists(self.idp_path) and os.unlink(self.idp_path))
        self.tenant = "tenant-ug"
        self.institution_id = "BANK-UG-01"
        self.binding_id = "BIND-UG-01"
        self.config = {
            "deployment_id": "MINAH-UG-01", "version": "9", "status": "ACTIVE",
            "owner_ref": self.tenant, "model_ref": "model:bank/minah-v1",
            "interface_version": "minah-review-0.1",
            "jurisdictions": ["UG"],
            "jurisdiction_package_id": "UG-PACK-01",
            "jurisdiction_package_version": "4",
            "policy_pack_version": "policy-3", "source_pack_version": "sources-4",
            "authority_profile_version": "authority-2",
        }
        self.runtime = MinahRuntime(self.config)
        self.resolver = InstitutionalRuntimeBindingResolver(
            registry=self.registry,
            minah_deployment_administration=self.runtime,
            identity_provider_registry=self.identity_provider_registry,
        )
        self._seed()

    def _seed(self):
        self.registry.create_record(
            tenant_id=self.tenant, resource_type="institution",
            resource_id=self.institution_id, version="2",
            status="VERIFIED", payload={
                "institution_id": self.institution_id, "legal_name": "Bank Uganda",
                "jurisdiction_ref": "UG", "status": "VERIFIED",
                "verification_ref": "verification:institution",
            })
        self.registry.create_record(
            tenant_id=self.tenant, resource_type="institution",
            resource_id="OTHER-BANK", version="1",
            status="VERIFIED", payload={
                "institution_id": "OTHER-BANK", "legal_name": "Other Bank",
                "jurisdiction_ref": "UG", "status": "VERIFIED",
                "verification_ref": "verification:other",
            })
        self.registry.create_record(
            tenant_id=self.tenant, resource_type="jurisdiction",
            resource_id="UG-PACK-01", version="4", status="ACTIVE", payload={
                "package_id": "UG-PACK-01", "version": "4",
                "owner_institution_id": self.institution_id,
                "jurisdiction_ref": "UG", "status": "ACTIVE",
                "sources": [{"source_ref": "law:ug:1", "source_type": "STATUTE",
                    "title": "Example Act", "publisher_ref": "publisher:ug",
                    "content_digest": "sha256:" + "a" * 64,
                    "provenance_ref": "evidence:law", "effective_from": "2026-01-01",
                    "status": "APPROVED", "approval_ref": "approval:law"}],
                "approval_ref": "approval:package",
            })
        self.registry.create_record(
            tenant_id=self.tenant, resource_type="minah",
            resource_id="MINAH-UG-01", version="5", status="ACTIVE", payload={
                "deployment_id": "MINAH-UG-01", "owner_institution_id": self.institution_id,
                "adapter_mode": "EXTERNAL_SERVICE", "adapter_ref": "adapter:bank",
                "model_ref": "model:bank/minah-v1",
                "interface_version": "minah-review-0.1", "status": "ACTIVE",
            })
        now = int(time.time())
        provider_config = {
            "tenant_id": self.tenant, "provider_id": "IDP-UG-01", "version": "1",
            "protocol": "OIDC", "issuer": "https://idp.bank.example",
            "client_id": "kin-sys-client", "status": "PENDING",
            "allowed_hosts": ["idp.bank.example"],
            "allowed_algorithms": ["RS256"], "audiences": ["kin-sys-client"],
            "allowed_acr": ["urn:bank:loa:2"], "required_amr": ["pwd"],
            "valid_from": now - 60, "valid_until": now + 3600,
            "cache_ttl_seconds": 60,
        }
        self.identity_provider_registry.register_provider(
            tenant_id=self.tenant, config=provider_config,
            event_id="IDP-EVENT-REGISTER", actor_ref="IDP-REGISTRAR",
            evidence_ref="evidence:idp-registration",
        )
        self.identity_provider_registry.transition_provider(
            tenant_id=self.tenant, provider_id="IDP-UG-01",
            expected_version="1", new_version="2", new_status="ACTIVE",
            event_id="IDP-EVENT-ACTIVE", actor_ref="IDP-APPROVER",
            evidence_ref="evidence:idp-approval",
        )
        self.registry.create_record(
            tenant_id=self.tenant, resource_type="binding",
            resource_id=self.binding_id, version="3", status="ACTIVE", payload={
                "binding_id": self.binding_id, "owner_institution_id": self.institution_id,
                "jurisdiction_package_id": "UG-PACK-01",
                "jurisdiction_package_version": "4",
                "minah_deployment_id": "MINAH-UG-01",
                "identity_provider_id": "IDP-UG-01", "identity_provider_version": "2",
                "policy_pack_version": "policy-3", "source_pack_version": "sources-4",
                "authority_profile_version": "authority-2",
                "status": "ACTIVE", "approval_ref": "approval:binding",
            })

    def resolve(self):
        return self.resolver.resolve(
            tenant_id=self.tenant, institution_id=self.institution_id,
            binding_id=self.binding_id)

    def test_resolves_exact_active_institutional_runtime_context(self):
        result = self.resolve()
        self.assertEqual(result["runtime_admission"], "ELIGIBLE_FOR_GOVERNED_EXECUTION")
        self.assertEqual(result["execution_order"],
                         ["MINAH", "IDENTITY", "AUTHORITY", "CEE", "FINAL_DECISION"])
        self.assertEqual(result["binding_version"], "3")
        self.assertEqual(result["jurisdiction_package_version"], "4")
        self.assertEqual(result["identity_provider_id"], "IDP-UG-01")
        self.assertEqual(result["identity_provider_version"], "2")

    def test_inactive_identity_provider_fails_closed(self):
        self.identity_provider_registry.transition_provider(
            tenant_id=self.tenant, provider_id="IDP-UG-01",
            expected_version="2", new_version="3", new_status="SUSPENDED",
            event_id="IDP-EVENT-SUSPEND", actor_ref="IDP-SUSPENDER",
            evidence_ref="evidence:idp-suspension",
        )
        with self.assertRaisesRegex(
            InstitutionalRuntimeBindingError, "RUNTIME_IDENTITY_PROVIDER_NOT_ACTIVE"):
            self.resolve()

    def test_stale_identity_provider_version_fails_closed(self):
        self.registry.create_record(
            tenant_id=self.tenant, resource_type="binding",
            resource_id=self.binding_id, version="4", status="ACTIVE", payload={
                "binding_id": self.binding_id, "owner_institution_id": self.institution_id,
                "jurisdiction_package_id": "UG-PACK-01",
                "jurisdiction_package_version": "4",
                "minah_deployment_id": "MINAH-UG-01",
                "identity_provider_id": "IDP-UG-01", "identity_provider_version": "1",
                "policy_pack_version": "policy-3", "source_pack_version": "sources-4",
                "authority_profile_version": "authority-2",
                "status": "ACTIVE", "approval_ref": "approval:binding",
            })
        with self.assertRaisesRegex(
            InstitutionalRuntimeBindingError, "RUNTIME_IDENTITY_PROVIDER_VERSION_STALE"):
            self.resolve()

    def test_suspended_binding_fails_closed(self):
        self.registry.create_record(
            tenant_id=self.tenant, resource_type="binding",
            resource_id=self.binding_id, version="4", status="SUSPENDED", payload={
                "binding_id": self.binding_id, "owner_institution_id": self.institution_id,
                "jurisdiction_package_id": "UG-PACK-01",
                "jurisdiction_package_version": "4",
                "minah_deployment_id": "MINAH-UG-01",
                "identity_provider_id": "IDP-UG-01", "identity_provider_version": "2",
                "policy_pack_version": "policy-3", "source_pack_version": "sources-4",
                "authority_profile_version": "authority-2",
                "status": "SUSPENDED", "approval_ref": "approval:binding",
            })
        with self.assertRaisesRegex(
            InstitutionalRuntimeBindingError, "RUNTIME_BINDING_NOT_ACTIVE"):
            self.resolve()

    def test_suspended_minah_fails_closed(self):
        self.runtime.available = False
        with self.assertRaisesRegex(
            InstitutionalRuntimeBindingError, "RUNTIME_MINAH_DEPLOYMENT_UNAVAILABLE"):
            self.resolve()

    def test_minah_must_declare_the_bound_jurisdiction(self):
        self.runtime.config["jurisdictions"] = ["KE"]
        with self.assertRaisesRegex(
            InstitutionalRuntimeBindingError,
            "RUNTIME_CONFIGURATION_PIN_MISMATCH:jurisdictions",
        ):
            self.resolve()

    def test_policy_pin_mismatch_fails_closed(self):
        self.runtime.config["policy_pack_version"] = "policy-new"
        with self.assertRaisesRegex(
            InstitutionalRuntimeBindingError, "RUNTIME_CONFIGURATION_PIN_MISMATCH:policy_pack_version"):
            self.resolve()

    def test_stale_jurisdiction_version_fails_closed(self):
        self.registry.create_record(
            tenant_id=self.tenant, resource_type="jurisdiction",
            resource_id="UG-PACK-01", version="5", status="ACTIVE", payload={
                "package_id": "UG-PACK-01", "version": "5",
                "owner_institution_id": self.institution_id,
                "jurisdiction_ref": "UG", "status": "ACTIVE",
                "sources": [{"source_ref": "law:ug:1", "source_type": "STATUTE",
                    "title": "Example Act", "publisher_ref": "publisher:ug",
                    "content_digest": "sha256:" + "a" * 64,
                    "provenance_ref": "evidence:law", "effective_from": "2026-01-01",
                    "status": "APPROVED", "approval_ref": "approval:law"}],
                "approval_ref": "approval:package",
            })
        with self.assertRaisesRegex(
            InstitutionalRuntimeBindingError, "RUNTIME_JURISDICTION_VERSION_STALE"):
            self.resolve()

    def test_cross_institution_binding_fails_closed(self):
        # The record is tenant-scoped but the caller cannot substitute another owner.
        with self.assertRaisesRegex(
            InstitutionalRuntimeBindingError, "RUNTIME_BINDING_OWNER_MISMATCH"):
            self.resolver.resolve(
                tenant_id=self.tenant, institution_id="OTHER-BANK",
                binding_id=self.binding_id)

    def test_tenant_isolation_fails_closed(self):
        with self.assertRaisesRegex(
            InstitutionalRuntimeBindingError, "RUNTIME_DEPENDENCY_NOT_FOUND:institution"):
            self.resolver.resolve(
                tenant_id="tenant-other", institution_id=self.institution_id,
                binding_id=self.binding_id)


if __name__ == "__main__":
    unittest.main()
