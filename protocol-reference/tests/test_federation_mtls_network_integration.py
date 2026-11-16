# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Live TLS/mTLS socket integration for the federation sender and receiver.

The receiver runs behind a real TLS socket and requires a CA-issued client
certificate. The sender uses the production HTTPS transport and verifies the
receiver certificate/hostname. Only DNS resolution and the final socket
address are injected so this deterministic test can use loopback without
weakening the production public-address policy.
"""
from __future__ import annotations

import multiprocessing
import os
import ssl
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from federation_http_transport import FederationHttpIngressApplication
from federation_https_transport import (
    FederationHTTPSJSONTransport,
    FederationHTTPTransportError,
)
from federation_mtls_identity import mtls_peer_identity_resolver


def _pem_key(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _issue_certificate(*, subject, issuer, public_key, issuer_key, serial,
                       now, extensions):
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(serial)
        .not_valid_before(now - timedelta(minutes=2))
        .not_valid_after(now + timedelta(days=2))
    )
    for extension, critical in extensions:
        builder = builder.add_extension(extension, critical)
    return builder.sign(issuer_key, algorithm=None)


class _PeerCertificateWSGIHandler(WSGIRequestHandler):
    def get_environ(self):
        environ = super().get_environ()
        peer_der = self.request.getpeercert(binary_form=True)
        if peer_der:
            environ["SSL_CLIENT_CERT"] = ssl.DER_cert_to_PEM_cert(peer_der)
        return environ

    def log_message(self, format, *args):
        # Keep test output deterministic and avoid logging request details.
        return



def _serve_mtls_receiver_process(arguments):
    """Run the TLS receiver in an OS process separate from the sender test."""
    paths, ready, received, stop = arguments

    class Receiver:
        def receive(self, envelope):
            received.put(envelope)
            return {
                "delivery_status": "COMPLETED",
                "message_id": envelope["claims"]["message_id"],
                "receiver_acknowledgement": {
                    "claims": {"message_id": envelope["claims"]["message_id"]},
                    "response": {"decision": "RECEIVED"},
                    "signature": "process-test-receipt",
                },
            }

    app = FederationHttpIngressApplication(
        receiver=Receiver(),
        peer_identity_resolver=mtls_peer_identity_resolver,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(paths["server_cert"], paths["server_key"])
    context.load_verify_locations(cafile=paths["ca"])
    context.verify_mode = ssl.CERT_REQUIRED
    server = make_server(
        "127.0.0.1", 0, app, server_class=WSGIServer,
        handler_class=_PeerCertificateWSGIHandler,
    )
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.timeout = 0.2
    ready.put(server.server_port)
    try:
        while not stop.is_set():
            server.handle_request()
    finally:
        server.server_close()


class _LoopbackPinnedHTTPSConnection:
    """Real TLS socket; test-only route around public-DNS address pinning."""
    def __init__(self, host, port, *, pinned_ip, timeout, context, local_port):
        import http.client
        self._connection = http.client.HTTPSConnection(
            host, port=port, timeout=timeout, context=context
        )
        self._local_port = local_port
        self.sock = None

    def request(self, *args, **kwargs):
        self._connection.connect = self.connect
        return self._connection.request(*args, **kwargs)

    def connect(self):
        import socket
        raw = socket.create_connection(("127.0.0.1", self._local_port),
                                       self._connection.timeout)
        try:
            self._connection.sock = self._connection._context.wrap_socket(
                raw, server_hostname=self._connection.host
            )
            self.sock = self._connection.sock
        except Exception:
            raw.close()
            raise

    def getresponse(self):
        return self._connection.getresponse()

    def close(self):
        self._connection.close()


class FederationLiveMTLSNetworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ca_key = Ed25519PrivateKey.generate()
        self.server_key = Ed25519PrivateKey.generate()
        self.client_key = Ed25519PrivateKey.generate()
        now = datetime.now(timezone.utc)
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "KIN-SYS test CA")])
        ca_cert = _issue_certificate(
            subject=ca_name, issuer=ca_name, public_key=self.ca_key.public_key(),
            issuer_key=self.ca_key, serial=x509.random_serial_number(), now=now,
            extensions=[(x509.BasicConstraints(ca=True, path_length=1), True)],
        )
        server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "receiver.test")])
        server_cert = _issue_certificate(
            subject=server_name, issuer=ca_name, public_key=self.server_key.public_key(),
            issuer_key=self.ca_key, serial=x509.random_serial_number(), now=now,
            extensions=[
                (x509.SubjectAlternativeName([x509.DNSName("receiver.test")]), False),
                (x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False),
            ],
        )
        client_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sender-installation")])
        client_cert = _issue_certificate(
            subject=client_name, issuer=ca_name, public_key=self.client_key.public_key(),
            issuer_key=self.ca_key, serial=x509.random_serial_number(), now=now,
            extensions=[
                (x509.SubjectAlternativeName([
                    x509.UniformResourceIdentifier(
                        "spiffe://kin-sys/installations/INSTALL-UG-01"
                    )
                ]), False),
                (x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), False),
            ],
        )
        paths = {
            "ca": ca_cert.public_bytes(serialization.Encoding.PEM),
            "server_cert": server_cert.public_bytes(serialization.Encoding.PEM),
            "server_key": _pem_key(self.server_key),
            "client_cert": client_cert.public_bytes(serialization.Encoding.PEM),
            "client_key": _pem_key(self.client_key),
        }
        for name, value in paths.items():
            path = os.path.join(self.temp.name, name + ".pem")
            with open(path, "wb") as stream:
                stream.write(value)
            paths[name] = path
        self.paths = paths

        class Receiver:
            def __init__(inner):
                inner.received = []
            def receive(inner, envelope):
                inner.received.append(envelope)
                return {
                    "delivery_status": "COMPLETED",
                    "message_id": envelope["claims"]["message_id"],
                    "receiver_acknowledgement": {
                        "claims": {"message_id": envelope["claims"]["message_id"]},
                        "response": {"decision": "RECEIVED"},
                        "signature": "test-receipt",
                    },
                }

        self.receiver = Receiver()
        app = FederationHttpIngressApplication(
            receiver=self.receiver,
            peer_identity_resolver=mtls_peer_identity_resolver,
        )
        self.server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.server_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.server_context.load_cert_chain(paths["server_cert"], paths["server_key"])
        self.server_context.load_verify_locations(cafile=paths["ca"])
        self.server_context.verify_mode = ssl.CERT_REQUIRED
        self.server = make_server(
            "127.0.0.1", 0, app, server_class=WSGIServer,
            handler_class=_PeerCertificateWSGIHandler,
        )
        self.local_port = self.server.server_port
        self.server.socket = self.server_context.wrap_socket(
            self.server.socket, server_side=True
        )
        self.server_thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.server_thread.start()
        self.addCleanup(self._stop_server)

        self.client_context = ssl.create_default_context(cafile=paths["ca"])
        self.client_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.client_context.load_cert_chain(paths["client_cert"], paths["client_key"])

        def factory(host, port, *, pinned_ip, timeout, context):
            return _LoopbackPinnedHTTPSConnection(
                host, port, pinned_ip=pinned_ip, timeout=timeout,
                context=context, local_port=self.local_port,
            )
        self.transport = FederationHTTPSJSONTransport(
            destinations={"INSTALL-RW-01": "https://receiver.test"},
            resolver=lambda host, port, type: [
                (2, 1, 6, "", ("8.8.8.8", port))
            ],
            connection_factory=factory,
            ssl_context=self.client_context,
        )

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)

    def _envelope(self, issuer="INSTALL-UG-01"):
        return {
            "claims": {
                "message_id": "MSG-LIVE-MTLS-01",
                "issuer_installation_id": issuer,
                "receiver_installation_id": "INSTALL-RW-01",
            },
            "signature": "x" * 86,
        }

    def test_sender_and_receiver_complete_real_mutual_tls_exchange(self):
        response = self.transport.post("INSTALL-RW-01", self._envelope())
        self.assertEqual(response["http_status"], 200)
        self.assertEqual(response["delivery_status"], "COMPLETED")
        self.assertEqual(len(self.receiver.received), 1)
        self.assertEqual(
            self.receiver.received[0]["claims"]["issuer_installation_id"],
            "INSTALL-UG-01",
        )


    def test_sender_and_receiver_exchange_across_separate_os_processes(self):
        context = multiprocessing.get_context("spawn")
        ready = context.Queue()
        received = context.Queue()
        stop = context.Event()
        process = context.Process(
            target=_serve_mtls_receiver_process,
            args=((self.paths, ready, received, stop),),
            daemon=True,
        )
        process.start()
        self.addCleanup(lambda: process.is_alive() and process.terminate())
        self.addCleanup(lambda: process.join(timeout=3))
        port = ready.get(timeout=10)

        def factory(host, destination_port, *, pinned_ip, timeout, context):
            return _LoopbackPinnedHTTPSConnection(
                host, destination_port, pinned_ip=pinned_ip, timeout=timeout,
                context=context, local_port=port,
            )

        transport = FederationHTTPSJSONTransport(
            destinations={"INSTALL-RW-01": "https://receiver.test"},
            resolver=lambda host, destination_port, type: [
                (2, 1, 6, "", ("8.8.8.8", destination_port))
            ],
            connection_factory=factory,
            ssl_context=self.client_context,
        )
        response = transport.post("INSTALL-RW-01", self._envelope())
        self.assertEqual(response["http_status"], 200)
        self.assertEqual(response["delivery_status"], "COMPLETED")
        delivered = received.get(timeout=5)
        self.assertEqual(
            delivered["claims"]["issuer_installation_id"], "INSTALL-UG-01"
        )
        self.assertNotEqual(process.pid, os.getpid())
        stop.set()
        process.join(timeout=3)
        self.assertFalse(process.is_alive())

    def test_certificate_identity_must_match_envelope_issuer_over_live_tls(self):
        with self.assertRaisesRegex(
            FederationHTTPTransportError, "REMOTE_HTTP_403"
        ):
            self.transport.post(
                "INSTALL-RW-01", self._envelope(issuer="INSTALL-OTHER-99")
            )
        self.assertEqual(len(self.receiver.received), 0)

    def test_receiver_requires_a_client_certificate(self):
        no_cert_context = ssl.create_default_context(cafile=self.paths["ca"])
        no_cert_context.minimum_version = ssl.TLSVersion.TLSv1_2
        no_cert_transport = FederationHTTPSJSONTransport(
            destinations={"INSTALL-RW-01": "https://receiver.test"},
            resolver=lambda host, port, type: [
                (2, 1, 6, "", ("8.8.8.8", port))
            ],
            connection_factory=lambda host, port, *, pinned_ip, timeout, context:
                _LoopbackPinnedHTTPSConnection(
                    host, port, pinned_ip=pinned_ip, timeout=timeout,
                    context=context, local_port=self.local_port,
                ),
            ssl_context=no_cert_context,
        )
        with self.assertRaises(FederationHTTPTransportError):
            no_cert_transport.post("INSTALL-RW-01", self._envelope())
        self.assertEqual(len(self.receiver.received), 0)


if __name__ == "__main__":
    unittest.main()
