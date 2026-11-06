# KIN-SYS OIDC HTTPS transport

**Status:** Hardened reference transport; integration and adversarial tests included. Not a production certification.  
**Copyright:** Joel Kagoro © 2026.

## Purpose

`oidc_https_transport.py` provides the concrete `fetch_json` callable required by `OIDCDiscoveryJWKSLoader`. It keeps outbound OIDC discovery and JWKS retrieval inside the receiving institution's trust boundary.

## Wiring

```python
from oidc_discovery import OIDCDiscoveryJWKSLoader
from oidc_https_transport import OIDCHTTPSJSONTransport

issuer = "https://identity.example.ug/tenant-a"
trusted_hosts = ("identity.example.ug", "keys.example.ug")

transport = OIDCHTTPSJSONTransport(allowed_hosts=trusted_hosts)
jwks_loader = OIDCDiscoveryJWKSLoader(
    issuer=issuer,
    allowed_hosts=trusted_hosts,
    fetch_json=transport,
    allowed_algorithms=("RS256", "ES256"),
)
```

The adapter and loader must be configured from institution-controlled settings. Do not derive issuer URLs, allowed hosts, audiences or algorithm policy from an incoming token or untrusted request.

## Transport controls

- HTTPS only, with a default certificate-verifying SSL context. A caller-supplied context is rejected unless certificate-chain verification and hostname checking are both enabled.
- Exact hostname allowlist; URL user information, fragments, non-standard ports, control characters and non-HTTPS schemes are rejected.
- DNS answers are resolved for each request and every answer must be globally routable. Mixed public/private answers fail closed.
- The selected validated IP address is passed directly to the socket connection. TLS SNI and certificate hostname validation continue to use the configured DNS hostname. This avoids a second DNS lookup between validation and connection (the common DNS-rebinding gap).
- Redirects are never followed. Every response must be HTTP 200 with JSON or a `+json` media type.
- Connect and read timeouts are separately bounded. The read is streamed with a `max_bytes + 1` cap; an oversized body is rejected even if `Content-Length` is absent or false.
- Duplicate JSON object keys and non-finite JSON constants are rejected. The decoded top-level value must be an object.
- Connections are closed on success and failure. Raw token material is not handled by this transport.

## Security boundary and deployment requirements

The resolver uses the host operating system's DNS resolver by default. Python's standard `getaddrinfo` does not provide a portable per-query deadline; production deployments should use a bounded resolver and enforce outbound network egress policy at the host/container/network layer as defense in depth. The transport pins the actual socket destination to the validated address, but cannot compensate for a compromised host, trust store, resolver, kernel or network path.

`ipaddress.is_global` is used as a conservative destination check. Environments with unusual public address allocations should review this policy rather than weakening it ad hoc. The institution must allowlist both the issuer metadata host and a separate JWKS host when applicable.

The loader's bounded cache and rate-limited unknown-key refresh remain in force. A successful cache entry is not proof that a key has not been revoked upstream during the cache interval. Institutions must select cache lifetimes appropriate to their risk, define emergency cache invalidation and issuer suspension procedures, and fail closed when a required refresh cannot be completed. This reference transport does not implement provider introspection or a global revocation feed.

## Verification

`tests/test_oidc_https_transport.py` exercises public-IP pinning, hostname allowlisting, private/loopback/link-local/reserved address rejection, mixed DNS answers, DNS rebinding between requests, redirects, response-size limits, content types, malformed and duplicate-key JSON, TLS verification configuration, TLS failures, DNS failures, connection timeouts, and end-to-end wiring into discovery/JWKS loading.

These deterministic tests do not constitute a live connection to an institutional identity provider. Before production use, run integration tests against a controlled test IdP and verify its real certificate chain, discovery metadata, JWKS rotation, outage behavior and emergency key-revocation procedure.
