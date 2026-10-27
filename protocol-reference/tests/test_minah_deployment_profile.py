# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Tests for the provider-neutral MINAH deployment profile contract."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from minah_deployment_profile import (
    PROFILE_VERSION,
    MinahProfileViolation,
    validate_minah_profile,
)


def profile(**overrides):
    value = {
        "profile_version": PROFILE_VERSION,
        "deployment_id": "ug-national-minah-01",
        "owner_scope": "NATIONAL_SOVEREIGN",
        "owner_ref": "UG-INSTITUTION-ROOT",
        "use_case": "PUBLIC_REVENUE_CLEARANCE",
        "jurisdictions": ["UG"],
        "policy_pack_version": "UG-REVENUE-POLICY-3",
        "source_pack_version": "UG-LEGAL-SOURCES-2026-10",
        "model_ref": "institution-selected-model-v2",
        "deployment_mode": "ON_PREMISES",
        "may_authorise": False,
    }
    value.update(overrides)
    return value


class MinahDeploymentProfileTests(unittest.TestCase):
    def test_accepts_country_owned_profile(self):
        result = validate_minah_profile(profile())
        self.assertEqual(result["owner_scope"], "NATIONAL_SOVEREIGN")
        self.assertEqual(result["deployment_id"], "ug-national-minah-01")

    def test_accepts_separate_enterprise_profile(self):
        result = validate_minah_profile(profile(
            deployment_id="enterprise-bank-minah-04",
            owner_scope="ENTERPRISE",
            owner_ref="BANK-LEGAL-ENTITY-44",
            use_case="CORPORATE_TREASURY_APPROVAL",
            jurisdictions=["UG", "KE"],
            policy_pack_version="BANK-POLICY-8",
            source_pack_version="BANK-COMPLIANCE-SOURCES-12",
            deployment_mode="CLOUD",
        ))
        self.assertEqual(result["owner_scope"], "ENTERPRISE")
        self.assertEqual(result["jurisdictions"], ["UG", "KE"])

    def test_rejects_missing_deployment_owner(self):
        value = profile()
        del value["owner_ref"]
        with self.assertRaisesRegex(MinahProfileViolation, "MISSING_REQUIRED_FIELDS"):
            validate_minah_profile(value)

    def test_rejects_duplicate_or_empty_jurisdictions(self):
        for jurisdictions in ([], ["UG", "UG"], [""]):
            with self.subTest(jurisdictions=jurisdictions):
                with self.assertRaisesRegex(MinahProfileViolation, "INVALID_JURISDICTIONS"):
                    validate_minah_profile(profile(jurisdictions=jurisdictions))

    def test_minah_profile_cannot_grant_institutional_authority(self):
        with self.assertRaisesRegex(
            MinahProfileViolation, "MINAH_MUST_NOT_GRANT_INSTITUTIONAL_AUTHORITY"
        ):
            validate_minah_profile(profile(may_authorise=True))

    def test_rejects_unknown_scope_and_deployment_mode(self):
        with self.assertRaisesRegex(MinahProfileViolation, "INVALID_OWNER_SCOPE"):
            validate_minah_profile(profile(owner_scope="GLOBAL_SHARED"))
        with self.assertRaisesRegex(MinahProfileViolation, "INVALID_DEPLOYMENT_MODE"):
            validate_minah_profile(profile(deployment_mode="MAGIC"))

    def test_returns_copy_without_mutating_input(self):
        original = profile()
        result = validate_minah_profile(original)
        result["jurisdictions"].append("KE")
        self.assertEqual(original["jurisdictions"], ["UG"])


if __name__ == "__main__":
    unittest.main()
