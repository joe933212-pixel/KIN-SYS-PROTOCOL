# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Failure-injection test for the CEE/IVM-commit to inbox-completion gap.

This test deliberately interrupts PostgreSQL inbox completion after the
receiver's local governed operation has committed. It then expires the inbox
lease, reconstructs the receiver adapter, and proves that recovery uses the
original pinned operation and returns the same deterministic signed receipt
without recording a second CEE request.
"""
from __future__ import annotations

import unittest

from test_postgresql_two_installation_federation import (
    DSN,
    PostgreSQLTwoInstallationFederationTests,
)
from institutional_federated_receiver import InstitutionalFederatedInstructionReceiver
from postgresql_federation_inbox import PostgreSQLFederationInbox


class FailOnceCompletionInbox:
    """Delegate to the durable inbox but fail once at the completion boundary."""

    def __init__(self, inbox):
        self.inbox = inbox
        self.attempted_response = None
        self.failed = False

    def __getattr__(self, name):
        return getattr(self.inbox, name)

    def complete(self, **kwargs):
        self.attempted_response = kwargs.get("response")
        if not self.failed:
            self.failed = True
            raise RuntimeError("INJECTED_FAILURE_BEFORE_INBOX_COMPLETION")
        return self.inbox.complete(**kwargs)


@unittest.skipUnless(DSN, "KIN_SYS_POSTGRES_TEST_DSN is not configured")
class PostgreSQLFederationCommitRecoveryTests(unittest.TestCase):
    def test_commit_before_inbox_completion_recovers_original_decision_and_receipt(self):
        fixture = PostgreSQLTwoInstallationFederationTests()
        fixture.setUpClass()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.addCleanup(fixture.doCleanups)

        failing_inbox = FailOnceCompletionInbox(fixture.receiver_inbox)
        interrupted_receiver = InstitutionalFederatedInstructionReceiver(
            receiver_tenant_id="tenant-rw",
            receiver_installation_id="INSTALL-RW-01",
            receiver_institution_id="BANK-RW-01",
            receiver_binding_id="BIND-RW-01",
            execution_service=fixture.receiver_runtime.service,
            trust_key_loader=fixture.receiver.trust_key_loader,
            public_key_loader=fixture.receiver.public_key_loader,
            clock=lambda: fixture.now,
            inbox=failing_inbox,
            inbox_worker_id="rw-inbox-worker-interrupted",
            inbox_lease_seconds=30,
            receiver_signing_key_id="KEY-RW-01",
            receiver_signing_key_loader=fixture.receiver.receiver_signing_key_loader,
        )

        with self.assertRaisesRegex(
            RuntimeError, "INJECTED_FAILURE_BEFORE_INBOX_COMPLETION"
        ):
            interrupted_receiver.receive(fixture.envelope)

        interrupted_response = failing_inbox.attempted_response
        self.assertIsInstance(interrupted_response, dict)
        self.assertIn("receiver_acknowledgement", interrupted_response)

        # The execution is already committed, while the inbox row is still
        # PROCESSING. Expire only the lease; keep protocol time fixed so the
        # deterministic Ed25519 acknowledgement is reproducible on recovery.
        with fixture.receiver_connect() as connection:
            connection.execute(
                """UPDATE federation_inbox
                   SET lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second'
                   WHERE receiver_tenant_id = %s
                     AND issuer_installation_id = %s
                     AND message_id = %s""",
                ("tenant-rw", "INSTALL-UG-01", "MSG-PG-FED-UG-RW-001"),
            )

        restarted_inbox = PostgreSQLFederationInbox(fixture.receiver_connect)
        restarted_receiver = InstitutionalFederatedInstructionReceiver(
            receiver_tenant_id="tenant-rw",
            receiver_installation_id="INSTALL-RW-01",
            receiver_institution_id="BANK-RW-01",
            receiver_binding_id="BIND-RW-01",
            # The reference CEE/IVM journal is retained to model a durable
            # journal adapter across process replacement. Separate-process
            # journal restoration is a later integration gate.
            execution_service=fixture.receiver_runtime.service,
            trust_key_loader=fixture.receiver.trust_key_loader,
            public_key_loader=fixture.receiver.public_key_loader,
            clock=lambda: fixture.now,
            inbox=restarted_inbox,
            inbox_worker_id="rw-inbox-worker-restarted",
            inbox_lease_seconds=30,
            receiver_signing_key_id="KEY-RW-01",
            receiver_signing_key_loader=fixture.receiver.receiver_signing_key_loader,
        )

        recovered = restarted_receiver.receive(fixture.envelope)
        self.assertTrue(recovered["message_verified"])
        self.assertFalse(recovered["duplicate_delivery"])
        self.assertEqual(recovered["delivery_status"], "COMPLETED")
        self.assertEqual(
            recovered["receiver_acknowledgement"],
            interrupted_response["receiver_acknowledgement"],
        )

        state = restarted_inbox.get(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id="MSG-PG-FED-UG-RW-001",
        )
        self.assertEqual(state["status"], "COMPLETED")
        self.assertEqual(state["attempt_count"], 2)

        operation = fixture.receiver_runtime.pipeline.cee.get(
            "OP-PG-FED-UG-RW-001"
        )
        self.assertIsNotNone(operation)
        self.assertEqual(operation["phase"], "FINALIZED")
        self.assertEqual(operation["outcome"], "COMMITTED")
        requested = [
            event for event in fixture.receiver_runtime.pipeline.cee.events(
                "OP-PG-FED-UG-RW-001"
            )
            if event.get("phase") == "REQUESTED"
        ]
        self.assertEqual(len(requested), 1)


if __name__ == "__main__":
    unittest.main()
