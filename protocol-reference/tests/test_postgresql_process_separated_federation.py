# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Process-separated PostgreSQL federation over a real mutual-TLS socket.

The sender and receiver use separate PostgreSQL databases. The receiver's
MINAH -> CEE -> IVM runtime, durable inbox and signed acknowledgement key are
constructed inside spawned OS processes. Coverage includes completed-delivery
replay after a clean restart and abrupt receiver-process loss after the durable
IVM commit but before inbox completion. The latter must recover the original
pinned CEE/IVM operation after lease expiry, without executing it twice.
"""
from __future__ import annotations

import multiprocessing
import os
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption, PublicFormat

from governed_contracts import CONTRACT_VERSION, compute_payload_digest
from institutional_federation import sign_cross_installation_instruction
from institutional_federation_acknowledgement import verify_receiver_acknowledgement
from postgresql_federation_outbox import PostgreSQLFederationOutbox
from postgresql_migrations import apply_migrations
from test_cross_installation_runtime_integration import IsolatedInstallation
from test_federation_mtls_network_integration import (
    FederationLiveMTLSNetworkTests,
    _LoopbackPinnedHTTPSConnection,
    _PeerCertificateWSGIHandler,
)
from federation_https_transport import FederationHTTPSJSONTransport, FederationHTTPTransportError


def _receiver_process(arguments):
    """Construct and serve the complete receiver inside a spawned process."""
    options, tls_paths, sender_public, receiver_private_raw, now, ready, stop, crash_after_ivm_commit = arguments
    server = None
    runtime = None
    try:
        import ssl
        import psycopg
        from wsgiref.simple_server import WSGIServer, make_server
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from federation_http_transport import FederationHttpIngressApplication
        from federation_mtls_identity import mtls_peer_identity_resolver
        from institutional_federated_receiver import InstitutionalFederatedInstructionReceiver
        from postgresql_cee_journal import PostgreSQLCEEJournal
        from postgresql_federation_inbox import PostgreSQLFederationInbox
        from postgresql_protocol_store import PostgreSQLProtocolStore
        from protocol_pipeline import KinSysProtocolPipeline
        from institutional_instruction_execution import InstitutionalInstructionExecutionService

        def connect():
            return psycopg.connect(**options)

        runtime = IsolatedInstallation(
            country="RW", tenant="tenant-rw", institution="BANK-RW-01",
            installation="INSTALL-RW-01", binding="BIND-RW-01",
            jurisdiction="RW", policy_version="KIN-SYS-DEMO-RULES:1.0",
            source_version="RW-SOURCES:2.0", minah_id="MINAH-RW-01",
            idp_id="IDP-RW-01", jurisdiction_package="RW-PACK-01",
            authority_version="RW-AUTHORITY:2.0",
        )
        runtime.pipeline.close()
        runtime.pipeline = KinSysProtocolPipeline(
            minah_profile=runtime.profile,
            cee_journal=PostgreSQLCEEJournal(connect),
            ivm_store=PostgreSQLProtocolStore(connect),
            identity_provider_id=runtime.idp_id,
            identity_provider_version="1",
            jurisdiction_package_id=runtime.jurisdiction_package,
            jurisdiction_package_version="1",
            authority_profile_version=runtime.authority_version,
        )
        if crash_after_ivm_commit:
            original_execute = runtime.pipeline.execute
            def commit_then_terminate(request, *, execution_guard=None):
                try:
                    return original_execute(
                        request, fail_after_ivm_commit=True,
                        execution_guard=execution_guard,
                    )
                except BaseException:
                    # Model abrupt process loss after durable IVM commit,
                    # before receiver inbox completion can be written.
                    os._exit(73)
            runtime.pipeline.execute = commit_then_terminate
        runtime.service = InstitutionalInstructionExecutionService(
            runtime_binding_resolver=runtime,
            pipeline_factory=lambda _context: runtime.pipeline,
            operation_pipeline_resolver=lambda **_kwargs: runtime.pipeline,
        )

        sender_key = Ed25519PublicKey.from_public_bytes(sender_public)
        receiver_key = Ed25519PrivateKey.from_private_bytes(receiver_private_raw)
        sender_trust = {
            "tenant_id": "tenant-rw",
            "key_id": "KEY-UG-01",
            "issuer_institution_id": "BANK-UG-01",
            "algorithm": "Ed25519",
            "status": "ACTIVE",
            "valid_from": 1_700_000_000,
            "valid_until": 1_900_000_000,
            "issuer_valid_from": 1_700_000_000,
            "issuer_valid_until": 1_900_000_000,
            "public_key_ref": "keyref:ug-sender",
        }
        receiver = InstitutionalFederatedInstructionReceiver(
            receiver_tenant_id="tenant-rw",
            receiver_installation_id="INSTALL-RW-01",
            receiver_institution_id="BANK-RW-01",
            receiver_binding_id="BIND-RW-01",
            execution_service=runtime.service,
            trust_key_loader=lambda tenant, key_id: (
                sender_trust if (tenant, key_id) == ("tenant-rw", "KEY-UG-01") else None
            ),
            public_key_loader=lambda ref: sender_key.public_bytes(
                Encoding.Raw, PublicFormat.Raw
            ) if ref == "keyref:ug-sender" else b"",
            clock=lambda: now,
            inbox=PostgreSQLFederationInbox(connect),
            inbox_worker_id=f"rw-process-inbox-worker-{os.getpid()}",
            inbox_lease_seconds=1 if crash_after_ivm_commit else 30,
            receiver_signing_key_id="KEY-RW-01",
            receiver_signing_key_loader=lambda tenant, institution, key_id: (
                receiver_key if (tenant, institution, key_id) ==
                ("tenant-rw", "BANK-RW-01", "KEY-RW-01") else None
            ),
        )
        app = FederationHttpIngressApplication(
            receiver=receiver,
            peer_identity_resolver=mtls_peer_identity_resolver,
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(tls_paths["server_cert"], tls_paths["server_key"])
        context.load_verify_locations(cafile=tls_paths["ca"])
        context.verify_mode = ssl.CERT_REQUIRED
        server = make_server(
            "127.0.0.1", 0, app, server_class=WSGIServer,
            handler_class=_PeerCertificateWSGIHandler,
        )
        server.socket = context.wrap_socket(server.socket, server_side=True)
        server.timeout = 0.2
        ready.put({"port": server.server_port, "pid": os.getpid()})
        while not stop.is_set():
            server.handle_request()
    except BaseException as exc:
        try:
            ready.put({"error": type(exc).__name__, "detail": str(exc)})
        except Exception:
            pass
        raise
    finally:
        if server is not None:
            server.server_close()
        if runtime is not None:
            runtime.close()
        for channel in (ready,):
            channel.close()
            channel.join_thread()


def _instruction():
    value = {
        "contract_version": CONTRACT_VERSION,
        "request_id": "REQ-PG-PROC-UG-RW-001",
        "operation_id": "OP-PG-PROC-UG-RW-001",
        "idempotency_key": "IDEM-PG-PROC-UG-RW-001",
        "principal_ref": "AUTHORITY-001",
        "authority_ref": "origin:authority-claim-is-not-inherited",
        "action": "ACTIVATE",
        "target_ref": "TX-PG-PROC-UG-RW-001",
        "purpose": "Separate-process receiving-institution review",
        "jurisdiction": "RW",
        "submitted_at": "2026-11-16T08:00:00+00:00",
        "rule_pack_version": "KIN-SYS-DEMO-RULES:1.0",
        "evidence_refs": ["evidence:PG-PROC-UG-RW-001"],
        "evidence": {
            "case_ref": "TX-PG-PROC-UG-RW-001",
            "consignment_id": "TX-PG-PROC-UG-RW-001",
            "commodity": "SYNTHETIC_MAIZE",
            "origin": "UG",
            "destination": "RW",
            "rule_refs": ["SYNTHETIC-CROSS-BORDER-RULE"],
        },
    }
    value["payload_digest"] = compute_payload_digest(value)
    return value


def _stop_process(process, stop):
    stop.set()
    process.join(timeout=8)
    if process.is_alive():
        process.terminate()
        process.join(timeout=3)


class PostgreSQLProcessSeparatedFederationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ.get("KIN_SYS_POSTGRES_TEST_DSN")
        if not cls.dsn:
            raise unittest.SkipTest("KIN_SYS_POSTGRES_TEST_DSN is not configured")
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict
        cls.psycopg = psycopg
        cls.sql = sql
        cls.conninfo_to_dict = staticmethod(conninfo_to_dict)

    def setUp(self):
        # Reuse the established CA-issued mTLS fixture, but send to the spawned
        # full receiver rather than the fixture's in-process stub.
        self.tls_fixture = FederationLiveMTLSNetworkTests()
        self.tls_fixture.setUp()
        self.addCleanup(self.tls_fixture.doCleanups)
        self.paths = self.tls_fixture.paths
        self.client_context = self.tls_fixture.client_context

        self.processes = []
        self.admin = self.psycopg.connect(self.dsn, autocommit=True)
        options = self.conninfo_to_dict(self.dsn)
        self.sender_database = "kin_sys_sender_" + uuid.uuid4().hex[:20]
        self.receiver_database = "kin_sys_receiver_" + uuid.uuid4().hex[:20]
        for database in (self.sender_database, self.receiver_database):
            self.admin.execute(
                self.sql.SQL("CREATE DATABASE {}").format(self.sql.Identifier(database))
            )

        def factory(database):
            values = dict(options)
            values["dbname"] = database
            return lambda: self.psycopg.connect(**values)

        self.sender_connect = factory(self.sender_database)
        self.receiver_connect = factory(self.receiver_database)
        with self.sender_connect() as connection:
            apply_migrations(connection)
        with self.sender_connect() as sender_connection:
            sender_database = sender_connection.execute("SELECT current_database()").fetchone()[0]
        with self.receiver_connect() as connection:
            receiver_database = connection.execute("SELECT current_database()").fetchone()[0]
            self.assertNotEqual(sender_database, receiver_database)
            apply_migrations(connection)
        self.receiver_options = dict(options)
        self.receiver_options["dbname"] = self.receiver_database

        self.now = 1_800_000_030
        self.sender_private = Ed25519PrivateKey.generate()
        self.receiver_private = Ed25519PrivateKey.generate()
        self.sender_public = self.sender_private.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        self.receiver_public = self.receiver_private.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        self.sender_trust_for_receiver = {
            "tenant_id": "tenant-ug",
            "installation_id": "INSTALL-RW-01",
            "institution_id": "BANK-RW-01",
            "key_id": "KEY-RW-01",
            "algorithm": "Ed25519",
            "status": "ACTIVE",
            "valid_from": 1_700_000_000,
            "valid_until": 1_900_000_000,
            "public_key_ref": "keyref:rw-receiver",
        }

        def verify_receipt(receipt, original):
            return verify_receiver_acknowledgement(
                receipt, original_envelope=original,
                sender_tenant_id="tenant-ug",
                sender_installation_id="INSTALL-UG-01",
                sender_institution_id="BANK-UG-01",
                receiver_trust_key_loader=lambda tenant, installation, key_id: (
                    self.sender_trust_for_receiver
                    if (tenant, installation, key_id) ==
                    ("tenant-ug", "INSTALL-RW-01", "KEY-RW-01") else None
                ),
                receiver_public_key_loader=lambda ref: (
                    self.receiver_public if ref == "keyref:rw-receiver" else b""
                ),
                clock=lambda: self.now,
            )

        self.outbox = PostgreSQLFederationOutbox(self.sender_connect, verify_receipt)
        self.envelope = sign_cross_installation_instruction(
            instruction=_instruction(),
            message_id="MSG-PG-PROC-UG-RW-001",
            key_id="KEY-UG-01",
            issuer_installation_id="INSTALL-UG-01",
            issuer_institution_id="BANK-UG-01",
            receiver_installation_id="INSTALL-RW-01",
            receiver_institution_id="BANK-RW-01",
            nonce="NONCE-PG-PROC-UG-RW-001",
            issued_at=self.now - 30,
            expires_at=self.now + 90,
            private_key=self.sender_private,
        )
        self.outbox.enqueue(
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            envelope=self.envelope,
        )

    def tearDown(self):
        for process, stop in self.processes:
            _stop_process(process, stop)
        for database in (self.sender_database, self.receiver_database):
            self.admin.execute(
                self.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    self.sql.Identifier(database)
                )
            )
        self.admin.close()

    def _start_receiver(self, *, crash_after_ivm_commit=False):
        context = multiprocessing.get_context("spawn")
        ready = context.Queue()
        stop = context.Event()
        process = context.Process(
            target=_receiver_process,
            args=((
                self.receiver_options, self.paths, self.sender_public,
                self.receiver_private.private_bytes(
                    Encoding.Raw, PrivateFormat.Raw, NoEncryption()
                ),
                self.now, ready, stop, crash_after_ivm_commit,
            ),),
            daemon=True,
        )
        process.start()
        self.processes.append((process, stop))
        self.addCleanup(_stop_process, process, stop)
        startup = ready.get(timeout=20)
        if "error" in startup:
            process.join(timeout=3)
            self.fail("receiver startup failed: " + startup["error"] + ": " + startup["detail"])
        self.assertTrue(process.is_alive(), "receiver process exited after startup")
        self.assertNotEqual(startup["pid"], os.getpid())
        return process, stop, startup["port"]

    def _transport(self, port):
        def factory(host, destination_port, *, pinned_ip, timeout, context):
            return _LoopbackPinnedHTTPSConnection(
                host, destination_port, pinned_ip=pinned_ip, timeout=timeout,
                context=context, local_port=port,
            )
        return FederationHTTPSJSONTransport(
            destinations={"INSTALL-RW-01": "https://receiver.test"},
            resolver=lambda host, destination_port, type: [
                (2, 1, 6, "", ("8.8.8.8", destination_port))
            ],
            connection_factory=factory,
            ssl_context=self.client_context,
        )

    def test_full_receiver_runtime_executes_in_separate_process_and_recovers_after_restart(self):
        claimed = self.outbox.claim(
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            message_id="MSG-PG-PROC-UG-RW-001",
            worker_id="ug-process-outbox-worker",
        )
        self.assertIsNotNone(claimed)

        first_process, first_stop, first_port = self._start_receiver()
        first_transport = self._transport(first_port)
        first_response = first_transport.post("INSTALL-RW-01", claimed["envelope"])
        # The TLS client certificate identifies INSTALL-UG-01. A different
        # envelope issuer must be rejected at ingress before receiver code.
        wrong_peer_envelope = {
            "claims": dict(claimed["envelope"]["claims"]),
            "signature": claimed["envelope"]["signature"],
        }
        wrong_peer_envelope["claims"]["issuer_installation_id"] = "INSTALL-OTHER-99"
        with self.assertRaisesRegex(FederationHTTPTransportError, "403"):
            first_transport.post("INSTALL-RW-01", wrong_peer_envelope)
        self.assertEqual(first_response["http_status"], 200)
        self.assertTrue(first_response["message_verified"])
        self.assertFalse(first_response["origin_authority_inherited"])
        self.assertFalse(first_response["receiver_execution_authorised_by_origin"])
        self.assertEqual(first_response["receiver_runtime_pins"]["tenant_id"], "tenant-rw")
        self.assertEqual(first_response["receiver_runtime_pins"]["institution_id"], "BANK-RW-01")
        self.assertEqual(first_response["receiver_runtime_pins"]["minah_deployment_id"], "MINAH-RW-01")
        self.assertIn("receiver_acknowledgement", first_response)

        acknowledged = self.outbox.acknowledge(
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            message_id="MSG-PG-PROC-UG-RW-001",
            receipt=first_response["receiver_acknowledgement"],
        )
        self.assertTrue(acknowledged["acknowledged"])
        self.assertFalse(acknowledged["duplicate"])

        first_stop.set()
        first_process.join(timeout=8)
        self.assertFalse(first_process.is_alive(), "first receiver process did not stop cleanly")
        self.assertEqual(first_process.exitcode, 0)

        # A fresh OS process reconstructs its receiver runtime from the
        # receiver database. Replaying the exact signed message must return
        # the stored signed acknowledgement, not execute the operation again.
        second_process, second_stop, second_port = self._start_receiver()
        replay = self._transport(second_port).post("INSTALL-RW-01", claimed["envelope"])
        self.assertEqual(replay["http_status"], 200)
        self.assertTrue(replay["duplicate_delivery"])
        self.assertEqual(
            replay["receiver_acknowledgement"],
            first_response["receiver_acknowledgement"],
        )

        with self.receiver_connect() as connection:
            inbox = connection.execute(
                """SELECT status,attempt_count FROM federation_inbox
                   WHERE receiver_tenant_id='tenant-rw'
                     AND issuer_installation_id='INSTALL-UG-01'
                     AND message_id='MSG-PG-PROC-UG-RW-001'"""
            ).fetchone()
            ivm_count = connection.execute("SELECT COUNT(*) FROM ivm_processes").fetchone()[0]
            process_row = connection.execute(
                "SELECT state,state_version FROM ivm_processes"
            ).fetchone()
        self.assertEqual(inbox[0], "COMPLETED")
        self.assertEqual(inbox[1], 1)
        self.assertEqual(ivm_count, 1)
        self.assertEqual(process_row, ("ACTIVE", 1))

        second_stop.set()
        second_process.join(timeout=8)
        self.assertFalse(second_process.is_alive(), "restarted receiver process did not stop cleanly")
        self.assertEqual(second_process.exitcode, 0)



    def test_postgresql_process_crash_after_ivm_commit_recovers_without_reexecution(self):
        claimed = self.outbox.claim(
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            message_id="MSG-PG-PROC-UG-RW-001",
            worker_id="ug-crash-recovery-outbox-worker",
        )
        self.assertIsNotNone(claimed)

        crashed_process, _stop, port = self._start_receiver(
            crash_after_ivm_commit=True
        )
        with self.assertRaises(FederationHTTPTransportError):
            self._transport(port).post("INSTALL-RW-01", claimed["envelope"])
        crashed_process.join(timeout=8)
        self.assertFalse(crashed_process.is_alive())
        self.assertEqual(crashed_process.exitcode, 73)

        with self.receiver_connect() as connection:
            inbox_before = connection.execute(
                """SELECT status,attempt_count FROM federation_inbox
                   WHERE receiver_tenant_id='tenant-rw'
                     AND issuer_installation_id='INSTALL-UG-01'
                     AND message_id='MSG-PG-PROC-UG-RW-001'"""
            ).fetchone()
            ivm_before = connection.execute(
                "SELECT state,state_version FROM ivm_processes"
            ).fetchone()
            cee_before = connection.execute(
                """SELECT phase FROM cee_operations
                   WHERE operation_id='OP-PG-PROC-UG-RW-001'"""
            ).fetchone()
        self.assertEqual(inbox_before, ("PROCESSING", 1))
        self.assertEqual(ivm_before, ("ACTIVE", 1))
        self.assertEqual(cee_before, ("COMMITTING",))

        # The crashed process leaves its lease intact. After expiry, a clean
        # process must recover the original pinned operation from PostgreSQL.
        import time
        time.sleep(1.2)
        recovered_process, _recovery_stop, recovery_port = self._start_receiver()
        recovered = self._transport(recovery_port).post(
            "INSTALL-RW-01", claimed["envelope"]
        )
        self.assertEqual(recovered["http_status"], 200)
        self.assertTrue(recovered["message_verified"])
        self.assertEqual(recovered["receiver_decision"]["protocol_decision"], "PASSED")
        self.assertIn("receiver_acknowledgement", recovered)

        acknowledged = self.outbox.acknowledge(
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            message_id="MSG-PG-PROC-UG-RW-001",
            receipt=recovered["receiver_acknowledgement"],
        )
        self.assertTrue(acknowledged["acknowledged"])

        with self.receiver_connect() as connection:
            inbox_after = connection.execute(
                """SELECT status,attempt_count FROM federation_inbox
                   WHERE receiver_tenant_id='tenant-rw'
                     AND issuer_installation_id='INSTALL-UG-01'
                     AND message_id='MSG-PG-PROC-UG-RW-001'"""
            ).fetchone()
            ivm_after = connection.execute(
                "SELECT state,state_version FROM ivm_processes"
            ).fetchone()
            ivm_count = connection.execute(
                "SELECT COUNT(*) FROM ivm_processes"
            ).fetchone()[0]
            cee_after = connection.execute(
                """SELECT phase FROM cee_operations
                   WHERE operation_id='OP-PG-PROC-UG-RW-001'"""
            ).fetchone()
        self.assertEqual(inbox_after, ("COMPLETED", 2))
        self.assertEqual(ivm_after, ("ACTIVE", 1))
        self.assertEqual(ivm_count, 1)
        self.assertEqual(cee_after, ("FINALIZED",))

        # Stop the receiver's serve_forever loop before joining it. The
        # process is intentionally long-lived after it has completed the
        # request; joining without signalling stop would hang until timeout.
        _recovery_stop.set()
        recovered_process.join(timeout=8)
        self.assertFalse(
            recovered_process.is_alive(),
            "recovered receiver process did not stop cleanly",
        )
        self.assertEqual(recovered_process.exitcode, 0)

    def test_lost_acknowledgement_response_replays_durably_without_reexecution(self):
        claimed = self.outbox.claim(
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            message_id="MSG-PG-PROC-UG-RW-001",
            worker_id="ug-lost-ack-outbox-worker",
            lease_seconds=1,
        )
        self.assertIsNotNone(claimed)

        first_process, first_stop, first_port = self._start_receiver()

        class _DropResponseAfterRemoteCommit:
            def __init__(self, transport):
                self.transport = transport
                self.remote_response = None

            def post(self, installation_id, envelope):
                self.remote_response = self.transport.post(installation_id, envelope)
                # The receiver has already committed its inbox result and
                # signed acknowledgement; the sender-side connection now fails.
                raise FederationHTTPTransportError("SIMULATED_RESPONSE_LOSS")

        dropped = _DropResponseAfterRemoteCommit(self._transport(first_port))
        with self.assertRaisesRegex(FederationHTTPTransportError, "SIMULATED_RESPONSE_LOSS"):
            dropped.post("INSTALL-RW-01", claimed["envelope"])
        first_response = dropped.remote_response
        self.assertEqual(first_response["http_status"], 200)
        self.assertIn("receiver_acknowledgement", first_response)

        # Model the sender losing the HTTP response after the receiver has
        # committed COMPLETED and its signed acknowledgement. The sender does
        # not call acknowledge(), so its outbox lease must expire and retry.
        lost_receipt = first_response["receiver_acknowledgement"]
        first_stop.set()
        first_process.join(timeout=8)
        self.assertFalse(first_process.is_alive())
        self.assertEqual(first_process.exitcode, 0)

        with self.sender_connect() as connection:
            sender_before_retry = connection.execute(
                """SELECT status,attempt_count FROM federation_outbox
                   WHERE sender_tenant_id='tenant-ug'
                     AND sender_installation_id='INSTALL-UG-01'
                     AND message_id='MSG-PG-PROC-UG-RW-001'
                """
            ).fetchone()
        self.assertEqual(sender_before_retry, ("DELIVERING", 1))

        import time
        time.sleep(1.2)
        retried = self.outbox.claim(
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            message_id="MSG-PG-PROC-UG-RW-001",
            worker_id="ug-lost-ack-retry-worker",
            lease_seconds=10,
        )
        self.assertIsNotNone(retried)
        self.assertEqual(retried["attempt_count"], 2)

        # A new receiver process returns the exact persisted receipt. It must
        # not run MINAH/CEE/IVM again merely because the sender missed the reply.
        second_process, second_stop, second_port = self._start_receiver()
        replay = self._transport(second_port).post(
            "INSTALL-RW-01", retried["envelope"]
        )
        self.assertEqual(replay["http_status"], 200)
        self.assertTrue(replay["duplicate_delivery"])
        self.assertEqual(replay["receiver_acknowledgement"], lost_receipt)

        acknowledged = self.outbox.acknowledge(
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            message_id="MSG-PG-PROC-UG-RW-001",
            receipt=replay["receiver_acknowledgement"],
        )
        self.assertTrue(acknowledged["acknowledged"])
        self.assertFalse(acknowledged["duplicate"])

        # Re-submitting the same verified receipt is idempotent; a conflicting
        # receipt remains rejected by the outbox's digest binding.
        duplicate_ack = self.outbox.acknowledge(
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            message_id="MSG-PG-PROC-UG-RW-001",
            receipt=replay["receiver_acknowledgement"],
        )
        self.assertTrue(duplicate_ack["acknowledged"])
        self.assertTrue(duplicate_ack["duplicate"])

        with self.receiver_connect() as connection:
            inbox = connection.execute(
                """SELECT status,attempt_count FROM federation_inbox
                   WHERE receiver_tenant_id='tenant-rw'
                     AND issuer_installation_id='INSTALL-UG-01'
                     AND message_id='MSG-PG-PROC-UG-RW-001'
                """
            ).fetchone()
            ivm_count = connection.execute(
                "SELECT COUNT(*) FROM ivm_processes"
            ).fetchone()[0]
            process_row = connection.execute(
                "SELECT state,state_version FROM ivm_processes"
            ).fetchone()
        self.assertEqual(inbox, ("COMPLETED", 1))
        self.assertEqual(ivm_count, 1)
        self.assertEqual(process_row, ("ACTIVE", 1))

        with self.sender_connect() as connection:
            sender_after_ack = connection.execute(
                """SELECT status,attempt_count,acknowledgement_sha256
                   FROM federation_outbox
                   WHERE sender_tenant_id='tenant-ug'
                     AND sender_installation_id='INSTALL-UG-01'
                     AND message_id='MSG-PG-PROC-UG-RW-001'
                """
            ).fetchone()
        self.assertEqual(sender_after_ack[0:2], ("ACKNOWLEDGED", 2))
        self.assertTrue(sender_after_ack[2])

        second_stop.set()
        second_process.join(timeout=8)
        self.assertFalse(second_process.is_alive())
        self.assertEqual(second_process.exitcode, 0)

    def test_postgresql_inbox_concurrent_workers_have_single_lease_owner(self):
        """Concurrent claims serialize; stale workers cannot complete a lease."""
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from postgresql_federation_inbox import PostgreSQLFederationInbox

        inbox = PostgreSQLFederationInbox(self.receiver_connect)
        message_id = "MSG-PG-LEASE-CONTENTION-001"
        envelope = {
            "claims": {
                "message_id": message_id,
                "operation_id": "OP-PG-LEASE-CONTENTION-001",
                "idempotency_key": "IDEM-PG-LEASE-CONTENTION-001",
            },
            "signature": "synthetic-test-envelope",
        }
        accepted = inbox.accept_verified(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id=message_id,
            operation_id="OP-PG-LEASE-CONTENTION-001",
            idempotency_key="IDEM-PG-LEASE-CONTENTION-001",
            envelope=envelope,
        )
        self.assertTrue(accepted["inserted"])

        barrier = Barrier(2)

        def claim(worker_id):
            barrier.wait(timeout=5)
            return inbox.claim(
                receiver_tenant_id="tenant-rw",
                issuer_installation_id="INSTALL-UG-01",
                message_id=message_id,
                worker_id=worker_id,
                lease_seconds=30,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(claim, "rw-concurrent-worker-A")
            second = pool.submit(claim, "rw-concurrent-worker-B")
            claims = [first.result(timeout=10), second.result(timeout=10)]

        winners = [item for item in claims if item is not None]
        losers = [item for item in claims if item is None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(losers), 1)
        winner = winners[0]
        self.assertEqual(winner["attempt_count"], 1)

        # The losing worker cannot steal an unexpired lease or complete it.
        self.assertIsNone(inbox.claim(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id=message_id,
            worker_id="rw-late-worker",
            lease_seconds=30,
        ))
        loser_id = (
            "rw-concurrent-worker-B"
            if winner["lease_owner"] == "rw-concurrent-worker-A"
            else "rw-concurrent-worker-A"
        )
        self.assertFalse(inbox.complete(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id=message_id,
            worker_id=loser_id,
            decision_ref="CEE-DECISION-LEASE-CONTENTION",
            response={"decision": "PASSED"},
        ))

        # Only the actual lease owner can complete; completion clears the lease.
        self.assertTrue(inbox.complete(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id=message_id,
            worker_id=winner["lease_owner"],
            decision_ref="CEE-DECISION-LEASE-CONTENTION",
            response={"decision": "PASSED"},
        ))
        record = inbox.get(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id=message_id,
        )
        self.assertEqual(record["status"], "COMPLETED")
        self.assertEqual(record["attempt_count"], 1)
        self.assertEqual(record["response"], {"decision": "PASSED"})
        self.assertIsNone(inbox.claim(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id=message_id,
            worker_id="rw-post-completion-worker",
            lease_seconds=30,
        ))



    def test_postgresql_inbox_expired_lease_transfers_ownership_and_fences_stale_worker(self):
        """A reclaimed lease fences the old worker from completing or failing."""
        import time
        from postgresql_federation_inbox import PostgreSQLFederationInbox

        inbox = PostgreSQLFederationInbox(self.receiver_connect)
        message_id = "MSG-PG-LEASE-TRANSFER-001"
        envelope = {
            "claims": {
                "message_id": message_id,
                "operation_id": "OP-PG-LEASE-TRANSFER-001",
                "idempotency_key": "IDEM-PG-LEASE-TRANSFER-001",
            },
            "signature": "synthetic-test-envelope",
        }
        accepted = inbox.accept_verified(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id=message_id,
            operation_id="OP-PG-LEASE-TRANSFER-001",
            idempotency_key="IDEM-PG-LEASE-TRANSFER-001",
            envelope=envelope,
        )
        self.assertTrue(accepted["inserted"])

        first = inbox.claim(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id=message_id,
            worker_id="rw-expiring-worker-A",
            lease_seconds=1,
        )
        self.assertIsNotNone(first)
        self.assertEqual(first["attempt_count"], 1)

        # Wait beyond the database lease expiry, then let another worker reclaim.
        time.sleep(1.2)
        second = inbox.claim(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id=message_id,
            worker_id="rw-recovery-worker-B",
            lease_seconds=30,
        )
        self.assertIsNotNone(second)
        self.assertEqual(second["attempt_count"], 2)
        self.assertEqual(second["lease_owner"], "rw-recovery-worker-B")

        # The first worker is fenced even though it still knows the message ID.
        self.assertFalse(inbox.complete(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id=message_id,
            worker_id="rw-expiring-worker-A",
            decision_ref="CEE-DECISION-STALE-LEASE",
            response={"decision": "STALE"},
        ))
        self.assertFalse(inbox.fail(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id=message_id,
            worker_id="rw-expiring-worker-A",
            error_code="STALE_WORKER_ATTEMPT",
            retryable=True,
        ))

        self.assertTrue(inbox.complete(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id=message_id,
            worker_id="rw-recovery-worker-B",
            decision_ref="CEE-DECISION-RECOVERED-LEASE",
            response={"decision": "PASSED", "recovered": True},
        ))
        record = inbox.get(
            receiver_tenant_id="tenant-rw",
            issuer_installation_id="INSTALL-UG-01",
            message_id=message_id,
        )
        self.assertEqual(record["status"], "COMPLETED")
        self.assertEqual(record["attempt_count"], 2)
        self.assertEqual(record["receiver_decision_ref"], "CEE-DECISION-RECOVERED-LEASE")
        self.assertEqual(record["response"], {"decision": "PASSED", "recovered": True})


    def test_concurrent_duplicate_https_deliveries_execute_once_across_receiver_processes(self):
        """Two independent receiver processes race on one signed HTTPS delivery."""
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        claimed = self.outbox.claim(
            sender_tenant_id="tenant-ug",
            sender_installation_id="INSTALL-UG-01",
            message_id="MSG-PG-PROC-UG-RW-001",
            worker_id="ug-concurrent-http-outbox-worker",
        )
        self.assertIsNotNone(claimed)

        # Each process owns a separate HTTPS listener and independently
        # reconstructs the receiver runtime, but both share the receiver DB.
        first_process, first_stop, first_port = self._start_receiver()
        second_process, second_stop, second_port = self._start_receiver()
        transports = (
            self._transport(first_port),
            self._transport(second_port),
        )
        barrier = Barrier(2)

        def deliver(transport):
            barrier.wait(timeout=10)
            return transport.post("INSTALL-RW-01", claimed["envelope"])

        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(deliver, transports[0])
            second_future = pool.submit(deliver, transports[1])
            responses = [
                first_future.result(timeout=45),
                second_future.result(timeout=45),
            ]

        # The process that wins the inbox lease completes with HTTP 200;
        # a concurrent loser may correctly observe the active lease as 202,
        # or arrive after completion and receive the durable replay as 200.
        statuses = [item["http_status"] for item in responses]
        self.assertTrue(
            all(status in (200, 202) for status in statuses),
            f"unexpected federation HTTP statuses: {statuses}",
        )
        self.assertIn(200, statuses, "at least one delivery must complete")
        self.assertTrue(all(item["message_verified"] for item in responses))
        self.assertEqual(
            sum(item.get("duplicate_delivery") is False for item in responses),
            1,
            "exactly one receiver process must own the first execution",
        )
        self.assertEqual(
            sum(item.get("duplicate_delivery") is True for item in responses),
            1,
            "exactly one concurrent request must be classified as a duplicate",
        )

        # A final replay after both racing requests settle must return the
        # durable signed acknowledgement without executing a second time.
        replay = self._transport(first_port).post(
            "INSTALL-RW-01", claimed["envelope"]
        )
        self.assertEqual(replay["http_status"], 200)
        self.assertTrue(replay["duplicate_delivery"])
        self.assertIn("receiver_acknowledgement", replay)

        with self.receiver_connect() as connection:
            inbox = connection.execute(
                """SELECT status,attempt_count FROM federation_inbox
                   WHERE receiver_tenant_id='tenant-rw'
                     AND issuer_installation_id='INSTALL-UG-01'
                     AND message_id='MSG-PG-PROC-UG-RW-001'"""
            ).fetchone()
            ivm_count = connection.execute(
                "SELECT COUNT(*) FROM ivm_processes"
            ).fetchone()[0]
            process_row = connection.execute(
                "SELECT state,state_version FROM ivm_processes"
            ).fetchone()
            cee_phase = connection.execute(
                """SELECT phase FROM cee_operations
                   WHERE operation_id='OP-PG-PROC-UG-RW-001'"""
            ).fetchone()
        self.assertEqual(inbox, ("COMPLETED", 1))
        self.assertEqual(ivm_count, 1)
        self.assertEqual(process_row, ("ACTIVE", 1))
        self.assertEqual(cee_phase, ("FINALIZED",))

        for process, stop in (
            (first_process, first_stop),
            (second_process, second_stop),
        ):
            stop.set()
            process.join(timeout=8)
            self.assertFalse(process.is_alive(), "concurrent receiver process did not stop")
            self.assertEqual(process.exitcode, 0)
