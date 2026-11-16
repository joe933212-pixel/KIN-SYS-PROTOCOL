# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Adversarial tests for the authenticated federation HTTP ingress."""
from __future__ import annotations

import io
import json
import unittest

from federation_http_transport import (
    FEDERATION_PATH, FederationHttpIngressApplication,
)


class RecordingReceiver:
    def __init__(self):
        self.envelopes = []

    def receive(self, envelope):
        self.envelopes.append(envelope)
        return {"delivery_status": "COMPLETED", "message_verified": True}


class FederationHttpIngressTests(unittest.TestCase):
    def setUp(self):
        self.receiver = RecordingReceiver()
        self.app = FederationHttpIngressApplication(
            receiver=self.receiver,
            peer_identity_resolver=lambda environ: environ.get("verified_peer_installation"),
        )
        self.envelope = {
            "claims": {"issuer_installation_id": "BANK-UG-A"},
            "signature": "x" * 86,
        }

    def request(self, *, method="POST", path=FEDERATION_PATH, body=None,
                content_type="application/json", peer="BANK-UG-A", length=None):
        payload = json.dumps(self.envelope).encode() if body is None else body
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "CONTENT_TYPE": content_type,
            "CONTENT_LENGTH": str(len(payload)) if length is None else str(length),
            "wsgi.input": io.BytesIO(payload),
            "verified_peer_installation": peer,
        }
        captured = {}
        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = dict(headers)
        response = b"".join(self.app(environ, start_response))
        return int(captured["status"].split()[0]), json.loads(response), captured["headers"]

    def test_authenticated_peer_envelope_reaches_receiver(self):
        status, body, headers = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(len(self.receiver.envelopes), 1)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(body["result"]["delivery_status"], "COMPLETED")

    def test_peer_identity_must_match_signed_issuer(self):
        status, body, _ = self.request(peer="OTHER-INSTALLATION")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "TRANSPORT_ISSUER_MISMATCH")
        self.assertEqual(self.receiver.envelopes, [])

    def test_missing_authenticated_peer_fails_closed(self):
        status, _, _ = self.request(peer=None)
        self.assertEqual(status, 401)
        self.assertEqual(self.receiver.envelopes, [])

    def test_caller_supplied_http_header_is_not_peer_identity(self):
        payload = json.dumps(self.envelope).encode()
        environ = {
            "REQUEST_METHOD": "POST", "PATH_INFO": FEDERATION_PATH,
            "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(payload)),
            "wsgi.input": io.BytesIO(payload),
            "HTTP_X_KIN_SYS_PEER_INSTALLATION": "BANK-UG-A",
        }
        captured = {}
        def start_response(status, headers):
            captured["status"] = status
        b"".join(self.app(environ, start_response))
        self.assertEqual(int(captured["status"].split()[0]), 401)
        self.assertEqual(self.receiver.envelopes, [])

    def test_oversized_body_is_rejected_before_read(self):
        status, _, _ = self.request(length=64 * 1024 + 1)
        self.assertEqual(status, 413)
        self.assertEqual(self.receiver.envelopes, [])

    def test_content_type_and_route_are_strict(self):
        status, _, _ = self.request(content_type="text/plain")
        self.assertEqual(status, 415)
        status, _, _ = self.request(path="/other")
        self.assertEqual(status, 404)
        self.assertEqual(self.receiver.envelopes, [])

    def test_duplicate_json_members_are_rejected(self):
        raw = b'{"claims":{"issuer_installation_id":"BANK-UG-A","issuer_installation_id":"EVIL"},"signature":"x"}'
        status, _, _ = self.request(body=raw)
        self.assertEqual(status, 400)
        self.assertEqual(self.receiver.envelopes, [])

    def test_truncated_body_is_rejected(self):
        status, _, _ = self.request(length=1000)
        self.assertEqual(status, 400)
        self.assertEqual(self.receiver.envelopes, [])

    def test_receiver_errors_are_not_exposed(self):
        class BrokenReceiver:
            def receive(self, envelope):
                raise RuntimeError("secret database password")
        app = FederationHttpIngressApplication(
            receiver=BrokenReceiver(),
            peer_identity_resolver=lambda environ: "BANK-UG-A",
        )
        payload = json.dumps(self.envelope).encode()
        environ = {
            "REQUEST_METHOD": "POST", "PATH_INFO": FEDERATION_PATH,
            "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(payload)),
            "wsgi.input": io.BytesIO(payload),
        }
        captured = {}
        def start_response(status, headers):
            captured["status"] = status
        response = b"".join(app(environ, start_response))
        self.assertEqual(int(captured["status"].split()[0]), 400)
        self.assertNotIn(b"secret database password", response)


if __name__ == "__main__":
    unittest.main()
