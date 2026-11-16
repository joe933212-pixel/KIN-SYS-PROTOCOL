# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Resolve an authenticated mTLS peer to one KIN-SYS installation identity.

TLS termination and certificate-chain validation MUST be performed by the
trusted HTTPS server/edge. That edge must expose the verified client
certificate as the server-controlled WSGI environ value SSL_CLIENT_CERT,
strip any caller-supplied HTTP header that could map to that value, and block
direct access to the application. This resolver never reads HTTP_* headers
or a certificate identity supplied in the JSON request body.
"""
from __future__ import annotations

import re
from typing import Any
from cryptography import x509
from cryptography.x509.oid import ExtensionOID

_MAX_CERTIFICATE_BYTES = 16 * 1024
_INSTALLATION_URI = re.compile(
    r"^spiffe://kin-sys/installations/"
    r"([A-Za-z0-9][A-Za-z0-9._:-]{0,127})$"
)


class FederationMTLSPeerIdentityError(ValueError):
    """The trusted TLS edge did not provide one valid peer identity."""


def installation_id_from_verified_client_certificate(
    environ: dict[str, Any],
    *,
    environ_key: str = "SSL_CLIENT_CERT",
) -> str:
    """Extract the sole KIN-SYS installation URI from a verified peer cert.

    The certificate chain, validity, EKU and revocation status are the TLS
    terminator's responsibility. This function only parses the certificate
    supplied through the explicitly trusted WSGI server environ key.
    """
    if not isinstance(environ, dict) or not isinstance(environ_key, str) or not environ_key:
        raise FederationMTLSPeerIdentityError("FEDERATION_MTLS_ENVIRONMENT_INVALID")
    value = environ.get(environ_key)
    if isinstance(value, str):
        try:
            raw = value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise FederationMTLSPeerIdentityError(
                "FEDERATION_MTLS_CERTIFICATE_ENCODING_INVALID"
            ) from exc
    elif isinstance(value, bytes):
        raw = value
    else:
        raise FederationMTLSPeerIdentityError("FEDERATION_MTLS_PEER_CERTIFICATE_MISSING")
    if not raw or len(raw) > _MAX_CERTIFICATE_BYTES:
        raise FederationMTLSPeerIdentityError("FEDERATION_MTLS_PEER_CERTIFICATE_SIZE_INVALID")
    try:
        certificate = x509.load_pem_x509_certificate(raw)
        extension = certificate.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        )
        uri_names = extension.value.get_values_for_type(x509.UniformResourceIdentifier)
    except (ValueError, TypeError, x509.ExtensionNotFound) as exc:
        raise FederationMTLSPeerIdentityError(
            "FEDERATION_MTLS_PEER_CERTIFICATE_INVALID"
        ) from exc
    identities = []
    for uri in uri_names:
        match = _INSTALLATION_URI.fullmatch(uri)
        if match:
            identities.append(match.group(1))
    if len(identities) != 1:
        raise FederationMTLSPeerIdentityError(
            "FEDERATION_MTLS_INSTALLATION_IDENTITY_AMBIGUOUS"
        )
    return identities[0]


def mtls_peer_identity_resolver(environ: dict[str, Any]) -> str:
    """Drop-in peer_identity_resolver for FederationHttpIngressApplication."""
    return installation_id_from_verified_client_certificate(environ)
