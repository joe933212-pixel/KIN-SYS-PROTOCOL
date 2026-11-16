# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
from __future__ import annotations

import unittest

from federation_outbox_dispatcher import (
    FederationOutboxDispatcher, FederationOutboxDispatcherError,
)


class FederationOutboxDispatcherTests(unittest.TestCase):
    def test_processes_only_one_bounded_claimed_batch(self):
        claims = [{"message_id": "M-1"}, {"message_id": "M-2"}]

        class Outbox:
            def __init__(self): self.kwargs = None
            def claim_batch(self, **kwargs):
                self.kwargs = kwargs
                return claims

        class Worker:
            def __init__(self): self.seen = []
            def deliver_claim(self, claim):
                self.seen.append(claim)
                return {"status": "ACKNOWLEDGED" if claim["message_id"] == "M-1"
                        else "RETRYABLE"}

        outbox, worker = Outbox(), Worker()
        dispatcher = FederationOutboxDispatcher(
            outbox=outbox, delivery_worker=worker,
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            worker_id="worker-1", batch_size=2, lease_seconds=45,
        )
        result = dispatcher.run_once()
        self.assertEqual(outbox.kwargs["limit"], 2)
        self.assertEqual(outbox.kwargs["lease_seconds"], 45)
        self.assertEqual(worker.seen, claims)
        self.assertEqual(
            {key: result[key] for key in ("claimed", "acknowledged", "retryable")},
            {"claimed": 2, "acknowledged": 1, "retryable": 1},
        )

    def test_rejects_unbounded_batch_configuration(self):
        class Outbox:
            def claim_batch(self, **kwargs): return []
        class Worker:
            def deliver_claim(self, claim): return {}
        with self.assertRaisesRegex(FederationOutboxDispatcherError, "BATCH_INVALID"):
            FederationOutboxDispatcher(
                outbox=Outbox(), delivery_worker=Worker(),
                sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
                worker_id="worker-1", batch_size=101,
            )

    def test_does_not_loop_or_sleep_when_queue_is_empty(self):
        class Outbox:
            calls = 0
            def claim_batch(self, **kwargs):
                self.calls += 1
                return []
        class Worker:
            def deliver_claim(self, claim): raise AssertionError("no claims expected")
        outbox = Outbox()
        result = FederationOutboxDispatcher(
            outbox=outbox, delivery_worker=Worker(),
            sender_tenant_id="tenant-ug", sender_installation_id="INSTALL-UG-01",
            worker_id="worker-1",
        ).run_once()
        self.assertEqual(outbox.calls, 1)
        self.assertEqual(result["claimed"], 0)


if __name__ == "__main__":
    unittest.main()
