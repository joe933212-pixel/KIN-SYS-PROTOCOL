"""Tests for the removable-drive dashboard wrapper and loopback sandbox app."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from sandbox.portable_server import create_sandbox_app


class PortableSandboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kin-sys-portable-")
        self.addCleanup(self.temp.cleanup)
        self.app = create_sandbox_app(Path(self.temp.name))
        self.addCleanup(self.app.close)

    def request(self, path: str, method: str = "GET"):
        captured = {}
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "CONTENT_LENGTH": "0",
            "CONTENT_TYPE": "",
            "REMOTE_ADDR": "127.0.0.1",
            "wsgi.input": io.BytesIO(b""),
        }

        def start_response(status, headers):
            captured["status"] = int(status.split()[0])
            captured["headers"] = dict(headers)

        body = b"".join(self.app(environ, start_response))
        return captured, body

    def test_dashboard_is_served_without_external_assets(self):
        response, body = self.request("/")
        self.assertEqual(response["status"], 200)
        self.assertIn(b"KIN-SYS", body)
        self.assertIn(b"Local Test Sandbox", body)
        self.assertEqual(response["headers"]["X-Frame-Options"], "DENY")
        self.assertEqual(response["headers"]["Cache-Control"], "no-store")

    def test_api_health_is_delegated_to_reference_service(self):
        response, body = self.request("/system/health")
        self.assertEqual(response["status"], 200)
        payload = json.loads(body)
        self.assertEqual(payload["status"], "ALIVE")
        self.assertEqual(payload["service"], "kin-sys-prototype")

    def test_sandbox_uses_separate_persistent_local_stores(self):
        expected = {"cee.sqlite3", "ivm.sqlite3", "ingress.sqlite3"}
        self.assertEqual({p.name for p in Path(self.temp.name).iterdir()}, expected)

    def test_dashboard_rejects_non_get_methods(self):
        response, _ = self.request("/", "POST")
        self.assertEqual(response["status"], 405)


if __name__ == "__main__":
    unittest.main()
