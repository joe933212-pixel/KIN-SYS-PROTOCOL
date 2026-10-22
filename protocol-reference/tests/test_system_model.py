# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import unittest

from integrated_demo import IntegratedDemonstrator
from component_contracts import ContractViolation, make_component_response
from system_model import SYSTEM_MODEL_VERSION, system_manifest, validate_system_trace


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
            self.assertTrue(central["system_trace_valid"])
            self.assertTrue(edge["system_trace_valid"])
            self.assertTrue(validate_system_trace(central["system_trace"], case_id="SYSTEM-MODEL-CENTRAL"))
            self.assertTrue(validate_system_trace(edge["system_trace"], case_id="SYSTEM-MODEL-EDGE"))
            self.assertEqual(
                {response["component_id"] for response in central["component_responses"]},
                {"identity_authority", "minah_ai", "institutional_coordination", "rules_and_statutory_policy", "external_adapters"},
            )
            self.assertEqual(
                {response["component_id"] for response in edge["component_responses"]},
                {"identity_authority", "minah_ai"},
            )
            for response in central["component_responses"] + edge["component_responses"]:
                self.assertEqual(response["contract_version"], "kin-sys-component-response-0.1")
                self.assertTrue(response["operation_id"].startswith(response["data"].get("case_id", response["operation_id"].split(":")[0])))
            central_components = {event["component_id"] for event in central["system_trace"]}
            self.assertTrue({
                "identity_authority",
                "minah_ai",
                "institutional_coordination",
                "rules_and_statutory_policy",
                "economic_accounting_and_remittance",
                "protocol_orchestration",
            }.issubset(central_components))
            self.assertIn("edge_operation", {event["component_id"] for event in edge["system_trace"]})
        finally:
            demo.close()

    def test_institutional_response_contract_preserves_deferred_stage_without_success(self):
        demo = IntegratedDemonstrator()
        try:
            result = demo.run_case(
                consignment_id="CONTRACT-ORIGIN-REJECT",
                origin_decision="REJECT",
            )
            decisions = [
                response for response in result["component_responses"]
                if response["component_id"] == "institutional_coordination"
            ]
            self.assertEqual(len(decisions), 2)
            self.assertEqual(decisions[0]["status"], "ACCEPTED")
            self.assertEqual(decisions[0]["data"]["status"], "ACCEPTED")
            self.assertEqual(decisions[1]["status"], "DEFERRED")
            self.assertEqual(decisions[1]["failure_code"], "ORIGIN_STAGE_NOT_APPROVED")
            self.assertEqual(decisions[1]["data"]["status"], "NOT_ATTEMPTED")
            self.assertIsNone(result["statutory_obligation"])
            self.assertIsNone(result["payment"])
        finally:
            demo.close()

    def test_misrouted_minah_response_stops_case_before_downstream_effects(self):
        class MisroutedMinah:
            version = "test-minah-0.1"

            def respond(self, operation_id, evidence):
                return make_component_response(
                    component_id="minah_ai",
                    operation_id="SOME-OTHER-CASE:MINAH:REVIEW:1",
                    component_version=self.version,
                    status="ACCEPTED",
                    data={
                        "status": "ADVISORY_ONLY",
                        "missing_fields": [],
                        "source_refs": [],
                        "uncertainty": "LOW",
                        "may_authorise": False,
                        "model_version": self.version,
                    },
                )

        demo = IntegratedDemonstrator(minah_adapter=MisroutedMinah())
        try:
            with self.assertRaisesRegex(ContractViolation, "COMPONENT_RESPONSE_OPERATION_MISMATCH"):
                demo.run_case(consignment_id="MINAH-MISROUTED-CASE")
            self.assertEqual(demo.workflow.history("MINAH-MISROUTED-CASE"), [])
            self.assertEqual(
                demo.payment_lifecycle.db.execute("SELECT COUNT(*) FROM obligations").fetchone()[0],
                0,
            )
        finally:
            demo.close()

    def test_system_trace_rejects_cross_case_or_incomplete_events(self):
        event = {
            "trace_contract_version": "kin-sys-component-trace-0.1",
            "case_id": "CASE-A",
            "operation_id": "CASE-A:OP:1",
            "component_id": "identity_authority",
            "status": "ACCEPTED",
            "mode": "SYNTHETIC_IDENTITIES",
            "component_version": "0.1",
            "evidence_refs": [],
            "reason": None,
        }
        self.assertTrue(validate_system_trace([event], case_id="CASE-A"))
        self.assertFalse(validate_system_trace([event], case_id="CASE-B"))
        self.assertFalse(validate_system_trace([{"case_id": "CASE-A"}], case_id="CASE-A"))


if __name__ == "__main__":
    unittest.main()
