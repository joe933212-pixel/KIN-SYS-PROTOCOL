# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import io
import json
import unittest

from http_service import create_app


class SessionResolver:
    def resolve(self, environ):
        return {"authenticated": True, "tenant_id": "tenant-ug"}


class RuntimeResolver:
    def __init__(self):
        self.calls = []

    def resolve(self, *, tenant_id, institution_id, binding_id):
        self.calls.append((tenant_id, institution_id, binding_id))
        return {
            "tenant_id": tenant_id, "institution_id": institution_id,
            "binding_id": binding_id, "binding_version": "3",
            "minah_deployment_id": "MINAH-UG-01", "minah_runtime_version": "9",
            "identity_provider_id": "IDP-UG-01", "identity_provider_version": "2",
            "identity_provider_config": {"issuer": "https://private-idp.example"},
            "minah_config": {
                "endpoint_ref": "https://private.example/minah",
                "secret_ref": "secret://tenant/minah",
                "model_ref": "model:bank/minah-v1",
            },
            "execution_order": ["MINAH", "IDENTITY", "AUTHORITY", "CEE", "FINAL_DECISION"],
            "runtime_admission": "ELIGIBLE_FOR_GOVERNED_EXECUTION",
        }


class InstitutionalRuntimeBindingHttpTests(unittest.TestCase):
    def setUp(self):
        self.resolver = RuntimeResolver()
        self.app = create_app(
            runtime_binding_resolver=self.resolver,
            institutional_session_resolver=SessionResolver(),
        )
        self.addCleanup(self.app.close)

    def request(self, path, method="GET"):
        env = {
            "REQUEST_METHOD": method, "PATH_INFO": path,
            "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": "0",
            "REMOTE_ADDR": "127.0.0.1", "wsgi.input": io.BytesIO(b""),
        }
        captured = {}
        def start(status, headers):
            captured["status"] = int(status.split()[0])
        result = self.app(env, start)
        return captured, json.loads(b"".join(result))

    def test_authenticated_route_resolves_session_tenant_and_redacts_runtime_secrets(self):
        response, body = self.request(
            "/institutional/runtime/BANK-UG-01/bindings/BIND-UG-01")
        self.assertEqual(response["status"], 200, body)
        self.assertEqual(self.resolver.calls, [("tenant-ug", "BANK-UG-01", "BIND-UG-01")])
        context = body["runtime_context"]
        self.assertEqual(context["runtime_admission"], "ELIGIBLE_FOR_GOVERNED_EXECUTION")
        self.assertNotIn("minah_config", context)
        self.assertNotIn("secret_ref", json.dumps(body))
        self.assertNotIn("endpoint_ref", json.dumps(body))
        self.assertNotIn("identity_provider_config", context)
        self.assertNotIn("private-idp.example", json.dumps(body))

    def test_runtime_route_is_get_only(self):
        response, body = self.request(
            "/institutional/runtime/BANK-UG-01/bindings/BIND-UG-01", method="POST")
        self.assertEqual(response["status"], 404)
        self.assertEqual(body["error"]["code"], "ROUTE_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
