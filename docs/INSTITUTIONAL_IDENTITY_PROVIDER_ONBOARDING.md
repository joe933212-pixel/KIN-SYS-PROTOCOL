# Institutional identity-provider onboarding

**Status:** Reference implementation of tenant-scoped OIDC provider registration and runtime adapter selection. Not production certification.  
**Copyright:** Joel Kagoro © 2026.

## Purpose

Each institution configures its own identity provider. KIN-SYS does not operate a central identity authority and does not infer an issuer from an incoming credential. The institution's authenticated administration workflow registers a provider policy under its own tenant, approves it, and binds it to the institution's identity-resolution path.

The implementation consists of:

- `institutional_identity_provider_registry.py`: immutable tenant-scoped provider configuration versions, a current-version pointer, and append-only lifecycle events.
- `oidc_institutional_adapter_factory.py`: constructs an OIDC adapter only from the current active provider configuration.
- `tests/test_identity_provider_registry.py`: lifecycle, tenant-isolation, policy and runtime-selection tests.

## Registration contract

A provider configuration pins:

- tenant ID, provider ID and immutable version;
- protocol (`OIDC` in this increment);
- exact HTTPS issuer and client ID;
- explicit hostname allowlist, including the issuer and any separate JWKS host;
- asymmetric signing algorithm allowlist;
- permitted audiences, assurance contexts (ACR) and required authentication methods (AMR);
- configuration validity window and JWKS cache lifetime.

The registry rejects unknown fields. It deliberately has no field for a client secret, private signing key, password, or token. Those belong in the institution's secret manager or identity provider. The provider's client ID is not a secret.

New registrations must begin in `PENDING`. The registry supports controlled status transitions through immutable versions: `PENDING → ACTIVE`, `ACTIVE → SUSPENDED/REVOKED`, and `SUSPENDED → ACTIVE/REVOKED`. `REVOKED` is terminal. A configuration revision creates a new `PENDING` version and moves the current head to it; the old runtime configuration is no longer selected while the replacement awaits approval. This is deliberately fail-closed.

The registry records actor and evidence references, but it does not authenticate the administrator or decide whether that actor is authorised to register, approve, suspend or revoke a provider. The application must verify the actor and relevant institutional authority before invoking a mutation. A tenant ID supplied in an untrusted request body is not an authenticated tenant context.

## Runtime wiring

```python
from institutional_identity import InstitutionalIdentityResolver
from institutional_identity_provider_registry import InstitutionalIdentityProviderRegistry
from oidc_institutional_adapter_factory import OIDCInstitutionalAdapterFactory

provider_registry = InstitutionalIdentityProviderRegistry("institutional-identity.sqlite")
adapter_factory = OIDCInstitutionalAdapterFactory(registry=provider_registry)

identity_resolver = InstitutionalIdentityResolver(
    adapter_loader=adapter_factory,
    issuer_policy_loader=provider_registry.resolve_active_issuer_policy,
    subject_binding_loader=load_active_tenant_subject_binding,
    consume_nonce=consume_nonce_once,
)
```

The application selects `provider_id` from trusted receiver-side configuration for the route or institution. It must not select it from token claims. The factory resolves only the current active version for the supplied tenant/provider pair, then creates the HTTPS transport, discovery/JWKS loader and OIDC ID-token adapter from the stored policy.

Issuer-policy resolution is separately tenant-scoped and fails closed unless exactly one active provider configuration matches the issuer. This avoids ambiguous policy selection where multiple active provider registrations share an issuer. The audience passed to authentication must still be the expected audience for the selected application.

## Suspension, revocation and change

A suspension or revocation moves the current version pointer to a new immutable status snapshot. The adapter factory checks the current registry head on every selection, so it stops returning a provider as soon as the new status is committed. A new configuration version is not usable until it is explicitly activated.

For emergency incident response, the institution should also invalidate any in-process identity sessions and revoke or disable the corresponding subject bindings as appropriate. Provider suspension does not retroactively undo already completed operations; KIN-SYS operation evidence must retain the provider ID and exact configuration version used at authentication time.

## Remaining controls

This is a SQLite reference registry, not a distributed production control plane. Production deployment still requires an authenticated administration API, policy-authorised lifecycle coordinator, shared durable storage, secret-manager integration where client credentials are required, operational key/revocation procedures, provider-specific conformance tests, audit retention and independent security review.

The current adapter supports OIDC ID-token verification. It does not implement SAML, SCIM provisioning, passkeys as a direct KIN-SYS credential protocol, identity proofing, or legal verification of the institution's right to use the external identity provider. Those remain separate provider integrations and governance responsibilities.
