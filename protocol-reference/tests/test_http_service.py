# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""HTTP contract tests for the local synthetic prototype service."""
import io
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from http_service import create_app


class HttpServiceTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.addCleanup(self.app.close)

    def request(self, path, method="GET", body=None, content_type="application/json", headers=None):
        raw = b"" if body is None else json.dumps(body).encode("utf-8")
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "CONTENT_TYPE": content_type,
            "CONTENT_LENGTH": str(len(raw)),
            "wsgi.input": io.BytesIO(raw),
        }
        for key, value in (headers or {}).items():
            environ[key] = value
        captured = {}

        def start_response(status, response_headers):
            captured["status"] = int(status.split()[0])
            captured["headers"] = dict(response_headers)

        chunks = self.app(environ, start_response)
        payload = json.loads(b"".join(chunks).decode("utf-8"))
        return captured, payload

    def test_health_is_live_and_carries_request_id(self):
        response, body = self.request("/system/health", headers={"HTTP_X_REQUEST_ID": "test-request-1"})
        self.assertEqual(response["status"], 200)
        self.assertEqual(body["status"], "ALIVE")
        self.assertEqual(body["request_id"], "test-request-1")
        self.assertEqual(response["headers"]["Cache-Control"], "no-store")

    def test_readiness_does_not_claim_production_ready(self):
        response, body = self.request("/system/readiness")
        self.assertEqual(response["status"], 200)
        self.assertFalse(body["production_ready"])

    def test_capabilities_disclose_synthetic_ivm_cee_pipeline_and_production_gaps(self):
        response, body = self.request("/system/capabilities")
        self.assertEqual(response["status"], 200)
        self.assertEqual(body["protocol_pipeline"]["status"], "SYNTHETIC_VERTICAL_SLICE_IMPLEMENTED")
        self.assertFalse(body["protocol_pipeline"]["cross_store_atomicity"])
        self.assertIn(
            "production identity and cryptographic authentication",
            body["not_yet_wired_as_production_components"],
        )

    def test_complete_synthetic_instruction_returns_trace_and_never_moves_funds(self):
        response, body = self.request("/demo/instructions", "POST", {"consignment_id": "HTTP-HAPPY-1"})
        self.assertEqual(response["status"], 200)
        self.assertEqual(body["status"], "COMPLETED_WITH_SYNTHETIC_ADAPTERS")
        result = body["result"]
        self.assertTrue(result["system_trace_valid"])
        self.assertEqual(result["minah_advisory"]["may_authorise"], False)
        self.assertFalse(result["payment"]["funds_moved"])
        self.assertFalse(result["payment_lifecycle"]["funds_moved"])
        self.assertEqual(body["protocol_boundary"]["ivm"], "NOT_WIRED_AS_SEPARATE_RUNTIME")
        self.assertEqual(body["protocol_boundary"]["cee"], "NOT_WIRED_AS_SEPARATE_LIFECYCLE_ENGINE")

    def test_protocol_route_returns_full_minah_ivm_cee_trace(self):
        payload = {
            "operation_id": "HTTP-PIPE-1",
            "event_id": "HTTP-EVENT-1",
            "process_id": "HTTP-PROCESS-1",
            "actor_id": "AUTHORITY-001",
            "event_type": "ACTIVATE",
            "jurisdiction": "UG-DEMO",
            "rule_set": "KIN-SYS-DEMO-RULES",
            "rule_version": "1.0",
            "evidence": {
                "consignment_id": "HTTP-PROCESS-1",
                "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
                "rule_refs": ["SYNTHETIC-RULE-1"],
            },
        }
        response, body = self.request("/protocol/instructions", "POST", payload)
        self.assertEqual(response["status"], 200)
        self.assertEqual(body["status"], "COMMITTED")
        self.assertFalse(body["production_ready"])
        self.assertEqual(body["result"]["minah_disposition"]["status"], "ADVISORY_ONLY")
        self.assertFalse(body["result"]["minah_disposition"]["may_authorise"])
        self.assertEqual(body["result"]["ivm_decision"]["to_state"], "ACTIVE")
        self.assertEqual(
            [event["phase"] for event in body["result"]["cee_events"]],
            ["REQUESTED", "ADMITTED", "CONTEXT_ESTABLISHED", "AUTHORITY_EVALUATED",
             "EXECUTING", "PREPARED", "COMMITTING", "COMMITTED", "FINALIZED"],
        )
        trace = body["result"]["component_trace"]
        components = [event["component"] for event in trace]
        self.assertLess(components.index("MINAH"), components.index("IVM"))
        self.assertEqual(trace[-1]["detail"]["phase"], "FINALIZED")
        self.assertFalse(body["result"]["funds_moved"])
        self.assertFalse(body["protocol_boundary"]["distributed_finality"])

    def test_protocol_route_defers_missing_evidence_before_ivm(self):
        payload = {
            "operation_id": "HTTP-PIPE-DEFER",
            "event_id": "HTTP-EVENT-DEFER",
            "process_id": "HTTP-PROCESS-DEFER",
            "actor_id": "AUTHORITY-001",
            "event_type": "ACTIVATE",
            "jurisdiction": "UG-DEMO",
            "rule_set": "KIN-SYS-DEMO-RULES",
            "rule_version": "1.0",
            "evidence": {"commodity": "SYNTHETIC_MAIZE"},
        }
        response, body = self.request("/protocol/instructions", "POST", payload)
        self.assertEqual(response["status"], 200)
        self.assertEqual(body["status"], "DEFERRED")
        self.assertIsNone(body["result"]["ivm_decision"])

    def test_protocol_route_rejects_unrecognised_rule_version(self):
        payload = {
            "operation_id": "HTTP-PIPE-RULE",
            "event_id": "HTTP-EVENT-RULE",
            "process_id": "HTTP-PROCESS-RULE",
            "actor_id": "AUTHORITY-001",
            "event_type": "ACTIVATE",
            "jurisdiction": "UG-DEMO",
            "rule_set": "KIN-SYS-DEMO-RULES",
            "rule_version": "2.0",
            "evidence": {
                "consignment_id": "HTTP-PROCESS-RULE", "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO", "destination": "BORDER-DEMO",
            },
        }
        response, body = self.request("/protocol/instructions", "POST", payload)
        self.assertEqual(response["status"], 422)
        self.assertEqual(body["error"]["code"], "INVALID_PROTOCOL_INSTRUCTION")

    def test_rejected_origin_stops_downstream_payment(self):
        response, body = self.request(
            "/demo/instructions", "POST",
            {"consignment_id": "HTTP-REJECT-1", "origin_decision": "REJECT"},
        )
        self.assertEqual(response["status"], 200)
        self.assertEqual(body["result"]["status"], "INSTITUTIONAL_DECISION_REJECTED")
        self.assertIsNone(body["result"]["payment"])

    def test_missing_identifier_is_rejected_before_execution(self):
        response, body = self.request("/demo/instructions", "POST", {})
        self.assertEqual(response["status"], 422)
        self.assertEqual(body["error"]["code"], "INVALID_CONSIGNMENT_ID")

    def test_unknown_fields_fail_closed(self):
        response, body = self.request("/demo/instructions", "POST", {"consignment_id": "HTTP-X", "skip_minah": True})
        self.assertEqual(response["status"], 422)
        self.assertEqual(body["error"]["code"], "UNKNOWN_FIELDS")

    def test_wrong_content_type_is_rejected(self):
        response, body = self.request("/demo/instructions", "POST", {"consignment_id": "HTTP-X"}, "text/plain")
        self.assertEqual(response["status"], 415)

    def test_oversized_request_is_rejected_before_reading_body(self):
        environ = {
            "REQUEST_METHOD": "POST", "PATH_INFO": "/demo/instructions",
            "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(64 * 1024 + 1),
            "wsgi.input": io.BytesIO(b""),
        }
        captured = {}
        def start_response(status, headers):
            captured["status"] = int(status.split()[0])
        payload = json.loads(b"".join(self.app(environ, start_response)).decode("utf-8"))
        self.assertEqual(captured["status"], 413)
        self.assertEqual(payload["error"]["code"], "REQUEST_TOO_LARGE")

    def test_unknown_production_route_is_not_faked_as_success(self):
        response, body = self.request("/cases", "POST", {"consignment_id": "HTTP-X"})
        self.assertEqual(response["status"], 404)
        self.assertEqual(body["error"]["code"], "ROUTE_NOT_FOUND")

    def test_get_on_instruction_route_is_not_allowed(self):
        response, body = self.request("/demo/instructions", "GET")
        self.assertEqual(response["status"], 405)
        self.assertEqual(body["error"]["code"], "METHOD_NOT_ALLOWED")


if __name__ == "__main__":
    unittest.main()
