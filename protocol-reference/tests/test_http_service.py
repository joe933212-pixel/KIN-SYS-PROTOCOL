# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""HTTP contract tests for the local synthetic prototype service."""
import io
import json
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from wsgiref.simple_server import make_server
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from http_service import create_app
from protocol_pipeline import KinSysProtocolPipeline
from integrated_demo import DeterministicMinahAdapter
from governed_contracts import compute_payload_digest


class HttpServiceTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(enable_legacy_demo_routes=True)
        self.addCleanup(self.app.close)

    def request_with_app(self, app, path, method="GET", body=None, content_type="application/json", headers=None):
        original = self.app
        self.app = app
        try:
            return self.request(path, method, body, content_type, headers)
        finally:
            self.app = original

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

    def test_legacy_synthetic_routes_are_disabled_by_default(self):
        app = create_app()
        self.addCleanup(app.close)
        for path in ("/demo/instructions", "/cee/cases", "/cee/reconciliation", "/cee/settlement-evidence"):
            response, body = self.request_with_app(app, path, "POST", {})
            self.assertEqual(response["status"], 404)
            self.assertEqual(body["error"]["code"], "ROUTE_NOT_AVAILABLE")
        capability_response, capabilities = self.request_with_app(app, "/system/capabilities")
        self.assertEqual(capability_response["status"], 200)
        self.assertFalse(capabilities["legacy_demo_routes"]["enabled_in_this_instance"])
        self.assertFalse(capabilities["legacy_demo_routes"]["default_enabled"])
        self.assertEqual(
            capabilities["legacy_demo_routes"]["classification"],
            "SYNTHETIC_ONLY_NOT_THE_GOVERNED_V0_1_INSTRUCTION_PATH",
        )

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
        self.assertFalse(body["legacy_route_policy"]["enabled_by_default"])
        self.assertFalse(body["legacy_route_policy"]["legacy_routes_are_canonical_execution_path"])
        self.assertFalse(body["protocol_pipeline"]["cross_store_atomicity"])
        self.assertEqual(
            body["ingress_adapter_contract"]["durable_ingress_journal"],
            "SQLITE_ENABLED_FOR_V0_1_INSTRUCTION_ENVELOPE",
        )
        self.assertEqual(
            body["ingress_adapter_contract"]["cross_store_reconciliation"],
            "EXPLICIT_RECOVERY_FROM_DURABLE_MINAH_DISPOSITION; EARLY_AMBIGUOUS_PHASES_REMAIN_UNKNOWN; LOCAL_SQLITE_ONLY",
        )
        self.assertEqual(
            body["ingress_adapter_contract"]["recovery_endpoint"],
            "POST /protocol/operations/{operation_id}/recover",
        )
        self.assertEqual(
            body["ingress_adapter_contract"]["legacy_internal_request_shape"],
            "OPT_IN_ONLY_WITH_ENABLE_LEGACY_DEMO_ROUTES",
        )
        self.assertEqual(
            body["ingress_adapter_contract"]["recovery_audit_endpoint"],
            "GET /protocol/operations/{operation_id}/recovery-events",
        )
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


    def test_legacy_execution_routes_are_disabled_by_default(self):
        app = create_app()
        try:
            response, body = self.request_with_app(
                app, "/protocol/instructions", "POST",
                {"operation_id": "LEGACY-REQUEST-DISABLED"},
            )
            self.assertEqual(response["status"], 404)
            self.assertEqual(body["error"]["code"], "LEGACY_REQUEST_SHAPE_DISABLED")
            for path in (
                "/demo/instructions",
                "/cee/cases",
                "/cee/reconciliation",
                "/cee/settlement-evidence",
            ):
                response, body = self.request_with_app(app, path, "POST", {})
                self.assertEqual(response["status"], 404, path)
                self.assertEqual(body["error"]["code"], "ROUTE_NOT_AVAILABLE", path)
        finally:
            app.close()

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
        self.assertEqual(body["decision"]["contract_version"], "kin-sys-governed-contract-0.1")
        self.assertEqual(body["decision"]["request_id"], body["request_id"])
        self.assertEqual(body["decision"]["protocol_decision"], "PASSED")
        self.assertEqual(body["decision"]["external_effect_status"], "NOT_ATTEMPTED")
        self.assertEqual(body["decision"]["rule_pack_version"], "KIN-SYS-DEMO-RULES:1.0")
        self.assertEqual(body["result"]["minah_disposition"]["status"], "ADVISORY_ONLY")
        self.assertFalse(body["result"]["minah_disposition"]["may_authorise"])
        self.assertEqual(body["result"]["ivm_decision"]["to_state"], "ACTIVE")
        self.assertEqual(
            [event["phase"] for event in body["result"]["cee_events"]],
            ["REQUESTED", "MINAH_REVIEWED", "IDENTITY_VERIFIED", "AUTHORITY_VERIFIED", "ADMITTED", "CONTEXT_ESTABLISHED", "AUTHORITY_EVALUATED",
             "EXECUTING", "PREPARED", "COMMITTING", "COMMITTED", "FINALIZED"],
        )
        trace = body["result"]["component_trace"]
        components = [event["component"] for event in trace]
        self.assertLess(components.index("MINAH"), components.index("IDENTITY"))
        self.assertLess(components.index("IDENTITY"), components.index("AUTHORITY"))
        self.assertLess(components.index("AUTHORITY"), components.index("IVM"))
        self.assertEqual(trace[-1]["detail"]["phase"], "FINALIZED")
        self.assertFalse(body["result"]["funds_moved"])
        self.assertFalse(body["protocol_boundary"]["distributed_finality"])

    def test_http_ingress_injects_adapters_and_enforces_the_real_pipeline_order(self):
        calls = []

        class RecordingMinah:
            def review(self, evidence):
                calls.append("MINAH")
                return DeterministicMinahAdapter().review(evidence)

        class RecordingIdentity:
            def verify_principal(self, request):
                calls.append("IDENTITY")
                return {"verified": True, "mode": "TEST_ADAPTER"}

        class RecordingAuthority:
            def verify_action(self, request):
                calls.append("AUTHORITY")
                return {"verified": True, "mode": "TEST_ADAPTER"}

        app = create_app(enable_legacy_demo_routes=True, 
            minah_adapter=RecordingMinah(),
            identity_adapter=RecordingIdentity(),
            authority_adapter=RecordingAuthority(),
        )
        self.addCleanup(app.close)
        payload = {
            "operation_id": "HTTP-ADAPTER-ORDER-1",
            "event_id": "HTTP-ADAPTER-EVENT-1",
            "process_id": "HTTP-ADAPTER-PROCESS-1",
            "actor_id": "AUTHORITY-001",
            "event_type": "ACTIVATE",
            "jurisdiction": "UG-DEMO",
            "rule_set": "KIN-SYS-DEMO-RULES",
            "rule_version": "1.0",
            "evidence": {
                "consignment_id": "HTTP-ADAPTER-PROCESS-1",
                "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
                "rule_refs": ["SYNTHETIC-RULE-1"],
            },
        }
        response, body = self.request_with_app(app, "/protocol/instructions", "POST", payload)
        self.assertEqual(response["status"], 200)
        self.assertEqual(body["status"], "COMMITTED")
        self.assertEqual(calls, ["MINAH", "IDENTITY", "AUTHORITY"])
        phases = [event["phase"] for event in body["result"]["cee_events"]]
        self.assertLess(phases.index("MINAH_REVIEWED"), phases.index("IDENTITY_VERIFIED"))
        self.assertLess(phases.index("IDENTITY_VERIFIED"), phases.index("AUTHORITY_VERIFIED"))
        self.assertLess(phases.index("AUTHORITY_VERIFIED"), phases.index("ADMITTED"))

    def test_live_http_server_enforces_injected_minah_identity_authority_order(self):
        calls = []

        class RecordingMinah:
            def review(self, evidence):
                calls.append("MINAH")
                return DeterministicMinahAdapter().review(evidence)

        class RecordingIdentity:
            def verify_principal(self, request):
                calls.append("IDENTITY")
                return {"verified": True, "mode": "LIVE_HTTP_TEST"}

        class RecordingAuthority:
            def verify_action(self, request):
                calls.append("AUTHORITY")
                return {"verified": True, "mode": "LIVE_HTTP_TEST"}

        app = create_app(
            enable_legacy_demo_routes=True,
            minah_adapter=RecordingMinah(),
            identity_adapter=RecordingIdentity(),
            authority_adapter=RecordingAuthority(),
        )
        server = make_server("127.0.0.1", 0, app)
        outcome = {}

        def client_request():
            payload = {
                "operation_id": "LIVE-HTTP-ORDER-1",
                "event_id": "LIVE-HTTP-EVENT-1",
                "process_id": "LIVE-HTTP-PROCESS-1",
                "actor_id": "AUTHORITY-001",
                "event_type": "ACTIVATE",
                "jurisdiction": "UG-DEMO",
                "rule_set": "KIN-SYS-DEMO-RULES",
                "rule_version": "1.0",
                "evidence": {
                    "consignment_id": "LIVE-HTTP-PROCESS-1",
                    "commodity": "SYNTHETIC_MAIZE",
                    "origin": "UGANDA-DEMO",
                    "destination": "BORDER-DEMO",
                    "rule_refs": ["SYNTHETIC-RULE-1"],
                },
            }
            request = Request(
                f"http://127.0.0.1:{server.server_port}/protocol/instructions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urlopen(request, timeout=5) as response:
                    outcome["status"] = response.status
                    outcome["body"] = json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                outcome["error"] = exc
            finally:
                server.shutdown()

        client = threading.Thread(target=client_request, daemon=True)
        client.start()
        try:
            # Serve the WSGI request on the same thread that created the SQLite
            # connections, matching the current single-threaded reference server.
            server.serve_forever()
            client.join(timeout=5)
            self.assertFalse(client.is_alive(), "HTTP client did not finish")
            if "error" in outcome:
                raise outcome["error"]
            body = outcome["body"]
            self.assertEqual(outcome["status"], 200)
            self.assertEqual(body["status"], "COMMITTED")
            self.assertEqual(calls, ["MINAH", "IDENTITY", "AUTHORITY"])
            phases = [event["phase"] for event in body["result"]["cee_events"]]
            self.assertLess(phases.index("MINAH_REVIEWED"), phases.index("IDENTITY_VERIFIED"))
            self.assertLess(phases.index("IDENTITY_VERIFIED"), phases.index("AUTHORITY_VERIFIED"))
            self.assertLess(phases.index("AUTHORITY_VERIFIED"), phases.index("ADMITTED"))
        finally:
            server.server_close()
            app.close()




    def test_v01_instruction_envelope_executes_through_existing_pipeline_and_binds_decision(self):
        class VerifiedIdentity:
            def verify_principal(self, request):
                self.asserted = request.get("_contract_envelope", {}).get("principal_ref")
                return {"verified": True, "mode": "CONTRACT_TEST_IDENTITY"}

        class VerifiedAuthority:
            def verify_action(self, request):
                envelope = request.get("_contract_envelope", {})
                if envelope.get("authority_ref") != "authority-check:contract-test":
                    return {"verified": False, "mode": "CONTRACT_TEST_AUTHORITY"}
                return {"verified": True, "mode": "CONTRACT_TEST_AUTHORITY"}

        identity = VerifiedIdentity()
        app = create_app(identity_adapter=identity, authority_adapter=VerifiedAuthority())
        self.addCleanup(app.close)
        payload = {
            "contract_version": "kin-sys-governed-contract-0.1",
            "request_id": "client-request-contract-001",
            "operation_id": "HTTP-CONTRACT-EXEC-1",
            "idempotency_key": "idempotency-contract-001",
            "principal_ref": "AUTHORITY-001",
            "authority_ref": "authority-check:contract-test",
            "action": "ACTIVATE",
            "target_ref": "HTTP-CONTRACT-PROCESS-1",
            "purpose": "synthetic conformance test",
            "jurisdiction": "UG-DEMO",
            "submitted_at": "2026-10-27T10:00:00+03:00",
            "payload_digest": "sha256:" + ("a" * 64),
            "rule_pack_version": "KIN-SYS-DEMO-RULES:1.0",
            "evidence_refs": ["evidence:contract-test-1"],
            "evidence": {
                "consignment_id": "HTTP-CONTRACT-PROCESS-1",
                "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
                "rule_refs": ["SYNTHETIC-RULE-1"],
            },
        }
        payload["payload_digest"] = compute_payload_digest(payload)
        response, body = self.request_with_app(app, "/protocol/instructions", "POST", payload)
        self.assertEqual(response["status"], 200)
        self.assertEqual(body["status"], "COMMITTED")
        self.assertEqual(body["decision"]["request_id"], payload["request_id"])
        self.assertEqual(body["decision"]["operation_id"], payload["operation_id"])
        self.assertEqual(body["decision"]["rule_pack_version"], payload["rule_pack_version"])
        self.assertEqual(body["decision"]["protocol_decision"], "PASSED")
        self.assertEqual(body["decision"]["evidence_refs"], payload["evidence_refs"])
        self.assertEqual(body["decision"]["external_effect_status"], "NOT_ATTEMPTED")
        self.assertEqual(identity.asserted, payload["principal_ref"])
        ingress_record = app.ingress.inspect(payload["operation_id"])
        self.assertIsNotNone(ingress_record)
        self.assertEqual(ingress_record["phase"], "PASSED")
        self.assertEqual(
            ingress_record["result"]["pipeline_result"]["status"], "COMMITTED"
        )
        phases = [event["phase"] for event in body["result"]["cee_events"]]
        self.assertLess(phases.index("MINAH_REVIEWED"), phases.index("IDENTITY_VERIFIED"))
        self.assertLess(phases.index("IDENTITY_VERIFIED"), phases.index("AUTHORITY_VERIFIED"))
        self.assertLess(phases.index("AUTHORITY_VERIFIED"), phases.index("ADMITTED"))

    def test_v01_envelope_metadata_is_pinned_and_cannot_change_on_retry(self):
        app = create_app(
            identity_adapter=type("Identity", (), {
                "verify_principal": lambda self, request: {"verified": True, "mode": "TEST"}
            })(),
            authority_adapter=type("Authority", (), {
                "verify_action": lambda self, request: {"verified": True, "mode": "TEST"}
            })(),
        )
        self.addCleanup(app.close)
        payload = {
            "contract_version": "kin-sys-governed-contract-0.1",
            "request_id": "client-request-contract-retry",
            "operation_id": "HTTP-CONTRACT-RETRY-1",
            "idempotency_key": "idempotency-contract-retry",
            "principal_ref": "AUTHORITY-001",
            "authority_ref": "authority-check:original",
            "action": "ACTIVATE",
            "target_ref": "HTTP-CONTRACT-RETRY-PROCESS",
            "purpose": "synthetic retry conformance test",
            "jurisdiction": "UG-DEMO",
            "submitted_at": "2026-10-27T10:00:00+03:00",
            "payload_digest": "sha256:" + ("b" * 64),
            "rule_pack_version": "KIN-SYS-DEMO-RULES:1.0",
            "evidence": {
                "consignment_id": "HTTP-CONTRACT-RETRY-PROCESS",
                "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
                "rule_refs": ["SYNTHETIC-RULE-1"],
            },
        }
        payload["payload_digest"] = compute_payload_digest(payload)
        first_response, first_body = self.request_with_app(app, "/protocol/instructions", "POST", payload)
        self.assertEqual(first_response["status"], 200)
        self.assertEqual(first_body["status"], "COMMITTED")
        changed = {**payload, "authority_ref": "authority-check:substituted"}
        changed["payload_digest"] = compute_payload_digest(changed)
        second_response, second_body = self.request_with_app(app, "/protocol/instructions", "POST", changed)
        self.assertEqual(second_response["status"], 409)
        self.assertEqual(second_body["error"]["code"], "IDEMPOTENCY_OR_STATE_CONFLICT")



    def test_v01_ingress_journal_replay_returns_stored_result_without_reinvoking_minah(self):
        calls = []

        class RecordingMinah:
            def review(self, evidence):
                calls.append("MINAH")
                return DeterministicMinahAdapter().review(evidence)

        app = create_app(
            minah_adapter=RecordingMinah(),
            identity_adapter=type("Identity", (), {
                "verify_principal": lambda self, request: {"verified": True, "mode": "TEST"}
            })(),
            authority_adapter=type("Authority", (), {
                "verify_action": lambda self, request: {"verified": True, "mode": "TEST"}
            })(),
        )
        self.addCleanup(app.close)
        payload = {
            "contract_version": "kin-sys-governed-contract-0.1",
            "request_id": "client-request-ingress-replay",
            "operation_id": "HTTP-INGRESS-REPLAY-1",
            "idempotency_key": "idempotency-ingress-replay",
            "principal_ref": "AUTHORITY-001",
            "authority_ref": "authority-check:replay",
            "action": "ACTIVATE",
            "target_ref": "HTTP-INGRESS-REPLAY-PROCESS",
            "purpose": "ingress journal replay test",
            "jurisdiction": "UG-DEMO",
            "submitted_at": "2026-10-27T10:00:00+03:00",
            "payload_digest": "sha256:" + ("d" * 64),
            "rule_pack_version": "KIN-SYS-DEMO-RULES:1.0",
            "evidence": {
                "consignment_id": "HTTP-INGRESS-REPLAY-PROCESS",
                "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
                "rule_refs": ["SYNTHETIC-RULE-1"],
            },
        }
        payload["payload_digest"] = compute_payload_digest(payload)
        first_response, first_body = self.request_with_app(app, "/protocol/instructions", "POST", payload)
        second_response, second_body = self.request_with_app(app, "/protocol/instructions", "POST", payload)
        self.assertEqual(first_response["status"], 200)
        self.assertEqual(second_response["status"], 200)
        self.assertEqual(first_body["decision"]["protocol_decision"], "PASSED")
        self.assertEqual(second_body["decision"]["protocol_decision"], "PASSED")
        self.assertTrue(second_body["result"]["duplicate"])
        self.assertEqual(calls, ["MINAH"])
        inspect_response, inspect_body = self.request_with_app(
            app, "/protocol/operations/HTTP-INGRESS-REPLAY-1", "GET"
        )
        self.assertEqual(inspect_response["status"], 200)
        self.assertEqual(inspect_body["source"], "MINAH_FIRST_INGRESS_JOURNAL")


    def test_explicit_recovery_after_ivm_commit_survives_full_app_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            cee_db = str(Path(directory) / "cee.sqlite3")
            ivm_db = str(Path(directory) / "ivm.sqlite3")
            ingress_db = str(Path(directory) / "ingress.sqlite3")
            payload = {
                "contract_version": "kin-sys-governed-contract-0.1",
                "request_id": "client-request-recover-commit",
                "operation_id": "HTTP-RECOVER-COMMIT-1",
                "idempotency_key": "idempotency-recover-commit",
                "principal_ref": "AUTHORITY-001",
                "authority_ref": "authority-check:recover-commit",
                "action": "ACTIVATE",
                "target_ref": "HTTP-RECOVER-COMMIT-PROCESS",
                "purpose": "recover committed IVM transition after lost acknowledgement",
                "jurisdiction": "UG-DEMO",
                "submitted_at": "2026-10-27T10:00:00+03:00",
                "payload_digest": "sha256:" + ("a" * 64),
                "rule_pack_version": "KIN-SYS-DEMO-RULES:1.0",
                "evidence": {
                    "consignment_id": "HTTP-RECOVER-COMMIT-PROCESS",
                    "commodity": "SYNTHETIC_MAIZE",
                    "origin": "UGANDA-DEMO",
                    "destination": "BORDER-DEMO",
                    "rule_refs": ["SYNTHETIC-RULE-1"],
                },
            }
            payload["payload_digest"] = compute_payload_digest(payload)

            pipeline1 = KinSysProtocolPipeline(cee_database=cee_db, ivm_database=ivm_db)
            app1 = create_app(pipeline=pipeline1, ingress_database=ingress_db)
            original_execute = pipeline1.ivm.execute

            def commit_then_lose_ack(event, **kwargs):
                decision = original_execute(event, **kwargs)
                if decision.status == "ACCEPTED" and not decision.duplicate:
                    raise RuntimeError("SIMULATED_PROCESS_FAILURE_AFTER_IVM_COMMIT")
                return decision

            pipeline1.ivm.execute = commit_then_lose_ack
            try:
                failed_response, failed_body = self.request_with_app(
                    app1, "/protocol/instructions", "POST", payload
                )
                self.assertEqual(failed_response["status"], 500)
                self.assertEqual(
                    app1.ingress.inspect(payload["operation_id"])["phase"],
                    "UNKNOWN_REQUIRES_RECONCILIATION",
                )
                self.assertEqual(
                    app1.pipeline.cee.get(payload["operation_id"])["phase"], "COMMITTING"
                )
                self.assertEqual(
                    app1.pipeline.ivm.process(payload["target_ref"])["state"], "ACTIVE"
                )
                self.assertEqual(
                    len(app1.pipeline.ivm.history(payload["target_ref"])), 1
                )
            finally:
                app1.close()

            pipeline2 = KinSysProtocolPipeline(cee_database=cee_db, ivm_database=ivm_db)
            app2 = create_app(pipeline=pipeline2, ingress_database=ingress_db)
            try:
                recovery_response, recovery_body = self.request_with_app(
                    app2,
                    "/protocol/operations/HTTP-RECOVER-COMMIT-1/recover",
                    "POST",
                )
                self.assertEqual(recovery_response["status"], 200)
                self.assertEqual(recovery_body["status"], "PASSED", recovery_body)
                self.assertFalse(recovery_body["recovery_required"])
                self.assertTrue(recovery_body["result"]["recovery"]["recovered"])
                self.assertEqual(
                    recovery_body["result"]["pipeline_result"]["status"], "COMMITTED"
                )
                self.assertEqual(
                    app2.pipeline.cee.get(payload["operation_id"])["phase"], "FINALIZED"
                )
                self.assertEqual(
                    app2.pipeline.ivm.process(payload["target_ref"])["state"], "ACTIVE"
                )
                self.assertEqual(
                    len(app2.pipeline.ivm.history(payload["target_ref"])), 1
                )
                audit = app2.ingress.recovery_events(payload["operation_id"])
                self.assertEqual(
                    [event["action"] for event in audit],
                    ["RECOVERY_STARTED", "RECOVERY_COMPLETED"],
                )
                replacement_response, replacement_body = self.request_with_app(
                    app2,
                    "/protocol/operations/HTTP-RECOVER-COMMIT-1/recover",
                    "POST",
                    {"operation_id": "DIFFERENT-OPERATION", "action": "REPLACE"},
                )
                self.assertEqual(replacement_response["status"], 400)
                self.assertEqual(
                    replacement_body["error"]["code"], "RECOVERY_BODY_NOT_ALLOWED"
                )
                self.assertEqual(
                    len(app2.pipeline.ivm.history(payload["target_ref"])), 1
                )
                audit_response, audit_body = self.request_with_app(
                    app2,
                    "/protocol/operations/HTTP-RECOVER-COMMIT-1/recovery-events",
                    "GET",
                )
                self.assertEqual(audit_response["status"], 200)
                self.assertEqual(audit_body["count"], 2)
                self.assertEqual(
                    [event["action"] for event in audit_body["events"]],
                    ["RECOVERY_STARTED", "RECOVERY_COMPLETED"],
                )
                self.assertNotIn("exception_message", audit_body["events"][-1]["evidence"])
                audit_response, audit_body = self.request_with_app(
                    app2,
                    "/protocol/operations/HTTP-RECOVER-COMMIT-1/recovery-events",
                    "GET",
                )
                self.assertEqual(audit_response["status"], 200)
                self.assertEqual(audit_body["count"], 2)
                self.assertEqual(
                    [event["action"] for event in audit_body["events"]],
                    ["RECOVERY_STARTED", "RECOVERY_COMPLETED"],
                )
                # The public audit projection omits raw exception messages.
                self.assertNotIn("reason", audit_body["events"][0]["evidence"])
            finally:
                app2.close()

    def test_v01_digest_tampering_is_rejected_before_minah_or_journal_admission(self):
        calls = []

        class RecordingMinah:
            def review(self, evidence):
                calls.append("MINAH")
                return DeterministicMinahAdapter().review(evidence)

        app = create_app(minah_adapter=RecordingMinah())
        self.addCleanup(app.close)
        payload = {
            "contract_version": "kin-sys-governed-contract-0.1",
            "request_id": "client-request-contract-tamper",
            "operation_id": "HTTP-CONTRACT-TAMPER-1",
            "idempotency_key": "idempotency-contract-tamper",
            "principal_ref": "AUTHORITY-001",
            "authority_ref": "authority-check:original",
            "action": "ACTIVATE",
            "target_ref": "HTTP-CONTRACT-TAMPER-PROCESS",
            "purpose": "digest tamper conformance test",
            "jurisdiction": "UG-DEMO",
            "submitted_at": "2026-10-27T10:00:00+03:00",
            "payload_digest": "sha256:" + ("c" * 64),
            "rule_pack_version": "KIN-SYS-DEMO-RULES:1.0",
            "evidence": {
                "consignment_id": "HTTP-CONTRACT-TAMPER-PROCESS",
                "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
                "rule_refs": ["SYNTHETIC-RULE-1"],
            },
        }
        payload["payload_digest"] = compute_payload_digest(payload)
        payload["authority_ref"] = "authority-check:tampered-without-rehash"
        response, body = self.request_with_app(app, "/protocol/instructions", "POST", payload)
        self.assertEqual(response["status"], 422)
        self.assertEqual(body["error"]["code"], "INVALID_PROTOCOL_INSTRUCTION")
        self.assertEqual(calls, [])
        operation_response, operation_body = self.request_with_app(
            app, "/protocol/operations/HTTP-CONTRACT-TAMPER-1", "GET"
        )
        self.assertEqual(operation_response["status"], 404)
        self.assertEqual(operation_body["error"]["code"], "OPERATION_NOT_FOUND")


    def test_http_inspection_reconciles_lost_ingress_ack_from_finalized_cee(self):
        calls = []

        class RecordingMinah:
            def review(self, evidence):
                calls.append("MINAH")
                return DeterministicMinahAdapter().review(evidence)

        app = create_app(
            minah_adapter=RecordingMinah(),
            identity_adapter=type("Identity", (), {
                "verify_principal": lambda self, request: {"verified": True, "mode": "TEST"}
            })(),
            authority_adapter=type("Authority", (), {
                "verify_action": lambda self, request: {"verified": True, "mode": "TEST"}
            })(),
        )
        self.addCleanup(app.close)
        payload = {
            "contract_version": "kin-sys-governed-contract-0.1",
            "request_id": "client-request-reconcile-1",
            "operation_id": "HTTP-RECONCILE-1",
            "idempotency_key": "idempotency-reconcile-1",
            "principal_ref": "AUTHORITY-001",
            "authority_ref": "authority-check:reconcile",
            "action": "ACTIVATE",
            "target_ref": "HTTP-RECONCILE-PROCESS",
            "purpose": "lost ingress acknowledgement test",
            "jurisdiction": "UG-DEMO",
            "submitted_at": "2026-10-27T10:00:00+03:00",
            "payload_digest": "sha256:" + ("f" * 64),
            "rule_pack_version": "KIN-SYS-DEMO-RULES:1.0",
            "evidence": {
                "consignment_id": "HTTP-RECONCILE-PROCESS",
                "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
                "rule_refs": ["SYNTHETIC-RULE-1"],
            },
        }
        payload["payload_digest"] = compute_payload_digest(payload)
        original_finish = app.ingress._finish

        def lose_ingress_ack(operation_id, result):
            raise RuntimeError("simulated lost ingress acknowledgement")

        app.ingress._finish = lose_ingress_ack
        response, body = self.request_with_app(app, "/protocol/instructions", "POST", payload)
        self.assertEqual(response["status"], 500)
        self.assertEqual(body["error"]["code"], "INTERNAL_ERROR")
        self.assertEqual(calls, ["MINAH"])
        self.assertEqual(app.ingress.inspect(payload["operation_id"])["phase"],
                         "UNKNOWN_REQUIRES_RECONCILIATION")

        app.ingress._finish = original_finish
        recovery_response, recovery_body = self.request_with_app(
            app, "/protocol/operations/HTTP-RECONCILE-1/recover", "POST"
        )
        self.assertEqual(recovery_response["status"], 200)
        self.assertEqual(recovery_body["status"], "PASSED", recovery_body)
        self.assertFalse(recovery_body["recovery_required"])
        self.assertTrue(recovery_body["result"]["recovery"]["recovered"])
        self.assertEqual(
            recovery_body["result"]["pipeline_result"]["status"], "COMMITTED"
        )
        # Recovery reads the durable CEE result; MINAH is not invoked again.
        self.assertEqual(calls, ["MINAH"])

    def test_http_verification_outages_return_unknown_without_cee_admission(self):
        class IdentityUnavailable:
            def verify_principal(self, request):
                raise TimeoutError("identity service unavailable")

        class IdentityAvailable:
            def verify_principal(self, request):
                return {"verified": True, "mode": "TEST_ADAPTER"}

        class AuthorityUnavailable:
            def verify_action(self, request):
                raise TimeoutError("authority service unavailable")

        base_payload = {
            "event_type": "ACTIVATE",
            "jurisdiction": "UG-DEMO",
            "rule_set": "KIN-SYS-DEMO-RULES",
            "rule_version": "1.0",
            "evidence": {
                "consignment_id": "HTTP-VERIFY-OUTAGE",
                "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
                "rule_refs": ["SYNTHETIC-RULE-1"],
            },
        }
        scenarios = (
            ("identity", IdentityUnavailable(), None, "IDENTITY_VERIFICATION_UNAVAILABLE"),
            ("authority", IdentityAvailable(), AuthorityUnavailable(), "AUTHORITY_VERIFICATION_UNAVAILABLE"),
        )
        for name, identity_adapter, authority_adapter, reason in scenarios:
            with self.subTest(gate=name):
                app = create_app(enable_legacy_demo_routes=True, 
                    identity_adapter=identity_adapter,
                    authority_adapter=authority_adapter,
                )
                try:
                    payload = {
                        **base_payload,
                        "operation_id": f"HTTP-{name.upper()}-OUTAGE-1",
                        "event_id": f"HTTP-{name.upper()}-OUTAGE-EVENT-1",
                        "process_id": f"HTTP-{name.upper()}-OUTAGE-PROCESS-1",
                        "actor_id": "AUTHORITY-001",
                        "evidence": {
                            **base_payload["evidence"],
                            "consignment_id": f"HTTP-{name.upper()}-OUTAGE-PROCESS-1",
                        },
                    }
                    response, body = self.request_with_app(
                        app, "/protocol/instructions", "POST", payload
                    )
                    self.assertEqual(response["status"], 200)
                    self.assertEqual(body["status"], "UNKNOWN")
                    self.assertEqual(body["decision"]["protocol_decision"], "UNKNOWN")
                    self.assertEqual(body["decision"]["external_effect_status"], "NOT_ATTEMPTED")
                    self.assertEqual(body["result"]["reason"], reason)
                    self.assertIsNone(body["result"]["ivm_decision"])
                    phases = [event["phase"] for event in body["result"]["cee_events"]]
                    self.assertNotIn("ADMITTED", phases)
                    self.assertNotIn("EXECUTING", phases)
                    if name == "identity":
                        self.assertNotIn("AUTHORITY_VERIFIED", phases)
                finally:
                    app.close()


    def test_http_minah_deferral_stops_before_identity_authority_and_ivm(self):
        calls = []

        class NeedsInformationMinah:
            def review(self, evidence):
                calls.append("MINAH")
                return {
                    "status": "NEEDS_INFORMATION",
                    "missing_fields": ["invoice_reference"],
                    "source_refs": ["TEST-POLICY-1"],
                    "uncertainty": "MEDIUM",
                    "may_authorise": False,
                    "model_version": "deferral-test-minah",
                }

        class MustNotRun:
            def verify_principal(self, request):
                calls.append("IDENTITY")
                raise AssertionError("identity must not run after MINAH deferral")
            def verify_action(self, request):
                calls.append("AUTHORITY")
                raise AssertionError("authority must not run after MINAH deferral")

        app = create_app(enable_legacy_demo_routes=True, 
            minah_adapter=NeedsInformationMinah(),
            identity_adapter=MustNotRun(),
            authority_adapter=MustNotRun(),
        )
        self.addCleanup(app.close)
        payload = {
            "operation_id": "HTTP-MINAH-DEFER-1",
            "event_id": "HTTP-MINAH-DEFER-EVENT-1",
            "process_id": "HTTP-MINAH-DEFER-PROCESS-1",
            "actor_id": "AUTHORITY-001",
            "event_type": "ACTIVATE",
            "jurisdiction": "UG-DEMO",
            "rule_set": "KIN-SYS-DEMO-RULES",
            "rule_version": "1.0",
            "evidence": {
                "consignment_id": "HTTP-MINAH-DEFER-PROCESS-1",
                "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
                "rule_refs": ["SYNTHETIC-RULE-1"],
            },
        }
        response, body = self.request_with_app(app, "/protocol/instructions", "POST", payload)
        self.assertEqual(response["status"], 200)
        self.assertEqual(body["status"], "DEFERRED")
        self.assertEqual(body["decision"]["protocol_decision"], "DEFERRED")
        self.assertEqual(body["decision"]["external_effect_status"], "NOT_ATTEMPTED")
        self.assertEqual(calls, ["MINAH"])
        self.assertIsNone(body["result"]["ivm_decision"])
        phases = [event["phase"] for event in body["result"]["cee_events"]]
        self.assertNotIn("IDENTITY_VERIFIED", phases)
        self.assertNotIn("AUTHORITY_VERIFIED", phases)
        self.assertNotIn("ADMITTED", phases)

    def test_http_minah_rejection_stops_before_identity_authority_and_execution(self):
        calls = []

        class RejectingMinah:
            def review(self, evidence):
                calls.append("MINAH")
                return {
                    "status": "REJECTED",
                    "missing_fields": [],
                    "source_refs": ["TEST-POLICY-1"],
                    "uncertainty": "LOW",
                    "may_authorise": False,
                    "model_version": "rejecting-test-minah",
                }

        class MustNotRunIdentity:
            def verify_principal(self, request):
                calls.append("IDENTITY")
                raise AssertionError("identity must not run after MINAH rejection")

        class MustNotRunAuthority:
            def verify_action(self, request):
                calls.append("AUTHORITY")
                raise AssertionError("authority must not run after MINAH rejection")

        app = create_app(enable_legacy_demo_routes=True, 
            minah_adapter=RejectingMinah(),
            identity_adapter=MustNotRunIdentity(),
            authority_adapter=MustNotRunAuthority(),
        )
        self.addCleanup(app.close)
        payload = {
            "operation_id": "HTTP-MINAH-REJECT-1",
            "event_id": "HTTP-MINAH-REJECT-EVENT-1",
            "process_id": "HTTP-MINAH-REJECT-PROCESS-1",
            "actor_id": "AUTHORITY-001",
            "event_type": "ACTIVATE",
            "jurisdiction": "UG-DEMO",
            "rule_set": "KIN-SYS-DEMO-RULES",
            "rule_version": "1.0",
            "evidence": {
                "consignment_id": "HTTP-MINAH-REJECT-PROCESS-1",
                "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
                "rule_refs": ["SYNTHETIC-RULE-1"],
            },
        }
        response, body = self.request_with_app(app, "/protocol/instructions", "POST", payload)
        self.assertEqual(response["status"], 200)
        self.assertEqual(body["status"], "REJECTED")
        self.assertEqual(body["decision"]["protocol_decision"], "REJECTED")
        self.assertEqual(body["decision"]["external_effect_status"], "NOT_ATTEMPTED")
        self.assertEqual(calls, ["MINAH"])
        self.assertIsNone(body["result"]["ivm_decision"])
        phases = [event["phase"] for event in body["result"]["cee_events"]]
        self.assertNotIn("IDENTITY_VERIFIED", phases)
        self.assertNotIn("AUTHORITY_VERIFIED", phases)
        self.assertNotIn("ADMITTED", phases)

    def test_http_ingress_authority_denial_stops_before_cee_admission_and_ivm(self):
        calls = []

        class RecordingMinah:
            def review(self, evidence):
                calls.append("MINAH")
                return DeterministicMinahAdapter().review(evidence)

        class RecordingIdentity:
            def verify_principal(self, request):
                calls.append("IDENTITY")
                return {"verified": True, "mode": "TEST_ADAPTER"}

        class DenyAuthority:
            def verify_action(self, request):
                calls.append("AUTHORITY")
                return {"verified": False, "mode": "TEST_DENY"}

        app = create_app(enable_legacy_demo_routes=True, 
            minah_adapter=RecordingMinah(),
            identity_adapter=RecordingIdentity(),
            authority_adapter=DenyAuthority(),
        )
        self.addCleanup(app.close)
        payload = {
            "operation_id": "HTTP-ADAPTER-DENY-1",
            "event_id": "HTTP-ADAPTER-DENY-EVENT-1",
            "process_id": "HTTP-ADAPTER-DENY-PROCESS-1",
            "actor_id": "AUTHORITY-001",
            "event_type": "ACTIVATE",
            "jurisdiction": "UG-DEMO",
            "rule_set": "KIN-SYS-DEMO-RULES",
            "rule_version": "1.0",
            "evidence": {
                "consignment_id": "HTTP-ADAPTER-DENY-PROCESS-1",
                "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
                "rule_refs": ["SYNTHETIC-RULE-1"],
            },
        }
        response, body = self.request_with_app(app, "/protocol/instructions", "POST", payload)
        self.assertEqual(response["status"], 200)
        self.assertEqual(body["status"], "REJECTED")
        self.assertEqual(body["decision"]["protocol_decision"], "REJECTED")
        self.assertEqual(body["decision"]["external_effect_status"], "NOT_ATTEMPTED")
        self.assertEqual(body["result"]["reason"], "AUTHORITY_NOT_VERIFIED")
        self.assertEqual(calls, ["MINAH", "IDENTITY", "AUTHORITY"])
        phases = [event["phase"] for event in body["result"]["cee_events"]]
        self.assertNotIn("ADMITTED", phases)
        self.assertIsNone(body["result"]["ivm_decision"])

    def test_protocol_operation_can_be_inspected_after_completion(self):
        payload = {
            "operation_id": "HTTP-INSPECT-1",
            "event_id": "HTTP-INSPECT-EVENT-1",
            "process_id": "HTTP-INSPECT-PROCESS-1",
            "actor_id": "AUTHORITY-001",
            "event_type": "ACTIVATE",
            "jurisdiction": "UG-DEMO",
            "rule_set": "KIN-SYS-DEMO-RULES",
            "rule_version": "1.0",
            "evidence": {
                "consignment_id": "HTTP-INSPECT-PROCESS-1",
                "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
            },
        }
        created, _ = self.request("/protocol/instructions", "POST", payload)
        self.assertEqual(created["status"], 200)
        response, body = self.request("/protocol/operations/HTTP-INSPECT-1")
        self.assertEqual(response["status"], 200)
        self.assertEqual(body["status"], "FINALIZED")
        self.assertFalse(body["operation"]["resumable"])
        self.assertEqual(body["operation"]["result"]["status"], "COMMITTED")
        self.assertTrue(body["operation"]["ivm_event"])

    def test_inspection_of_unknown_operation_is_not_found(self):
        response, body = self.request("/protocol/operations/DOES-NOT-EXIST")
        self.assertEqual(response["status"], 404)
        self.assertEqual(body["error"]["code"], "OPERATION_NOT_FOUND")

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

    def test_cee_case_route_runs_institutional_ivm_payment_path_and_inspection(self):
        payload = {
            "operation_id": "HTTP-CEE-CASE-1",
            "case_id": "HTTP-CEE-CONSIGNMENT-1",
            "jurisdiction": "UG-DEMO",
            "evidence": {
                "consignment_id": "HTTP-CEE-CONSIGNMENT-1",
                "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
                "rule_refs": ["SYNTHETIC-RULE-1"],
            },
            "origin_actor": "ORIGIN-A",
            "destination_actor": "DESTINATION-B",
            "execution_actor": "AUTHORITY-001",
            "origin_decision": "APPROVE",
            "destination_decision": "APPROVE",
            "declared_value_minor": 1000000,
            "payment_outcome": "CONFIRMED",
            "exempt": False,
            "revoke_origin_before_decision": False,
        }
        response, body = self.request("/cee/cases", "POST", payload)
        self.assertEqual(response["status"], 200)
        self.assertEqual(body["status"], "COMPLETED_WITH_SIMULATED_SETTLEMENT")
        self.assertFalse(body["production_ready"])
        self.assertFalse(body["result"]["funds_moved"])
        self.assertEqual(body["protocol_boundary"]["cross_store_atomicity"], False)
        inspected, operation = self.request("/cee/operations/HTTP-CEE-CASE-1")
        self.assertEqual(inspected["status"], 200)
        self.assertEqual(operation["operation"]["phase"], "FINALIZED")
        events = operation["operation"]["events"]
        components = [event["component"] for event in events]
        self.assertIn("MINAH", components)
        minah_index = next(i for i, event in enumerate(events) if event["component"] == "MINAH")
        admission_index = next(
            i for i, event in enumerate(events)
            if event["component"] == "CEE" and event["phase"] == "ADMITTED"
        )
        self.assertLess(minah_index, admission_index)
        self.assertIn("INSTITUTIONAL", components)
        self.assertIn("IVM", components)
        self.assertIn("PAYMENT", components)

    def test_cee_case_route_rejects_destination_before_payment(self):
        payload = {
            "operation_id": "HTTP-CEE-REJECT-1",
            "case_id": "HTTP-CEE-REJECT-CASE",
            "jurisdiction": "UG-DEMO",
            "evidence": {
                "consignment_id": "HTTP-CEE-REJECT-CASE",
                "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
            },
            "origin_actor": "ORIGIN-A",
            "destination_actor": "DESTINATION-B",
            "execution_actor": "AUTHORITY-001",
            "origin_decision": "APPROVE",
            "destination_decision": "REJECT",
            "declared_value_minor": 1000000,
            "payment_outcome": "CONFIRMED",
            "exempt": False,
            "revoke_origin_before_decision": False,
        }
        response, body = self.request("/cee/cases", "POST", payload)
        self.assertEqual(response["status"], 200)
        self.assertEqual(body["status"], "REJECTED")
        self.assertIsNone(body["result"]["obligation"])
        self.assertIsNone(body["result"]["payment_lifecycle"])

    def test_cee_reconciliation_route_records_versioned_finding_without_resubmission(self):
        payload = {
            "operation_id": "HTTP-CEE-RECON-1",
            "case_id": "HTTP-CEE-RECON-CASE",
            "jurisdiction": "UG-DEMO",
            "evidence": {
                "consignment_id": "HTTP-CEE-RECON-CASE",
                "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
            },
            "origin_actor": "ORIGIN-A",
            "destination_actor": "DESTINATION-B",
            "execution_actor": "AUTHORITY-001",
            "origin_decision": "APPROVE",
            "destination_decision": "APPROVE",
            "declared_value_minor": 1000000,
            "payment_outcome": "TIMEOUT_UNKNOWN",
            "exempt": False,
            "revoke_origin_before_decision": False,
        }
        created, body = self.request("/cee/cases", "POST", payload)
        self.assertEqual(created["status"], 200)
        self.assertEqual(body["status"], "PENDING_PAYMENT_RECONCILIATION")
        response, reconciled = self.request("/cee/reconciliation", "POST", {
            "operation_id": "HTTP-CEE-RECON-1",
            "event_id": "HTTP-CEE-RECON-EVENT-2",
            "finding": "NOT_SETTLED",
            "source_sequence": 2,
            "provider_reference": "SYNTHETIC-RECON-REF",
        })
        self.assertEqual(response["status"], 200)
        self.assertEqual(reconciled["status"], "PENDING_PAYMENT_RESOLUTION")
        self.assertEqual(reconciled["result"]["payment_lifecycle"]["recorded_status"], "RECONCILED_NOT_SETTLED")
        self.assertFalse(reconciled["protocol_boundary"]["funds_moved"])

    def test_cee_settlement_evidence_route_validates_obligation_and_is_inspectable(self):
        payload = {
            "operation_id": "HTTP-CEE-SETTLE-1",
            "case_id": "HTTP-CEE-SETTLE-CASE",
            "jurisdiction": "UG-DEMO",
            "evidence": {
                "consignment_id": "HTTP-CEE-SETTLE-CASE",
                "commodity": "SYNTHETIC_MAIZE",
                "origin": "UGANDA-DEMO",
                "destination": "BORDER-DEMO",
            },
            "origin_actor": "ORIGIN-A",
            "destination_actor": "DESTINATION-B",
            "execution_actor": "AUTHORITY-001",
            "origin_decision": "APPROVE",
            "destination_decision": "APPROVE",
            "declared_value_minor": 1000000,
            "payment_outcome": "TIMEOUT_UNKNOWN",
            "exempt": False,
            "revoke_origin_before_decision": False,
        }
        created, body = self.request("/cee/cases", "POST", payload)
        self.assertEqual(created["status"], 200)
        amount = body["result"]["obligation"]["amount_minor"]
        response, settled = self.request("/cee/settlement-evidence", "POST", {
            "operation_id": "HTTP-CEE-SETTLE-1",
            "event_id": "HTTP-CEE-SETTLE-EVENT-1",
            "settlement_reference": "SYNTHETIC-SETTLEMENT-REF",
            "amount_minor": amount,
            "currency": "UGX",
        })
        self.assertEqual(response["status"], 200)
        self.assertEqual(settled["status"], "COMPLETED_WITH_RECONCILIATION_CONFIRMED_SETTLEMENT")
        self.assertEqual(settled["protocol_boundary"]["settlement_evidence"],
                         "CALLER_SUPPLIED_SYNTHETIC_EVIDENCE_NOT_AUTHENTICATED")
        self.assertFalse(settled["result"]["funds_moved"])
        inspected, operation = self.request("/cee/operations/HTTP-CEE-SETTLE-1")
        self.assertEqual(inspected["status"], 200)
        self.assertEqual(operation["operation"]["phase"], "FINALIZED")
        self.assertIn("SETTLEMENT_EVIDENCE", [event["phase"] for event in operation["operation"]["events"]])

    def test_cee_settlement_route_rejects_wrong_amount(self):
        payload = {
            "operation_id": "HTTP-CEE-SETTLE-BAD",
            "case_id": "HTTP-CEE-SETTLE-BAD-CASE",
            "jurisdiction": "UG-DEMO",
            "evidence": {"consignment_id": "HTTP-CEE-SETTLE-BAD-CASE", "commodity": "SYNTHETIC_MAIZE"},
            "origin_actor": "ORIGIN-A",
            "destination_actor": "DESTINATION-B",
            "execution_actor": "AUTHORITY-001",
            "origin_decision": "APPROVE",
            "destination_decision": "APPROVE",
            "declared_value_minor": 1000000,
            "payment_outcome": "TIMEOUT_UNKNOWN",
            "exempt": False,
            "revoke_origin_before_decision": False,
        }
        self.request("/cee/cases", "POST", payload)
        response, body = self.request("/cee/settlement-evidence", "POST", {
            "operation_id": "HTTP-CEE-SETTLE-BAD",
            "event_id": "HTTP-CEE-SETTLE-BAD-EVENT",
            "settlement_reference": "SYNTHETIC-SETTLEMENT-REF",
            "amount_minor": 1,
            "currency": "UGX",
        })
        self.assertEqual(response["status"], 422)
        self.assertEqual(body["error"]["code"], "INVALID_SETTLEMENT_EVIDENCE")

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
