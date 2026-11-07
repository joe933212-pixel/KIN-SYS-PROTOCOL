import unittest

from institutional_deployment_domain import (
    DomainModelViolation,
    validate_activation,
    validate_jurisdiction_package,
    validate_minah_deployment,
    validate_source,
    validate_institution,
)

def source(status="APPROVED", approval_ref="approval:source-1"):
    return {
        "source_ref": "UG-LAW-1", "source_type": "STATUTE", "title": "Demonstration Act",
        "publisher_ref": "publisher:ug", "content_digest": "sha256:" + "a" * 64,
        "provenance_ref": "evidence:origin-1", "effective_from": "2026-01-01",
        "status": status, "approval_ref": approval_ref,
    }

def records(owner="UG-INST-001"):
    institution = {
        "institution_id": owner, "legal_name": "Demonstration Institution",
        "jurisdiction_ref": "UG", "status": "VERIFIED", "verification_ref": "verify:1",
    }
    jurisdiction = {
        "package_id": "UG-PACK", "version": "1.0.0", "owner_institution_id": owner,
        "jurisdiction_ref": "UG", "status": "APPROVED", "sources": [source()],
        "approval_ref": "approval:pack-1",
    }
    minah = {
        "deployment_id": "UG-MINAH-1", "owner_institution_id": owner,
        "adapter_mode": "EXTERNAL_SERVICE", "adapter_ref": "adapter:ug-1",
        "model_ref": "model:provider/model-v1", "interface_version": "minah-review-0.1",
        "status": "CONFORMANCE_PASSED",
    }
    binding = {
        "binding_id": "UG-BINDING-1", "owner_institution_id": owner,
        "jurisdiction_package_id": "UG-PACK", "jurisdiction_package_version": "1.0.0",
        "minah_deployment_id": "UG-MINAH-1", "identity_provider_id": "IDP-UG-01", "identity_provider_version": "2", "policy_pack_version": "policy-1",
        "source_pack_version": "sources-1", "authority_profile_version": "authority-1",
        "status": "APPROVED", "approval_ref": "approval:binding-1",
    }
    return institution, jurisdiction, minah, binding

class InstitutionalDomainTests(unittest.TestCase):
    def test_valid_records_are_activation_eligible(self):
        result = validate_activation(*records(), conformance_evidence_ref="evidence:conformance-1")
        self.assertTrue(result["activation_eligible"])
        self.assertEqual(result["jurisdiction_package"], "UG-PACK@1.0.0")

    def test_uploaded_source_is_not_authoritative_without_approval(self):
        package = records()[1]
        package["sources"] = [source(status="SUBMITTED", approval_ref=None)]
        with self.assertRaisesRegex(DomainModelViolation, "ACTIVE_JURISDICTION_REQUIRES_APPROVED_SOURCES"):
            validate_jurisdiction_package(package)

    def test_approved_source_requires_approval_evidence(self):
        item = source(approval_ref=None)
        with self.assertRaisesRegex(DomainModelViolation, "APPROVED_SOURCE_REQUIRES_APPROVAL_EVIDENCE"):
            from institutional_deployment_domain import validate_source
            validate_source(item)

    def test_cross_tenant_minah_is_rejected(self):
        institution, jurisdiction, minah, binding = records()
        minah["owner_institution_id"] = "OTHER-INSTITUTION"
        with self.assertRaisesRegex(DomainModelViolation, "CROSS_TENANT_OWNERSHIP_MISMATCH"):
            validate_activation(institution, jurisdiction, minah, binding, conformance_evidence_ref="evidence:1")

    def test_binding_must_pin_exact_package_version(self):
        institution, jurisdiction, minah, binding = records()
        binding["jurisdiction_package_version"] = "latest"
        with self.assertRaisesRegex(DomainModelViolation, "JURISDICTION_BINDING_VERSION_MISMATCH"):
            validate_activation(institution, jurisdiction, minah, binding, conformance_evidence_ref="evidence:1")

    def test_minah_domain_record_rejects_secret_material(self):
        item = records()[2]
        item["api_key"] = "must-not-be-here"
        with self.assertRaisesRegex(DomainModelViolation, "UNSUPPORTED_MINAH_FIELDS"):
            validate_minah_deployment(item)

    def test_python_validator_rejects_unknown_fields(self):
        item = records()[0]
        item["skip_verification"] = True
        with self.assertRaisesRegex(DomainModelViolation, "UNSUPPORTED_INSTITUTION_FIELDS"):
            validate_institution(item)

    def test_source_digest_requires_full_sha256(self):
        item = source()
        item["content_digest"] = "sha256:abcd"
        with self.assertRaisesRegex(DomainModelViolation, "SOURCE_REQUIRES_SHA256_CONTENT_DIGEST"):
            validate_source(item)

    def test_activation_requires_conformance_evidence(self):
        with self.assertRaisesRegex(DomainModelViolation, "CONFORMANCE_EVIDENCE_REQUIRED"):
            validate_activation(*records(), conformance_evidence_ref=None)

if __name__ == "__main__":
    unittest.main()
