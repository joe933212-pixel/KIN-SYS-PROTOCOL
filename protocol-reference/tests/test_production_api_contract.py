# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Structural conformance checks for the proposed production API contract.

These checks validate the OpenAPI document only. They do not prove routes are
implemented, served, authenticated, or secure.
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_PATH = ROOT / "api" / "openapi.json"


class ProductionApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = json.loads(API_PATH.read_text(encoding="utf-8"))

    def test_openapi_document_declares_supported_version_and_title(self):
        self.assertEqual(self.api["openapi"], "3.1.0")
        self.assertEqual(self.api["info"]["title"], "KIN-SYS Production Integration API")
        self.assertTrue(self.api["info"]["version"].endswith("-draft"))

    def test_every_route_is_explicitly_a_draft_not_a_live_route(self):
        self.assertGreaterEqual(len(self.api["paths"]), 20)
        operation_ids = []
        for path, path_item in self.api["paths"].items():
            for method, operation in path_item.items():
                if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                    continue
                operation_ids.append(operation["operationId"])
                self.assertEqual(
                    operation["x-implementation-status"],
                    "CONTRACT_DRAFT_NOT_ROUTED",
                    f"{method.upper()} {path} must not imply it is implemented",
                )
                self.assertIn("200", operation["responses"])
                self.assertIn("400", operation["responses"])
                self.assertIn("503", operation["responses"])
        self.assertEqual(len(operation_ids), len(set(operation_ids)))

    def test_all_local_schema_references_resolve(self):
        schemas = self.api["components"]["schemas"]
        parameters = self.api["components"]["parameters"]

        def check(value, location):
            if isinstance(value, dict):
                ref = value.get("$ref")
                if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                    name = ref.rsplit("/", 1)[1]
                    self.assertIn(name, schemas, f"{location}: unresolved schema {ref}")
                if isinstance(ref, str) and ref.startswith("#/components/parameters/"):
                    name = ref.rsplit("/", 1)[1]
                    self.assertIn(name, parameters, f"{location}: unresolved parameter {ref}")
                for key, child in value.items():
                    check(child, f"{location}/{key}")
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    check(child, f"{location}[{index}]")

        check(self.api["paths"], "paths")

    def test_protected_operations_use_declared_bearer_security(self):
        for path, path_item in self.api["paths"].items():
            for method, operation in path_item.items():
                if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                    continue
                if path == "/system/health":
                    self.assertEqual(operation.get("security"), [])
                else:
                    self.assertTrue(
                        operation.get("security", self.api.get("security")),
                        f"{method.upper()} {path} lacks declared security",
                    )
        self.assertIn("bearerAuth", self.api["components"]["securitySchemes"])

    def test_production_gates_are_recorded(self):
        gates = self.api["x-critical-production-gates"]
        self.assertGreaterEqual(len(gates), 8)
        self.assertTrue(any("payment" in gate.lower() for gate in gates))
        self.assertTrue(any("MINAH" in gate for gate in gates))
        self.assertTrue(any("Nexus Pod" in gate for gate in gates))


if __name__ == "__main__":
    unittest.main()
