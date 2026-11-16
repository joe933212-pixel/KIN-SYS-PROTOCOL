# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Bounded polling dispatcher for a PostgreSQL federation sender outbox.

Scheduling is deliberately external (for example, a supervised process or
institution-managed job runner). Each invocation claims at most one bounded
batch; network delivery occurs only after the database claim transaction ends.
"""
from __future__ import annotations

from typing import Any


class FederationOutboxDispatcherError(ValueError):
    """Invalid dispatcher configuration or collaborator contract."""


class FederationOutboxDispatcher:
    """Claim and deliver one bounded batch for one sender installation."""

    def __init__(
        self, *, outbox: Any, delivery_worker: Any,
        sender_tenant_id: str, sender_installation_id: str, worker_id: str,
        batch_size: int = 10, lease_seconds: int = 60,
    ):
        if not callable(getattr(outbox, "claim_batch", None)):
            raise FederationOutboxDispatcherError("FEDERATION_BATCH_CLAIM_REQUIRED")
        if not callable(getattr(delivery_worker, "deliver_claim", None)):
            raise FederationOutboxDispatcherError("FEDERATION_DELIVERY_WORKER_REQUIRED")
        for value in (sender_tenant_id, sender_installation_id, worker_id):
            if not isinstance(value, str) or not value:
                raise FederationOutboxDispatcherError("FEDERATION_DISPATCHER_ID_INVALID")
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise FederationOutboxDispatcherError("FEDERATION_DISPATCHER_BATCH_INVALID")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise FederationOutboxDispatcherError("FEDERATION_DISPATCHER_LEASE_INVALID")
        self.outbox = outbox
        self.delivery_worker = delivery_worker
        self.sender_tenant_id = sender_tenant_id
        self.sender_installation_id = sender_installation_id
        self.worker_id = worker_id
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds

    def run_once(self) -> dict[str, Any]:
        """Claim and process at most batch_size rows; never sleeps or loops."""
        claims = self.outbox.claim_batch(
            sender_tenant_id=self.sender_tenant_id,
            sender_installation_id=self.sender_installation_id,
            worker_id=self.worker_id,
            limit=self.batch_size,
            lease_seconds=self.lease_seconds,
        )
        if not isinstance(claims, list) or len(claims) > self.batch_size:
            raise FederationOutboxDispatcherError("FEDERATION_BATCH_CLAIM_RESPONSE_INVALID")
        results = []
        for claim in claims:
            results.append(self.delivery_worker.deliver_claim(claim))
        acknowledged = sum(
            isinstance(item, dict) and item.get("status") == "ACKNOWLEDGED"
            for item in results
        )
        retryable = sum(
            isinstance(item, dict) and item.get("status") == "RETRYABLE"
            for item in results
        )
        return {
            "claimed": len(claims),
            "acknowledged": acknowledged,
            "retryable": retryable,
            "results": results,
        }
