# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import unittest

from integrated_demo import IntegratedDemonstrator
from system_model import SYSTEM_MODEL_VERSION, system_manifest


class WholeSystemModelTests(unittest.TestCase):
    def test_manifest_covers_major_system_responsibilities_and_limits(self):
        manifest = system_manifest()
        self.assertEqual(manifest["model_version"], SYSTEM_MODEL_VERSION)
        self.assertEqual(manifest["scope"], "WHOLE_KIN_SYS_SYSTEM")
        components = {item["id"]: item for item in manifest["components"]}

        required = {
            "protocol_orchestration",
            "identity_authority",
            "protocol_ingress_and_messages",
            "evidence_and_external_facts",
            "rules_and_statutory_policy",
            "minah_ai",
            "execution_and_history",
            "institutional_coordination",
            "economic_accounting_and_remittance",
            "edge_operation",
            "network_and_federation",
            "nexus_pod_infrastructure",
            "privacy_and_security",
            "governance_disputes_and_upgrades",
            "external_adapters",
            "economic_mechanism_kin",
            "conformance_and_evidence",
            "cee_ivm_research_lineage",
            "crs_cisa_research_lineage",
        }
        self.assertTrue(required.issubset(components))
        self.assertEqual(components["minah_ai"]["status"], "SIMULATED")
        self.assertEqual(components["nexus_pod_infrastructure"]["status"], "SPECIFIED")
        self.assertEqual(components["economic_mechanism_kin"]["status"], "OPEN_DESIGN")
        self.assertEqual(components["network_and_federation"]["status"], "SPECIFIED")
        self.assertIn("No physical Nexus Pod", " ".join(manifest["system_claims"]))

    def test_each_demonstrator_profile_exposes_same_whole_system_model(self):
        demo = IntegratedDemonstrator()
        try:
            central = demo.run_case(consignment_id="SYSTEM-MODEL-CENTRAL")
            edge = demo.run_case(
                consignment_id="SYSTEM-MODEL-EDGE",
                edge_connected=False,
            )
            self.assertEqual(central["system_model"]["model_version"], SYSTEM_MODEL_VERSION)
            self.assertEqual(edge["system_model"]["model_version"], SYSTEM_MODEL_VERSION)
            self.assertEqual(
                [item["id"] for item in central["system_model"]["components"]],
                [item["id"] for item in edge["system_model"]["components"]],
            )
        finally:
            demo.close()


if __name__ == "__main__":
    unittest.main()
