# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import unittest
from deployment_profiles import CORE_PROTOCOL_OBLIGATIONS, DEPLOYMENT_PROFILE_CONTRACT_VERSION, deployment_profiles, validate_deployment_profiles

class DeploymentProfileTests(unittest.TestCase):
    def test_all_profiles_share_core_and_require_no_custom_hardware(self):
        model = deployment_profiles()
        self.assertEqual(model["contract_version"], DEPLOYMENT_PROFILE_CONTRACT_VERSION)
        self.assertEqual(model["core_protocol_obligations"], list(CORE_PROTOCOL_OBLIGATIONS))
        self.assertTrue(validate_deployment_profiles(model))
        self.assertEqual({p["id"] for p in model["profiles"]}, {"software_standard", "constrained_connectivity", "hardware_enhanced_security"})
        self.assertTrue(all(p["hardware_required"] is False for p in model["profiles"]))

    def test_constrained_profile_requires_offline_and_recovery_acceptance(self):
        p = next(x for x in deployment_profiles()["profiles"] if x["id"] == "constrained_connectivity")
        checks = " ".join(p["additional_acceptance_checks"]).lower()
        self.assertIn("power-loss", checks)
        self.assertIn("stale", checks)
        self.assertIn("reconnect", checks)
        self.assertIn("offline", p["offline_policy"].lower())

    def test_hardware_enhanced_profile_keeps_hardware_optional(self):
        p = next(x for x in deployment_profiles()["profiles"] if x["id"] == "hardware_enhanced_security")
        self.assertFalse(p["hardware_required"])
        self.assertIn("HSM", p["optional_capabilities"])
        self.assertIn("zero_knowledge_proofs", p["optional_capabilities"])

    def test_validator_rejects_required_hardware_and_duplicate_ids(self):
        model = deployment_profiles()
        model["profiles"][0]["hardware_required"] = True
        self.assertFalse(validate_deployment_profiles(model))
        model = deployment_profiles()
        model["profiles"].append(dict(model["profiles"][0]))
        self.assertFalse(validate_deployment_profiles(model))

if __name__ == "__main__":
    unittest.main()
