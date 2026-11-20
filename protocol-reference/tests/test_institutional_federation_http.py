# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import io
import json
import unittest

from institutional_federation_http import (
    FEDERATED_ROUTE, MAX_FEDERATED_BODY_BYTES,
    create_federation_http_app,
)


class RecordingReceiver:
    def __init__(self, result=None, error=None):
        self.result = result or {
            "receiver_decision": {"protocol_decision": "PASSED"},
            "message_verified": True,
            "origin_authority_inherited": False,
        }
        self.error = error
        self.envelopes = []

    def receive(self, envelope):
        self.envelopes.append(envelope)
        if self.error:
            raise self.error
        return self.result


def invoke(app, *, method="POST", path=FEDERATED_ROUTE,
           body=None, content_type="application/json", length=None):
    raw = body if isinstance(body, bytes) else (
        json.dumps(body).encode("utf-8") if body is not None else b""
    )
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": str(len(raw) if length is None else length),
        "wsgi.input": io.BytesIO(raw),
    }
    response = b"".join(app(environ, start_response))
    captured["body"] = json.loads(response.decode("utf-8"))
    return captured


class InstitutionalFederationHttpTests(unittest.TestCase):
    def setUp(self):
        self.receiver = RecordingReceiver()
        self.app = create_federation_http_app(self.receiver)

    def test_valid_envelope_is_passed_unchanged_to_receiver(self):
        envelope = {"message_id": "MSG-1", "signature": "opaque", "instruction": {"x": 1}}
        response = invoke(self.app, body=envelope)
        self.assertTrue(response["status"].startswith("200 "))
        self.assertEqual(self.receiver.envelopes, [envelope])
        self.assertEqual(response["body"]["status"], "PASSED")
        self.assertFalse(response["body"]["production_ready"])
        self.assertEqual(response["headers"]["Cache-Control"], "no-store")

    def test_wrong_path_is_not_exposed(self):
        response = invoke(self.app, path="/")
        self.assertTrue(response["status"].startswith("404 "))
        self.assertEqual(self.receiver.envelopes, [])

    def test_non_post_method_is_rejected(self):
        response = invoke(self.app, method="GET")
        self.assertTrue(response["status"].startswith("405 "))
        self.assertEqual(self.receiver.envelopes, [])

    def test_non_json_content_type_is_rejected(self):
        response = invoke(self.app, body={"x": 1}, content_type="text/plain")
        self.assertTrue(response["status"].startswith("415 "))
        self.assertEqual(self.receiver.envelopes, [])

    def test_missing_length_is_rejected(self):
        response = invoke(self.app, body={"x": 1}, length="")
        self.assertTrue(response["status"].startswith("400 "))
        self.assertEqual(self.receiver.envelopes, [])

    def test_oversized_body_is_rejected_before_reading(self):
        response = invoke(
            self.app, body=b"x", content_type="application/json",
            length=MAX_FEDERATED_BODY_BYTES + 1,
        )
        self.assertTrue(response["status"].startswith("413 "))
        self.assertEqual(self.receiver.envelopes, [])

    def test_invalid_json_is_rejected(self):
        response = invoke(self.app, body=b"{", content_type="application/json")
        self.assertTrue(response["status"].startswith("400 "))
        self.assertEqual(self.receiver.envelopes, [])

    def test_receiver_rejection_does_not_leak_internal_exception(self):
        self.app = create_federation_http_app(
            RecordingReceiver(error=RuntimeError("PRIVATE_KEY_DATABASE_PATH"))
        )
        response = invoke(self.app, body={"signed": "envelope"})
        self.assertTrue(response["status"].startswith("409 "))
        self.assertNotIn("PRIVATE_KEY_DATABASE_PATH", json.dumps(response["body"]))


if __name__ == "__main__":
    unittest.main()
