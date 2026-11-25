# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
from __future__ import annotations

import json
import unittest

from federation_https_transport import (
    FederationHTTPSJSONTransport, FederationHTTPTransportError,
    FederationOutboxDeliveryWorker,
)


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = json.dumps(payload or {
            "request_id": "req-1",
            "result": {
                "delivery_status": "COMPLETED",
                "receiver_acknowledgement": {"claims": {}, "response": {}, "signature": "x"},
            },
        }).encode()
    def read(self, size=-1):
        return self.payload[:size]
    

class FakeConnection:
    def __init__(self, response=None, **kwargs):
        self.response = response or FakeResponse()
        self.sock = None
        self.request_args = None
        self.closed = False
    def request(self, *args, **kwargs):
        self.request_args = (args, kwargs)
    def getresponse(self):
        return self.response
    def close(self):
        self.closed = True


class FederationHTTPSTransportTests(unittest.TestCase):
    def setUp(self):
        self.envelope = {
            "claims": {"receiver_installation_id": "INSTALL-RW-01"},
            "signature": "signed",
        }
        self.connections = []
        def factory(host, port, **kwargs):
            item = FakeConnection()
            self.connections.append(item)
            return item
        self.transport = FederationHTTPSJSONTransport(
            destinations={"INSTALL-RW-01": "https://receiver.example"},
            resolver=lambda host, port, type: [
                (2, 1, 6, "", ("8.8.8.8", port))
            ],
            connection_factory=factory,
        )

    def test_posts_only_to_registry_destination_and_returns_receiver_receipt(self):
        result = self.transport.post("INSTALL-RW-01", self.envelope)
        self.assertEqual(result["delivery_status"], "COMPLETED")
        args, kwargs = self.connections[0].request_args
        self.assertEqual(args[0:2], ("POST", "/federation/v1/messages"))
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        self.assertTrue(self.connections[0].closed)

    def test_rejects_destination_not_in_registry(self):
        with self.assertRaisesRegex(FederationHTTPTransportError, "DESTINATION_OR_ENVELOPE"):
            self.transport.post("UNREGISTERED", self.envelope)

    def test_rejects_private_or_loopback_dns_answer(self):
        transport = FederationHTTPSJSONTransport(
            destinations={"INSTALL-RW-01": "https://receiver.example"},
            resolver=lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))],
            connection_factory=lambda *a, **k: self.fail("must not connect"),
        )
        with self.assertRaisesRegex(FederationHTTPTransportError, "IP_BLOCKED"):
            transport.post("INSTALL-RW-01", self.envelope)

    def test_rejects_non_https_and_unexpected_paths(self):
        for url in ("http://receiver.example", "https://receiver.example/other",
                    "https://user:pass@receiver.example"):
            transport = FederationHTTPSJSONTransport(
                destinations={"INSTALL-RW-01": url},
                resolver=self.transport._resolver,
                connection_factory=lambda *a, **k: self.fail("must not connect"),
            )
            with self.assertRaises(FederationHTTPTransportError):
                transport.post("INSTALL-RW-01", self.envelope)

    def test_rejects_redirects_and_non_success_statuses(self):
        for status in (301, 302, 400, 500):
            transport = FederationHTTPSJSONTransport(
                destinations={"INSTALL-RW-01": "https://receiver.example"},
                resolver=self.transport._resolver,
                connection_factory=lambda *a, _status=status, **k: FakeConnection(
                    response=FakeResponse(status=_status, payload={})
                ),
            )
            with self.assertRaisesRegex(FederationHTTPTransportError, "REMOTE_HTTP"):
                transport.post("INSTALL-RW-01", self.envelope)

    def test_delivery_worker_requires_signed_receipt_and_delegates_verification(self):
        class Outbox:
            def __init__(self): self.acks=[]; self.retries=[]
            def acknowledge(self, **kwargs):
                self.acks.append(kwargs)
                return {"status": "ACKNOWLEDGED"}
            def mark_retryable(self, **kwargs):
                self.retries.append(kwargs)
                return True
        class Transport:
            def post(self, receiver, envelope):
                return {"delivery_status": "COMPLETED",
                        "receiver_acknowledgement": {"signature": "signed"}}
        outbox=Outbox()
        worker=FederationOutboxDeliveryWorker(outbox=outbox, transport=Transport())
        claim={
            "sender_tenant_id":"tenant-ug", "sender_installation_id":"INSTALL-UG-01",
            "message_id":"MSG-1", "receiver_installation_id":"INSTALL-RW-01",
            "envelope":self.envelope, "lease_owner":"worker-1",
        }
        result=worker.deliver_claim(claim)
        self.assertEqual(result["status"], "ACKNOWLEDGED")
        self.assertEqual(len(outbox.acks), 1)
        self.assertEqual(outbox.acks[0]["receipt"]["signature"], "signed")

    def test_delivery_worker_retries_when_acknowledgement_is_missing(self):
        class Outbox:
            def __init__(self): self.retries=[]
            def acknowledge(self, **kwargs): self.fail("must not acknowledge")
            def mark_retryable(self, **kwargs): self.retries.append(kwargs); return True
        class Transport:
            def post(self, receiver, envelope): return {"delivery_status":"COMPLETED"}
        outbox=Outbox()
        worker=FederationOutboxDeliveryWorker(outbox=outbox, transport=Transport())
        claim={
            "sender_tenant_id":"tenant-ug", "sender_installation_id":"INSTALL-UG-01",
            "message_id":"MSG-2", "receiver_installation_id":"INSTALL-RW-01",
            "envelope":self.envelope, "lease_owner":"worker-2",
        }
        result=worker.deliver_claim(claim)
        self.assertEqual(result["status"], "RETRYABLE")
        self.assertEqual(len(outbox.retries), 1)


if __name__ == "__main__":
    unittest.main()
