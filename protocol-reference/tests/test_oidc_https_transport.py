# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import json
import socket
import ssl
import unittest

from oidc_https_transport import OIDCHTTPSJSONTransport, OIDCTransportError


def resolver_for(addresses):
    def resolve(host, port, type):
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET,
             socket.SOCK_STREAM, 6, "", (address, port, 0, 0)
             if ":" in address else (address, port))
            for address in addresses
        ]
    return resolve


class FakeSocket:
    def settimeout(self, value):
        self.timeout = value


class FakeResponse:
    def __init__(self, status=200, headers=None, body=b'{"issuer":"https://id.example"}'):
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}
        self.body = body

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, amount):
        return self.body[:amount]


class FakeConnection:
    def __init__(self, host, port, *, pinned_ip, timeout, context, response=None):
        self.host, self.port, self.pinned_ip = host, port, pinned_ip
        self.timeout, self.context = timeout, context
        self.response = response or FakeResponse()
        self.sock = FakeSocket()
        self.request_args = None
        self.closed = False

    def request(self, method, path, headers):
        self.request_args = (method, path, headers)

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class OIDCHTTPSJSONTransportTests(unittest.TestCase):
    def setUp(self):
        self.connections = []
        def factory(*args, **kwargs):
            connection = FakeConnection(*args, **kwargs)
            self.connections.append(connection)
            return connection
        self.transport = OIDCHTTPSJSONTransport(
            allowed_hosts=("id.example", "keys.example"),
            resolver=resolver_for(["93.184.216.34"]),
            connection_factory=factory,
        )

    def fetch(self, url="https://id.example/.well-known/openid-configuration",
              response=None, max_bytes=4096):
        if response is not None:
            self.connections.clear()
            self.transport._connection_factory = lambda *a, **kw: FakeConnection(
                *a, **kw, response=response
            )
        return self.transport(url, timeout_seconds=5, max_bytes=max_bytes)

    def test_pins_resolved_ip_and_uses_host_for_http_and_tls(self):
        result = self.fetch()
        self.assertEqual(result["issuer"], "https://id.example")
        connection = self.connections[0]
        self.assertEqual(connection.pinned_ip, "93.184.216.34")
        method, path, headers = connection.request_args
        self.assertEqual((method, path), ("GET", "/.well-known/openid-configuration"))
        self.assertEqual(headers["Host"], "id.example")
        self.assertTrue(connection.closed)

    def test_rejects_non_allowlisted_host_before_resolution(self):
        with self.assertRaises(OIDCTransportError):
            self.fetch("https://attacker.example/metadata")
        self.assertEqual(self.connections, [])

    def test_rejects_private_loopback_link_local_and_reserved_dns_answers(self):
        for address in ("127.0.0.1", "10.0.0.8", "169.254.169.254",
                        "::1", "fe80::1", "192.0.2.1"):
            transport = OIDCHTTPSJSONTransport(
                allowed_hosts=("id.example",),
                resolver=resolver_for([address]),
                connection_factory=lambda *a, **kw: self.fail("must not connect"),
            )
            with self.subTest(address=address), self.assertRaises(OIDCTransportError):
                transport("https://id.example/config", timeout_seconds=5, max_bytes=4096)

    def test_rejects_mixed_public_and_private_dns_answers(self):
        transport = OIDCHTTPSJSONTransport(
            allowed_hosts=("id.example",),
            resolver=resolver_for(["93.184.216.34", "10.0.0.2"]),
            connection_factory=lambda *a, **kw: self.fail("must not connect"),
        )
        with self.assertRaises(OIDCTransportError):
            transport("https://id.example/config", timeout_seconds=5, max_bytes=4096)

    def test_rejects_redirect_without_following_location(self):
        with self.assertRaises(OIDCTransportError) as caught:
            self.fetch(response=FakeResponse(
                status=302, headers={"Location": "https://169.254.169.254/latest"}
            ))
        self.assertEqual(str(caught.exception), "OIDC_REDIRECT_REJECTED")

    def test_rejects_oversized_content_length_and_streamed_body(self):
        with self.assertRaises(OIDCTransportError):
            self.fetch(response=FakeResponse(
                headers={"Content-Type": "application/json", "Content-Length": "5000"}
            ), max_bytes=4096)
        with self.assertRaises(OIDCTransportError):
            self.fetch(response=FakeResponse(body=b"x" * 4097), max_bytes=4096)

    def test_rejects_wrong_content_type_and_malformed_json(self):
        with self.assertRaises(OIDCTransportError):
            self.fetch(response=FakeResponse(
                headers={"Content-Type": "text/plain"}
            ))
        with self.assertRaises(OIDCTransportError):
            self.fetch(response=FakeResponse(body=b'{"issuer":'))

    def test_rejects_duplicate_keys_and_non_finite_numbers(self):
        for body in (b'{"issuer":"a","issuer":"b"}', b'{"value":NaN}'):
            with self.subTest(body=body), self.assertRaises(OIDCTransportError):
                self.fetch(response=FakeResponse(body=body))

    def test_rejects_url_userinfo_query_port_and_redirect_schemes(self):
        for url in (
            "http://id.example/config",
            "https://user@id.example/config",
            "https://id.example:8443/config",
            "https://id.example/config#fragment",
        ):
            with self.subTest(url=url), self.assertRaises(OIDCTransportError):
                self.fetch(url)

    def test_bounds_timeouts(self):
        self.fetch()
        self.assertLessEqual(self.connections[0].timeout, 5)
        self.assertEqual(self.connections[0].sock.timeout, 5)


    def test_rejects_unverified_tls_context(self):
        context = ssl._create_unverified_context()
        with self.assertRaises(OIDCTransportError) as caught:
            OIDCHTTPSJSONTransport(
                allowed_hosts=("id.example",), ssl_context=context,
                resolver=resolver_for(["93.184.216.34"]),
            )
        self.assertEqual(str(caught.exception), "OIDC_TLS_VERIFICATION_REQUIRED")

    def test_tls_certificate_failure_is_fail_closed(self):
        def tls_failure(*args, **kwargs):
            raise ssl.SSLCertVerificationError("certificate mismatch")
        transport = OIDCHTTPSJSONTransport(
            allowed_hosts=("id.example",),
            resolver=resolver_for(["93.184.216.34"]),
            connection_factory=tls_failure,
        )
        with self.assertRaises(OIDCTransportError) as caught:
            transport("https://id.example/config", timeout_seconds=5, max_bytes=4096)
        self.assertEqual(str(caught.exception), "OIDC_PROVIDER_CONNECTION_FAILED")

    def test_dns_rebinding_is_checked_again_and_connection_is_ip_pinned(self):
        answers = [["93.184.216.34"], ["10.0.0.7"]]
        calls = []
        def changing_resolver(host, port, type):
            addresses = answers[min(len(calls), 1)]
            calls.append(addresses)
            return resolver_for(addresses)(host, port, type)
        transport = OIDCHTTPSJSONTransport(
            allowed_hosts=("id.example",),
            resolver=changing_resolver,
            connection_factory=lambda *a, **kw: self.connections.append(
                FakeConnection(*a, **kw)
            ) or self.connections[-1],
        )
        transport("https://id.example/config", timeout_seconds=5, max_bytes=4096)
        self.assertEqual(self.connections[0].pinned_ip, "93.184.216.34")
        with self.assertRaises(OIDCTransportError):
            transport("https://id.example/config", timeout_seconds=5, max_bytes=4096)
        self.assertEqual(len(self.connections), 1)

    def test_dns_resolution_failure_is_fail_closed(self):
        def broken_resolver(*args, **kwargs):
            raise TimeoutError("DNS timed out")
        transport = OIDCHTTPSJSONTransport(
            allowed_hosts=("id.example",), resolver=broken_resolver,
            connection_factory=lambda *a, **kw: self.fail("must not connect"),
        )
        with self.assertRaises(OIDCTransportError) as caught:
            transport("https://id.example/config", timeout_seconds=5, max_bytes=4096)
        self.assertEqual(str(caught.exception), "OIDC_DNS_RESOLUTION_FAILED")

    def test_connection_timeout_is_fail_closed(self):
        def timed_out(*args, **kwargs):
            raise TimeoutError("connection timed out")
        transport = OIDCHTTPSJSONTransport(
            allowed_hosts=("id.example",),
            resolver=resolver_for(["93.184.216.34"]),
            connection_factory=timed_out,
        )
        with self.assertRaises(OIDCTransportError) as caught:
            transport("https://id.example/config", timeout_seconds=5, max_bytes=4096)
        self.assertEqual(str(caught.exception), "OIDC_PROVIDER_CONNECTION_FAILED")


if __name__ == "__main__":
    unittest.main()
