# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.

# Controlled live OIDC integration profile

The deterministic OIDC tests use generated keys and fixture-backed provider
documents. This opt-in profile is the next validation layer: it exercises the
real institution-controlled issuer over DNS, certificate-verified HTTPS,
discovery metadata, JWKS retrieval and signed ID-token verification.

It is not a production certification or a substitute for an independently
operated identity-provider security review.

## Test issuer requirements

Use a dedicated, disposable Keycloak realm (or another OIDC-conformant issuer)
that is reachable through public DNS and HTTPS. The configured issuer hostname
and every hostname used by its `jwks_uri` must be explicitly allowlisted.
The current reference transport rejects private, loopback, link-local and
reserved destination addresses. A local Docker-only hostname therefore cannot
be used with this production-policy transport.

Configure a confidential or public test client with:
- the exact client ID used as the expected audience;
- OpenID Connect authorization-code flow;
- a test redirect URI controlled by the operator;
- a dedicated non-production test user;
- an ID token containing the assurance claims required by the realm policy.

Do not enable password/direct-access grants merely to simplify this test.
Obtain a fresh ID token through the authorization-code flow. Do not use a
production user, production client, production credentials or production
issuer. The token is short-lived and must be handled as a secret.

## Run the live profile

Install the same vetted dependencies used by CI:

```sh
python -m pip install -r protocol-reference/requirements-trust.txt
python -m pip install -r protocol-reference/requirements-identity.txt
```

Set these environment variables in the shell that runs the tests. Do not put
the token in a committed file, workflow YAML, issue, log or shell history.

- `KIN_SYS_OIDC_LIVE_ISSUER`: exact issuer URL from discovery metadata.
- `KIN_SYS_OIDC_LIVE_CLIENT_ID`: test client ID / expected audience.
- `KIN_SYS_OIDC_LIVE_ALLOWED_HOSTS`: comma-separated, lowercase exact host
  allowlist covering the issuer and JWKS endpoints.
- `KIN_SYS_OIDC_LIVE_ID_TOKEN`: fresh compact signed ID token.
- `KIN_SYS_OIDC_LIVE_NONCE`: the exact nonce bound to the authorization
  request that produced the token.
- `KIN_SYS_OIDC_LIVE_TENANT_ID`: synthetic KIN-SYS tenant reference for the
  test institution.

Then run:

```sh
python -m unittest protocol-reference.tests.test_oidc_live_provider -v
```

The normal conformance suite discovers this test too. When the environment is
not configured, the three live cases are explicitly reported as skipped; they
must not be represented as live-provider validation.

## What this profile proves

When run against the controlled live issuer, the tests establish that:
1. the institution-pinned discovery endpoint is reached through the bounded
   HTTPS transport with certificate and hostname verification;
2. the discovered issuer and JWKS endpoint satisfy the configured host policy;
3. the real JWKS is loaded and used to verify the supplied signed ID token;
4. the resulting identity assertion retains the configured tenant context;
5. a mismatched nonce is rejected; and
6. a modified token is rejected.

The test does not yet automate Keycloak provisioning, signing-key rotation,
provider-registry activation/suspension, network outage/recovery, or a complete
authenticated institutional administrator workflow. Those are separate
acceptance cases and must be completed before the live OIDC integration gate
can be considered closed. In particular, successful token verification does
not create an authority grant: identity and authority remain separate KIN-SYS
controls.

## Evidence to retain

For each controlled run, record the issuer hostname (not the token), realm
configuration version, test date, source commit, test command, pass/skip
counts, and sanitized failure codes. Never retain ID tokens, client secrets,
user passwords, private keys or authorization codes in test reports.
