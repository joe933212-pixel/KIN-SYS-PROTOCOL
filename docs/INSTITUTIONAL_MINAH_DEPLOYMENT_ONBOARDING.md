# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.

# Institutional MINAH Deployment Registration and Onboarding (draft v0.1)

## Purpose

This increment provides a tenant-scoped registration and lifecycle boundary for an institution's own MINAH agent. The institution may use an internally developed agent, a third-party-hosted service, or a sovereign model/service under its own control. KIN-SYS does not require a single model vendor, central MINAH service, or KIN-SYS custody of model credentials.

The registry stores a provider-neutral deployment descriptor and governance references. It does not upload model binaries, host or train a model, establish legal identity, or independently validate the legal sufficiency of a mandate.

## Institutional ownership and agent modes

Every deployment belongs to the institution identified by the verified session tenant. The HTTP request cannot choose `tenant_id` or `owner_ref`. Agent modes are:

- `INTERNAL`: developed and operated by the deploying institution.
- `THIRD_PARTY_HOSTED`: an institution-selected external service; its endpoint is represented by a reference.
- `SOVEREIGN`: a model/service operated under the institution's sovereign control.

Deployment modes include self-hosted, on-premises, cloud, edge and managed. The descriptor may carry a secret-manager reference such as `secret://...`; it must never contain API keys, bearer tokens, passwords, private keys, credentials or model binaries.

## Governance pins

A deployment pins exact versions of:

- the tenant-local identity provider;
- the institution's mandate;
- the jurisdiction set;
- the policy pack and source pack;
- the authority profile;
- the MINAH interface contract.

Registration is allowed only when the pinned identity provider exists in the same tenant, is ACTIVE, and is at the exact requested version. Activation rechecks this relationship. The runtime-facing `resolve_active` method must be called before a deployment is selected for instruction handling; it returns no executable deployment if the deployment is not ACTIVE or its exact identity-provider pin is no longer ACTIVE/current.

The descriptor declares permitted operations, prohibited operations and decision boundaries. The validator requires `authority.grant` and `bypass.cee` to be prohibited and requires `CEE_FINAL_ADMISSION` as a decision boundary. MINAH may interpret and evaluate instructions within its mandate; it cannot create institutional authority or bypass CEE.

## Authority and lifecycle

The application service resolves a signed actor assertion against the tenant, resource type `minah_deployment` and exact deployment ID. The actor is taken from the verified authority result, never from the request body. Roles are separate for registration, configuration, activation, suspension and revocation:

- `minah_deployment_registrar`
- `minah_deployment_configurator`
- `minah_deployment_approver`
- `minah_deployment_suspension_authority`
- `minah_deployment_revocation_authority`

Every mutation requires an event ID and evidence reference. Versions are immutable; configuration revision creates a new PENDING version and advances the current pointer only under expected-version concurrency. Lifecycle transitions are persisted with an audit event in one SQLite transaction. REVOKED is terminal.

The injected authority resolver must verify the assertion's signature, issuer, tenant, actor, role/resource scope, validity and one-time nonce. A fresh assertion is required for each mutation. The local registry does not authenticate administrators or independently prove that evidence references are genuine.

## HTTP API

The routes use the verified institutional session resolver already established for identity-provider administration. Tenant context and actor assertion are obtained from that resolver.

- `POST /institutional/minah-deployments` — register a deployment in PENDING state.
- `GET /institutional/minah-deployments/{deployment_id}` — read the tenant-local current snapshot.
- `GET /institutional/minah-deployments/{deployment_id}/events` — read lifecycle audit events.
- `PUT /institutional/minah-deployments/{deployment_id}/configuration` — create a new PENDING configuration version; requires `expected_version`.
- `POST /institutional/minah-deployments/{deployment_id}/lifecycle` — activate, suspend or revoke using `expected_version` and `new_version`.

Every mutation body carries `event_id` and `evidence_ref`. Registration/configuration bodies must omit `tenant_id` and `owner_ref`; the service derives ownership from the authenticated session. A request-supplied actor or role is never accepted.

## Execution boundary

The intended governed instruction path remains:

**MINAH → Identity → Authority → CEE → Final Decision**

This registration layer describes and governs which institution-owned MINAH deployment may be selected. It is not a replacement for the runtime's identity, authority or CEE checks. In particular, registration or authentication does not grant the MINAH agent or its caller execution authority. CEE remains the final rule-governed admission/decision boundary.

The reference `resolve_active` method checks the current deployment state and exact identity-provider pin. A production runtime must invoke an equivalent check at the point of use and revalidate provider/deployment revocation before protected execution; merely reading the administrative record is not an execution authorisation.

## Current limitations

- SQLite is a local reference store, not distributed production persistence.
- Provider-neutral agent descriptors do not prove connectivity, model identity, model integrity, safety or conformance. A separate institution-controlled connectivity and conformance assessment is required before operational use.
- Mandate, jurisdiction, policy, source and authority references are pinned identifiers; their authoritative contents and legal effect must be resolved and verified by the institution's governance services.
- Secret references are opaque identifiers; this module does not retrieve secrets or connect to a secret manager.
- The authority resolver, session resolver, identity-provider registry and replay guard are injected trust boundaries.
- This increment does not deploy a model, issue identity credentials, grant permissions, execute instructions or certify production readiness.

Copyright © 2026 Joel Kagoro.
