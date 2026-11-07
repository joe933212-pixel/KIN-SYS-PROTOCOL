# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import io
import json
import unittest

from governed_contracts import CONTRACT_VERSION, compute_payload_digest
from http_service import create_app
from institutional_instruction_execution import InstitutionalInstructionExecutionService


class SessionResolver:
    def resolve(self, environ):
        return {"authenticated": True, "tenant_id": "tenant-ug"}


class RuntimeResolver:
    def resolve(self, *, tenant_id, institution_id, binding_id):
        return {
            "tenant_id": tenant_id,
            "institution_id": institution_id,
            "institution_version": "2.0",
            "binding_id": binding_id,
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
            "minah_config": {"endpoint_ref": "secret://private"},
        }


class Pipeline:
    EXECUTION_ORDER = ("MINAH", "IDENTITY", "AUTHORITY", "CEE", "FINAL_DECISION")
    SUPPORTS_INSTITUTIONAL_RUNTIME_PINS = True

    def __init__(self):
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        return {
            "operation_id": request["operation_id"],
            "status": "COMMITTED",
            "reason": "SYNTHETIC_GATES_PASSED",
            "funds_moved": False,
        }


def make_instruction():
    value = {
        "contract_version": CONTRACT_VERSION,
        "request_id": "REQ-HTTP-1",
        "operation_id": "OP-HTTP-1",
        "idempotency_key": "idempotency-http-1",
        "principal_ref": "actor:alice",
        "authority_ref": "authority:bank",
        "action": "ACTIVATE",
        "target_ref": "PROCESS-HTTP-1",
        "purpose": "Institutional validation",
        "jurisdiction": "UG",
        "submitted_at": "2026-11-07T10:00:00+00:00",
        "rule_pack_version": "BANK-RULES:1.0",
        "evidence_refs": ["evidence:http-1"],
        "evidence": {"document_ref": "evidence:http-1"},
    }
    value["payload_digest"] = compute_payload_digest(value)
    return value


class InstitutionalInstructionHttpTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = Pipeline()
        service = InstitutionalInstructionExecutionService(
            runtime_binding_resolver=RuntimeResolver(),
            pipeline_factory=lambda context: self.pipeline,
        )
        self.app = create_app(
            institutional_instruction_execution=service,
            institutional_session_resolver=SessionResolver(),
        )
        self.addCleanup(self.app.close)

    def request(self, payload, path="/institutional/instructions", method="POST"):
        raw = json.dumps(payload).encode()
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": str(len(raw)),
            "REMOTE_ADDR": "127.0.0.1",
            "wsgi.input": io.BytesIO(raw),
        }
        captured = {}
        def start_response(status, headers):
            captured["status"] = int(status.split()[0])
        body = b"".join(self.app(environ, start_response))
        return captured, json.loads(body)

    def test_authenticated_institutional_route_executes_with_session_tenant_and_safe_pins(self):
        response, body = self.request({
            "institution_id": "BANK-UG-01",
            "binding_id": "BIND-UG-01",
            "instruction": make_instruction(),
            "evidence": {"document_ref": "evidence:http-1"},
        })
        self.assertEqual(response["status"], 200, body)
        self.assertEqual(body["decision"]["protocol_decision"], "PASSED")
        self.assertEqual(body["runtime_pins"]["tenant_id"], "tenant-ug")
        self.assertNotIn("minah_config", json.dumps(body))
        self.assertNotIn("endpoint_ref", json.dumps(body))
        self.assertEqual(
            self.pipeline.requests[0]["_institutional_runtime_binding"]["binding_id"],
            "BIND-UG-01",
        )

    def test_client_cannot_supply_tenant_context(self):
        response, body = self.request({
            "tenant_id": "tenant-attacker",
            "institution_id": "BANK-UG-01",
            "binding_id": "BIND-UG-01",
            "instruction": make_instruction(),
            "evidence": {"document_ref": "evidence:http-1"},
        })
        self.assertEqual(response["status"], 422)
        self.assertEqual(body["error"]["code"], "UNKNOWN_FIELDS")
        self.assertEqual(self.pipeline.requests, [])

    def test_route_is_post_only(self):
        response, body = self.request({}, method="GET")
        self.assertEqual(response["status"], 405)
        self.assertEqual(body["error"]["code"], "METHOD_NOT_ALLOWED")


if __name__ == "__main__":
    unittest.main()
