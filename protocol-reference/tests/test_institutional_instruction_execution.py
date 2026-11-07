# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import unittest

from governed_contracts import CONTRACT_VERSION, compute_payload_digest
from institutional_instruction_execution import (
    InstitutionalInstructionExecutionError,
    InstitutionalInstructionExecutionService,
)
from protocol_pipeline import KinSysProtocolPipeline


class RuntimeResolver:
    def __init__(self, context=None):
        self.context = context or {
            "tenant_id": "tenant-ug",
            "institution_id": "BANK-UG-01",
            "institution_version": "2.0",
            "binding_id": "BIND-UG-01",
            "binding_version": "3.0",
            "jurisdiction_ref": "UG",
            "jurisdiction_package_id": "JUR-UG-01",
            "jurisdiction_package_version": "4.0",
            "minah_deployment_id": "MINAH-UG-01",
            "minah_domain_version": "5.0",
            "minah_runtime_version": "9.0",
            "policy_pack_version": "BANK-RULES:1.0",
            "source_pack_version": "UG-SOURCES:2.0",
            "authority_profile_version": "BANK-AUTHORITY:1.0",
            "runtime_admission": "ELIGIBLE_FOR_GOVERNED_EXECUTION",
            "minah_config": {
                "deployment_id": "MINAH-UG-01",
                "owner_ref": "tenant-ug",
                "model_ref": "model:bank/minah-v1",
                "policy_pack_version": "BANK-RULES:1.0",
                "source_pack_version": "UG-SOURCES:2.0",
                "endpoint_ref": "secret://private-endpoint",
            },
        }
        self.calls = []

    def resolve(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.context)


class Pipeline:
    def __init__(self):
        self.requests = []

    def execute(self, request):
        self.requests.append(dict(request))
        return {
            "operation_id": request["operation_id"],
            "status": "COMMITTED",
            "reason": "SYNTHETIC_GATES_PASSED",
            "funds_moved": False,
            "ivm_decision": {"status": "ACCEPTED"},
        }


def instruction():
    value = {
        "contract_version": CONTRACT_VERSION,
        "request_id": "REQ-1001",
        "operation_id": "OP-1001",
        "idempotency_key": "idempotency-1001",
        "principal_ref": "actor:alice",
        "authority_ref": "authority:bank-ug",
        "action": "ACTIVATE",
        "target_ref": "PROCESS-1001",
        "purpose": "Institutional transaction validation",
        "jurisdiction": "UG",
        "submitted_at": "2026-11-07T10:00:00+00:00",
        "rule_pack_version": "BANK-RULES:1.0",
        "evidence_refs": ["evidence:case-1001"],
        "evidence": {"document_ref": "evidence:case-1001"},
    }
    value["payload_digest"] = compute_payload_digest(value)
    return value


class InstitutionalInstructionExecutionTests(unittest.TestCase):
    def setUp(self):
        self.resolver = RuntimeResolver()
        self.pipeline = Pipeline()
        self.service = InstitutionalInstructionExecutionService(
            runtime_binding_resolver=self.resolver,
            pipeline_factory=lambda context: self.pipeline,
        )

    def test_resolves_binding_pins_exact_versions_and_executes_pipeline(self):
        result = self.service.execute(
            tenant_id="tenant-ug",
            institution_id="BANK-UG-01",
            binding_id="BIND-UG-01",
            instruction=instruction(),
            evidence={"document_ref": "evidence:case-1001"},
        )
        self.assertEqual(self.resolver.calls, [{
            "tenant_id": "tenant-ug",
            "institution_id": "BANK-UG-01",
            "binding_id": "BIND-UG-01",
        }])
        self.assertEqual(result["decision"]["protocol_decision"], "PASSED")
        self.assertEqual(result["runtime_pins"]["binding_version"], "3.0")
        self.assertEqual(result["runtime_pins"]["minah_runtime_version"], "9.0")
        pinned = self.pipeline.requests[0]["_institutional_runtime_binding"]
        self.assertEqual(pinned, result["runtime_pins"])
        self.assertEqual(self.pipeline.requests[0]["jurisdiction"], "UG")
        self.assertNotIn("minah_config", pinned)
        self.assertNotIn("endpoint_ref", str(pinned))

    def test_rejects_instruction_for_different_jurisdiction_before_pipeline(self):
        request = instruction()
        request["jurisdiction"] = "KE"
        request["payload_digest"] = compute_payload_digest(request)
        with self.assertRaisesRegex(
            InstitutionalInstructionExecutionError,
            "INSTRUCTION_JURISDICTION_DOES_NOT_MATCH_ACTIVE_BINDING",
        ):
            self.service.execute(
                tenant_id="tenant-ug", institution_id="BANK-UG-01",
                binding_id="BIND-UG-01", instruction=request, evidence=request["evidence"],
            )
        self.assertEqual(self.pipeline.requests, [])

    def test_rejects_policy_version_drift_before_pipeline(self):
        request = instruction()
        request["rule_pack_version"] = "BANK-RULES:2.0"
        request["payload_digest"] = compute_payload_digest(request)
        with self.assertRaisesRegex(
            InstitutionalInstructionExecutionError,
            "INSTRUCTION_POLICY_VERSION_DOES_NOT_MATCH_ACTIVE_BINDING",
        ):
            self.service.execute(
                tenant_id="tenant-ug", institution_id="BANK-UG-01",
                binding_id="BIND-UG-01", instruction=request, evidence={"ref": "e1"},
            )
        self.assertEqual(self.pipeline.requests, [])

    def test_reference_pipeline_persists_institutional_pins_before_minah_review(self):
        context = dict(self.resolver.context)
        context.update({
            "jurisdiction_ref": "UG-DEMO",
            "policy_pack_version": "KIN-SYS-DEMO-RULES:1.0",
            "source_pack_version": "SYNTHETIC-NO-LEGAL-SOURCES",
            "minah_config": {
                "deployment_id": "MINAH-UG-01",
                "owner_ref": "tenant-ug",
                "model_ref": "deterministic-minah-mock",
                "policy_pack_version": "KIN-SYS-DEMO-RULES:1.0",
                "source_pack_version": "SYNTHETIC-NO-LEGAL-SOURCES",
            },
        })
        self.resolver.context = context
        profile = {
            "profile_version": "kin-sys-minah-profile-0.1",
            "deployment_id": "MINAH-UG-01",
            "owner_scope": "INSTITUTION",
            "owner_ref": "tenant-ug",
            "use_case": "INSTITUTIONAL_REFERENCE_TEST",
            "jurisdictions": ["UG-DEMO"],
            "policy_pack_version": "KIN-SYS-DEMO-RULES:1.0",
            "source_pack_version": "SYNTHETIC-NO-LEGAL-SOURCES",
            "model_ref": "deterministic-minah-mock",
            "deployment_mode": "SELF_HOSTED",
            "may_authorise": False,
        }
        pipeline = KinSysProtocolPipeline(
            minah_profile=profile,
            cee_database=":memory:",
            ivm_database=":memory:",
        )
        self.addCleanup(pipeline.close)
        service = InstitutionalInstructionExecutionService(
            runtime_binding_resolver=self.resolver,
            pipeline_factory=lambda runtime_context: pipeline,
        )
        evidence = {
            "consignment_id": "PROCESS-REAL-1",
            "commodity": "SYNTHETIC_MAIZE",
            "origin": "UGANDA-DEMO",
            "destination": "BORDER-DEMO",
            "rule_refs": ["SYNTHETIC-RULE-1"],
        }
        request = {
            "contract_version": CONTRACT_VERSION,
            "request_id": "REQ-REAL-1",
            "operation_id": "OP-REAL-1",
            "idempotency_key": "idempotency-real-1",
            "principal_ref": "AUTHORITY-001",
            "authority_ref": "AUTHORITY-001",
            "action": "ACTIVATE",
            "target_ref": "PROCESS-REAL-1",
            "purpose": "Synthetic institutional execution test",
            "jurisdiction": "UG-DEMO",
            "submitted_at": "2026-11-07T10:00:00+00:00",
            "rule_pack_version": "KIN-SYS-DEMO-RULES:1.0",
            "evidence_refs": ["evidence:real-1"],
            "evidence": evidence,
        }
        request["payload_digest"] = compute_payload_digest(request)
        outcome = service.execute(
            tenant_id="tenant-ug", institution_id="BANK-UG-01",
            binding_id="BIND-UG-01", instruction=request, evidence=evidence,
        )
        self.assertEqual(outcome["decision"]["protocol_decision"], "PASSED")
        operation = pipeline.cee.get("OP-REAL-1")
        self.assertIsNotNone(operation)
        self.assertEqual(
            operation["request_json"]["_institutional_runtime_binding"],
            outcome["runtime_pins"],
        )
        self.assertEqual(
            operation["request_json"]["_institutional_runtime_binding"]["binding_version"],
            "3.0",
        )


if __name__ == "__main__":
    unittest.main()
