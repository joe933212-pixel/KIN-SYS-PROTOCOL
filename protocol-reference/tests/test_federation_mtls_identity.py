# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID

from federation_mtls_identity import (
    FederationMTLSPeerIdentityError,
    installation_id_from_verified_client_certificate,
    mtls_peer_identity_resolver,
)


def certificate_pem(*uris):
    key = Ed25519PrivateKey.generate()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "KIN-SYS test peer")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier(uri) for uri in uris]
            ),
            critical=False,
        )
        .sign(key, algorithm=None)
    )
    return cert.public_bytes(x509.Encoding.PEM) if hasattr(x509, "Encoding") else cert.public_bytes(
        __import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.PEM
    )


class FederationMTLSPeerIdentityTests(unittest.TestCase):
    def test_extracts_installation_from_exact_spiffe_uri(self):
        pem = certificate_pem("spiffe://kin-sys/installations/INSTALL-UG-01")
        self.assertEqual(
            installation_id_from_verified_client_certificate({"SSL_CLIENT_CERT": pem}),
            "INSTALL-UG-01",
        )

    def test_drop_in_resolver_uses_server_certificate_environment_value(self):
        pem = certificate_pem("spiffe://kin-sys/installations/INSTALL-RW-01")
        self.assertEqual(mtls_peer_identity_resolver({"SSL_CLIENT_CERT": pem}), "INSTALL-RW-01")

    def test_ignores_caller_controlled_http_headers(self):
        with self.assertRaisesRegex(
            FederationMTLSPeerIdentityError, "CERTIFICATE_MISSING"
        ):
            mtls_peer_identity_resolver({
                "HTTP_SSL_CLIENT_CERT": "spiffe://kin-sys/installations/ATTACKER",
                "HTTP_X_KIN_SYS_PEER_INSTALLATION": "ATTACKER",
            })

    def test_rejects_missing_or_malformed_certificate(self):
        for environ in ({}, {"SSL_CLIENT_CERT": "not a certificate"}):
            with self.assertRaises(FederationMTLSPeerIdentityError):
                mtls_peer_identity_resolver(environ)

    def test_requires_exactly_one_kin_sys_installation_identity(self):
        cases = (
            ("spiffe://other-domain/installations/INSTALL-UG-01",),
            (
                "spiffe://kin-sys/installations/INSTALL-UG-01",
                "spiffe://kin-sys/installations/INSTALL-RW-01",
            ),
            ("spiffe://kin-sys/installations/invalid/id",),
        )
        for uris in cases:
            with self.subTest(uris=uris):
                pem = certificate_pem(*uris)
                with self.assertRaisesRegex(
                    FederationMTLSPeerIdentityError, "IDENTITY_AMBIGUOUS"
                ):
                    mtls_peer_identity_resolver({"SSL_CLIENT_CERT": pem})

    def test_rejects_oversized_certificate_input(self):
        with self.assertRaisesRegex(
            FederationMTLSPeerIdentityError, "CERTIFICATE_SIZE_INVALID"
        ):
            installation_id_from_verified_client_certificate({
                "SSL_CLIENT_CERT": b"x" * (16 * 1024 + 1)
            })


if __name__ == "__main__":
    unittest.main()
