# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import io
import json
import unittest

from governed_contracts import CONTRACT_VERSION, compute_payload_digest
from http_service import create_app
from institutional_instruction_execution import InstitutionalInstructionExecutionService


class SessionResolver:
    def __init__(self):
        self.tenant_id = "tenant-ug"
        self.institution_id = "BANK-UG-01"

    def resolve(self, environ):
        return {
            "authenticated": True, "tenant_id": self.tenant_id,
            "institution_id": self.institution_id, "actor_ref": "operator:alice",
        }


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
            "identity_provider_id": "IDP-UG-01",
            "identity_provider_version": "2.0",
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
                "jurisdictions": ["UG"],
                "policy_pack_version": "BANK-RULES:1.0",
                "source_pack_version": "UG-SOURCES:2.0",
                "endpoint_ref": "secret://private",
            },
        }


class ActiveMinahResolver:
    """Synthetic live deployment registry used by execution and recovery tests."""

    def __init__(self):
        self.execution_epoch = 1
        self.leases = {}

    def resolve_active(self, *, tenant_id, deployment_id):
        if tenant_id != "tenant-ug" or deployment_id != "MINAH-UG-01":
            return None
        return {
            "tenant_id": "tenant-ug",
            "deployment_id": "MINAH-UG-01",
            "version": "9.0",
            "execution_epoch": self.execution_epoch,
            "status": "ACTIVE",
            "config": {
                "deployment_id": "MINAH-UG-01",
                "owner_ref": "tenant-ug",
                "model_ref": "model:bank/minah-v1",
                "jurisdictions": ["UG"],
                "policy_pack_version": "BANK-RULES:1.0",
                "source_pack_version": "UG-SOURCES:2.0",
            },
        }

    def acquire_execution_lease(self, *, tenant_id, deployment_id):
        deployment = self.resolve_active(
            tenant_id=tenant_id, deployment_id=deployment_id)
        if deployment is None or deployment["status"] != "ACTIVE":
            raise RuntimeError("MINAH_DEPLOYMENT_NOT_ACTIVE")
        lease = {
            "lease_id": "http-test-lease",
            "lease_token": "http-test-token",
            "tenant_id": tenant_id,
            "deployment_id": deployment_id,
            "execution_epoch": self.execution_epoch,
            "fencing_token": len(self.leases) + 1,
            "version": deployment["version"],
        }
        self.leases[lease["lease_id"]] = dict(lease)
        return lease

    def execute_under_execution_lease(self, *, lease, action):
        current = self.leases.get(lease.get("lease_id"))
        if current != lease or lease.get("execution_epoch") != self.execution_epoch:
            raise RuntimeError("MINAH_EXECUTION_LEASE_FENCED")
        return action()

    def release_execution_lease(self, *, lease):
        self.leases.pop(lease.get("lease_id"), None)


class Journal:
    def __init__(self):
        self.operations = {}
        self.audit = {}

    def get(self, operation_id):
        return self.operations.get(operation_id)

    def events(self, operation_id):
        return []

    def observations(self, operation_id):
        return list(self.audit.get(operation_id, []))

    def record_observation(self, operation_id, kind, status, detail):
        self.audit.setdefault(operation_id, []).append({
            "kind": kind, "status": status, "detail": dict(detail),
            "created_at": "2026-11-07T12:00:00+00:00",
        })


class Pipeline:
    EXECUTION_ORDER = ("MINAH", "IDENTITY", "AUTHORITY", "CEE", "FINAL_DECISION")
    SUPPORTS_INSTITUTIONAL_RUNTIME_PINS = True
    identity_provider_id = "IDP-UG-01"
    identity_provider_version = "2.0"
    jurisdiction_package_id = "JUR-UG-01"
    jurisdiction_package_version = "4.0"
    authority_profile_version = "BANK-AUTHORITY:1.0"
    minah_profile = {
        "profile_version": "kin-sys-minah-profile-0.1",
        "deployment_id": "MINAH-UG-01",
        "owner_ref": "tenant-ug",
        "model_ref": "model:bank/minah-v1",
        "jurisdictions": ["UG"],
        "policy_pack_version": "BANK-RULES:1.0",
        "source_pack_version": "UG-SOURCES:2.0",
        "may_authorise": False,
    }

    def __init__(self):
        self.requests = []
        self.cee = Journal()
        self.recoveries = []

    def execute(self, request, *, execution_guard=None):
        self.requests.append(dict(request))
        result = {
            "operation_id": request["operation_id"],
            "status": "COMMITTED",
            "reason": "SYNTHETIC_GATES_PASSED",
            "funds_moved": False,
            "private_token": "must-not-leak",
        }
        def commit_synthetic_result():
            self.cee.operations[request["operation_id"]] = {
                "operation_id": request["operation_id"],
                "request_json": dict(request),
                "request_digest": "sha256:synthetic-test-fingerprint",
                "phase": "FINALIZED",
                "result_json": dict(result),
            }
            return result
        if execution_guard is not None:
            return execution_guard(commit_synthetic_result)
        return commit_synthetic_result()

    def inspect(self, operation_id):
        operation = self.cee.get(operation_id)
        if operation is None:
            return None
        return {"operation_id": operation_id, "phase": operation["phase"], "resumable": False}

    def recover(self, operation_id, *, execution_guard=None):
        self.recoveries.append(operation_id)
        operation = self.cee.get(operation_id)
        if operation is None:
            return {"operation_id": operation_id, "status": "UNKNOWN", "reason": "OPERATION_NOT_FOUND"}
        def reconcile():
            return dict(operation["result_json"])
        if execution_guard is not None:
            return execution_guard(reconcile)
        return reconcile()


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
            minah_deployment_resolver=ActiveMinahResolver(),
        )
        self.session = SessionResolver()
        self.app = create_app(
            institutional_instruction_execution=service,
            institutional_session_resolver=self.session,
        )
        self.addCleanup(self.app.close)

    def request(self, payload, path="/institutional/instructions", method="POST"):
        raw = b"" if payload is None else json.dumps(payload).encode()
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

    def test_operation_inspection_is_tenant_scoped_and_redacts_private_configuration(self):
        response, _ = self.request({
            "institution_id": "BANK-UG-01", "binding_id": "BIND-UG-01",
            "instruction": make_instruction(),
            "evidence": {"document_ref": "evidence:http-1"},
        })
        self.assertEqual(response["status"], 200)
        response, body = self.request(
            {}, path="/institutional/instructions/OP-HTTP-1", method="GET")
        self.assertEqual(response["status"], 200, body)
        self.assertEqual(body["operation"]["runtime_pins"]["tenant_id"], "tenant-ug")
        self.assertEqual(body["operation"]["runtime_pins"]["binding_version"], "3.0")
        self.assertNotIn("minah_config", json.dumps(body))
        self.assertNotIn("endpoint_ref", json.dumps(body))
        self.assertNotIn("evidence", json.dumps(body))
        self.session.tenant_id = "tenant-ke"
        response, body = self.request(
            {}, path="/institutional/instructions/OP-HTTP-1", method="GET")
        self.assertEqual(response["status"], 404, body)
        self.session.tenant_id = "tenant-ug"
        self.session.institution_id = "BANK-KE-01"
        response, body = self.request(
            {}, path="/institutional/instructions/OP-HTTP-1", method="GET")
        self.assertEqual(response["status"], 404, body)

    def test_recovery_uses_original_operation_without_replacement_body_and_records_audit(self):
        response, _ = self.request({
            "institution_id": "BANK-UG-01", "binding_id": "BIND-UG-01",
            "instruction": make_instruction(),
            "evidence": {"document_ref": "evidence:http-1"},
        })
        self.assertEqual(response["status"], 200)
        response, body = self.request(
            None, path="/institutional/instructions/OP-HTTP-1/recover", method="POST")
        self.assertEqual(response["status"], 200, body)
        self.assertEqual(body["decision"]["protocol_decision"], "PASSED")
        self.assertEqual(self.pipeline.recoveries, ["OP-HTTP-1"])
        audit = self.pipeline.cee.observations("OP-HTTP-1")
        self.assertEqual([item["status"] for item in audit], ["REQUESTED", "COMMITTED"])
        self.assertEqual(audit[0]["detail"]["actor_ref"], "operator:alice")

    def test_recovery_rejects_replacement_request_body(self):
        response, body = self.request(
            {"replacement": True},
            path="/institutional/instructions/OP-HTTP-1/recover", method="POST")
        self.assertEqual(response["status"], 400)
        self.assertEqual(body["error"]["code"], "RECOVERY_BODY_NOT_ALLOWED")

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
        self.assertNotIn("private_token", json.dumps(body))
        self.assertEqual(
            self.pipeline.requests[0]["_institutional_runtime_binding"]["binding_id"],
            "BIND-UG-01",
        )

    def test_session_cannot_submit_for_a_different_institution(self):
        self.session.institution_id = "BANK-KE-01"
        response, body = self.request({
            "institution_id": "BANK-UG-01",
            "binding_id": "BIND-UG-01",
            "instruction": make_instruction(),
            "evidence": {"document_ref": "evidence:http-1"},
        })
        self.assertEqual(response["status"], 403)
        self.assertEqual(
            body["error"]["code"], "INSTITUTIONAL_SESSION_INSTITUTION_MISMATCH")
        self.assertEqual(self.pipeline.requests, [])

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
