# Institutional Deployment Domain Model

**Status:** Engineering baseline, draft v0.1. Domain contract only; not production security or legal assurance.  
**Copyright:** Joel Kagoro © 2026. See repository licensing and notice files.

## Purpose

Define the ownership and version relationships required before implementing institutional onboarding APIs and dashboard workflows. This model extends, rather than replaces, the existing MINAH deployment-profile and approval contracts.

## Domain aggregate

An institutional deployment consists of four independently versioned records:

1. **Institution:** stable institutional identity, claimed jurisdiction, verification state and verification-evidence reference.
2. **Jurisdiction package:** institution-scoped package version containing source records, provenance, effective dates, status and approval evidence.
3. **MINAH deployment:** institution-owned adapter registration, model/provider reference, interface version and deployment lifecycle.
4. **Deployment binding:** association of one institution, one exact jurisdiction package version, one MINAH deployment, and pinned policy/source/authority profile versions.

The binding is the unit eligible for activation. A model, source pack, policy pack, jurisdiction package or authority-profile update creates a new version or binding; it must not silently mutate the context already pinned to an admitted operation.

## Ownership and isolation invariants

- Every institution-owned record carries an immutable owner institution reference.
- A MINAH deployment and its binding must belong to the same institution as the institution record.
- A binding identifies an exact jurisdiction package ID and version, never a floating latest alias.
- Cross-tenant references are rejected before activation. Shared infrastructure must enforce tenant isolation in storage, authorization, routing, logs, caches and evidence access; identifiers alone do not provide isolation.
- Institutional identity, legal authority, AI output and administrator permissions are separate claims. An owner reference is not proof of ownership or authority.
- KIN-SYS standardises the protocol interface and execution constraints; it does not own the institution's model, prompts, source corpus or mandate.

## Jurisdiction source lifecycle

A source record includes a stable source reference, source type, title, publisher reference, content digest, provenance reference, effective-from date, status and, when approved, approval evidence. Effective-to dates, amendment/supersession links, retrieval location, language, issuing authority and signature/verification evidence should also be retained where applicable.

The lifecycle is explicit: SUBMITTED → VALIDATED → APPROVED. A source may also be REJECTED or SUPERSEDED. Uploading or hashing a document proves neither authenticity nor legal force. Approval must be performed by a verified actor with authority for the source and jurisdiction, under a separately enforced approval policy. A package may be approved only when its sources and package version have the required approvals and effective-date checks.

## MINAH adapter registration

Initial integration modes:

- NATIVE: institution-operated agent behind the standard adapter contract.
- EXTERNAL_SERVICE: authenticated service endpoint controlled by the institution or selected provider.
- MANAGED_RUNTIME: institution's agent hosted in an approved isolated runtime.

The domain model stores adapter and model references, not API keys, passwords, private keys or model binaries. Credentials belong in a secret manager. Model binaries and runtime artifacts require separate supply-chain, isolation and attestation design. Registration does not imply connectivity, conformance or authority.

The existing reference seam exposes review, verify_principal and verify_action. The serialised wire contract, authentication, timeout semantics, model/version provenance, uncertainty handling and evaluation corpus must be frozen before external integrations are accepted.

## Lifecycle and activation

Institution: PENDING_VERIFICATION → VERIFIED, with SUSPENDED and REVOKED controlled states.

Jurisdiction: DRAFT → SUBMITTED → VALIDATED → APPROVED → ACTIVE; it may be RETIRED.

MINAH: REGISTERED → CONNECTIVITY_VERIFIED → CONFORMANCE_PASSED → ACTIVE; it may be SUSPENDED or REVOKED.

Binding: DRAFT → VALIDATED → APPROVED → ACTIVE; it may be SUSPENDED or REVOKED.

Activation eligibility requires a verified institution; approved jurisdiction package and source records; MINAH conformance evidence; approved binding; exact owner and version matches; and authorised activation approval. The reference validator checks structural and cross-record consistency only. It does not authenticate evidence, validate legal authority, verify signatures, persist state or provide production tenant isolation.

## Operation pinning

Every governed operation must pin at least:

- institution ID and authenticated tenant context;
- binding ID and binding version;
- MINAH deployment ID, profile version and model/provider reference;
- jurisdiction package ID and exact version;
- policy pack, source pack and authority-profile versions;
- approval/evidence references and relevant freshness/expiry data.

Retries and recovery preserve this pinned context. A newly activated binding applies to new operations only unless an explicit, authorised re-evaluation procedure says otherwise. Emergency suspension/revocation prevents new admissions and triggers defined handling for in-flight operations.

## Cross-border coordination

The two-institution demonstrator models each participant as an independent owner and decision domain. Each institution's MINAH evaluates only its own mandate and jurisdictional responsibilities. One participant's approval is not a proxy for the other's. CEE coordinates the instruction through the governed path; IVM enforcement remains inside CEE. A cross-border case identifies which rules apply to which participant, the evidence each participant accepts, and the agreed coordination outcome.

## Persistence increment: reference registry

The first persistence slice is implemented in `protocol-reference/institutional_registry.py`. It uses SQLite and provides tenant-scoped record creation, retrieval and listing for the four domain resource types, immutable (resource ID, version) records, canonical JSON digests, and append-only lifecycle events with event-ID idempotency. Composite tenant/resource keys are used in the event foreign key so an event cannot attach to a record owned by another tenant.

The registry deliberately accepts a `tenant_id` from its caller; it does not authenticate or authorise that caller. The production API must derive tenant context from a verified principal and never trust a tenant ID supplied in an untrusted request body. The SQLite reference store is a single-process/local persistence increment, not a distributed registry, production isolation boundary, approval engine, or legal verification service. It records versioned state snapshots and append-only events.

The authenticated onboarding facade now requires an injected trusted evidence verifier before it creates an institution, jurisdiction package, MINAH deployment or binding record. The verifier receives tenant, resource type/ID, purpose and expected version context. A successful response must bind to the requested evidence reference and return a stable verification reference plus a SHA-256 digest; the receipt is persisted in the registration audit event in the same transaction as the initial snapshot. Missing, rejected, mismatched or malformed verification receipts fail before persistence. The verifier remains an injected trust boundary: no production evidence repository, legal-source adjudication, document authenticity service or key custody is supplied by this reference implementation.

The lifecycle policy is implemented in `protocol-reference/institutional_lifecycle_service.py`; the application boundary is implemented in `protocol-reference/institutional_lifecycle_coordinator.py`. The coordinator now requires an `InstitutionalAuthorityResolver`; it no longer accepts a caller-supplied actor dictionary as proof of authority. The resolver verifies a short-lived HMAC-SHA256 signed assertion, checks the exact authority-grant ID and version, tenant, actor, active status, validity window, role and resource scope, and consumes a one-time nonce through an injected replay guard. Only then does it construct the actor assertion passed to lifecycle policy. The lifecycle policy defines allowed state transitions and distinct authority roles for verification, review, approval, activation, suspension and revocation. The coordinator loads the exact tenant-scoped record version, evaluates the policy, validates the proposed snapshot, and asks the registry to persist the new immutable snapshot and audit event atomically with an expected-version check. Binding activation additionally requires the cross-record activation eligibility gate. The grant loader must be backed by a trusted authority-grant store; signing keys must come from a secret manager; and the nonce consumer must be a shared atomic replay store in a multi-process deployment. The included `SQLiteNonceGuard` provides atomic single-use nonce consumption across processes sharing one SQLite file and prunes expired nonce records using the assertion expiry. It remains a local/single-host reference adapter, not a distributed multi-host replay service; the in-memory guard is retained for unit tests. HMAC is a symmetric reference trust mechanism, not a public-key institutional identity standard. This layer verifies signed assertions against configured trust and a supplied grant record; it does not authenticate the human, independently establish the legal validity of a grant, or cryptographically verify the grant's underlying approval evidence.

The reference tests cover tenant-scoped visibility, immutable version conflicts, event idempotency and conflict detection, cross-tenant event attachment rejection, missing tenant context, unsupported resource types, non-canonical payloads, atomic lifecycle snapshot/event persistence, stale-version rejection and idempotent transition retries. Coordinator tests verify policy-to-registry integration, signed assertion verification, tamper rejection, expiry, replay rejection, exact grant-version pinning, grant status and resource-scope checks, failed-authority non-persistence, cross-tenant read rejection, stale-version handling and the binding activation-context gate.


## Federated institutional signatures and trust roots

The authority resolver now supports two deliberately distinct assertion profiles:

- **HMAC-SHA256** remains available for local/internal reference use where both sides are already inside one explicitly trusted administrative boundary. It is symmetric: every verifier holding the key can also create signatures. It is not the inter-institution federation mechanism.
- **Ed25519** is available for federated assertions. The issuer signs the canonical claim set with its private key; the receiving institution verifies using a public key obtained through a trusted key-reference adapter. The implementation uses the vetted `cryptography` package rather than custom cryptographic code. Federated claims bind the issuer institution ID, key ID, tenant, actor, exact grant ID/version, issue/expiry times and nonce. The resolver also requires the signed issuer to match the issuer recorded on the current authority grant.

The trust registry now records an explicit receiver-tenant-to-issuer trust relationship with status, trust-basis reference and validity window. Trust-key records are associated with an issuer institution, key ID, algorithm, public-key reference, validity and status. The resolver's `load_trust_key` adapter returns a key only while the issuer trust relationship remains active. Key validity and key revocation are independently checked. Multiple key IDs may overlap during rotation; retiring the old key is an explicit revocation action. Revoking the issuer relationship disables every key registered beneath that issuer for that receiving tenant.

The public-key reference is resolved by an injected `public_key_loader`; the SQLite registry does not store private keys and does not itself retrieve secrets or public key bytes from a vault. Trust establishment is tenant-specific and must be governed by an authenticated, auditable institutional process. A record in this local reference database is not, by itself, proof that the issuer is legally recognised or that a trust agreement is valid.

`SQLiteNonceGuard` implements the resolver's expiry-aware `consume(tenant_id, nonce, expires_at)` contract with a unique tenant/nonce key and transactional insert. Separate processes sharing the same SQLite file cannot consume the same live nonce twice. A production multi-host deployment must replace it with a shared atomic store implementing the same contract. The Ed25519 test dependency is declared in `protocol-reference/requirements-trust.txt` and installed by the conformance workflow. The portable USB sandbox's current MINAH → CEE → Final Decision demonstrator continues to use its standard-library-only synthetic path; the federation resolver is not yet exposed through the sandbox UI/API. The portable packaging work must preserve that offline, self-contained test path unless and until a separately packaged cryptographic dependency and its supply-chain verification are deliberately introduced.

### Federation limitations

This increment verifies a signed assertion against an explicitly trusted issuer key and a current authority grant. It does not yet implement a complete signed authority-grant issuance/acceptance protocol, certificate or institutional identity discovery, legal trust-agreement adjudication, key custody/HSM integration, automated rotation distribution, identity-provider authentication, or a distributed atomic replay store. The SQLite trust catalogue and in-memory nonce guard remain local reference components. Cross-tenant and cross-border trust must be established independently by each receiving institution; there is no universal KIN-SYS root that overrides institutional sovereignty.


## Implementation sequence

1. Implement and test domain validators and activation eligibility.
2. Add the SQLite reference registry with immutable versions and tenant-scoped access.
3. Implement local signed authority assertions and Ed25519 federated assertion verification against receiver-controlled issuer trust roots (reference verifier and SQLite trust metadata implemented; production key custody, issuer governance, identity and distributed replay adapters remain outstanding).
4. Add authenticated institutional registration, authority verification and mandatory trusted evidence verification (reference verifier interface and atomic audit receipts implemented; production evidence service remains outstanding).
5. Add jurisdiction source ingestion, provenance and approval workflow.
6. Add MINAH adapter registration, secret references, connectivity and conformance checks.
7. Add binding approval and activation.
8. Expose tenant-scoped APIs and onboarding dashboard.
9. Demonstrate two independent institutions and cross-border coordination.

## Acceptance criteria for this increment

- Malformed records and unknown lifecycle states fail closed.
- An uploaded source cannot be marked approved without approval evidence.
- An approved jurisdiction cannot contain unapproved sources.
- MINAH records cannot contain credentials or model binaries.
- A binding cannot point to another tenant's MINAH or jurisdiction package.
- A binding pins an exact jurisdiction package version.
- Activation eligibility fails unless every required lifecycle gate and evidence reference is present.
- Registration rejects evidence that the injected trusted verifier cannot resolve and verify for the exact tenant/resource/purpose/version context; successful verification receipts are retained in the atomic registration audit event.
- Lifecycle transitions reject illegal skips, missing authority roles, unverified actors, missing evidence, and self-approval for approval transitions.
- The coordinator rejects unsigned, expired, replayed, wrong-tenant, wrong-grant-version and out-of-scope actor assertions.
- Existing MINAH profile approval and operation-pinning behaviour remains intact.

## Explicit non-claims

This increment is not an onboarding UI, live identity verification, production-grade registry, legal-source authenticity service, production evidence repository, model upload/runtime service, cryptographic approval implementation or production tenant isolation. The SQLite reference registry is implemented, but must not be represented as a production registry. Those require separate implementations and tests.


## Institutional trust and delegated authority registry

The next persistence increment is implemented in `protocol-reference/institutional_trust_registry.py`. It introduces a separate SQLite reference store for authority grants and institutional trust-key metadata rather than mixing these records into institution/deployment aggregates.

Authority grants are tenant-scoped and immutable by (grant ID, version). A current-version head is advanced with an expected-version check; every creation, revision and revocation writes an audit event in the same transaction. The resolver-compatible `load_grant(tenant_id, grant_id)` returns only the current head. Consequently, when a grant is revised or revoked, an assertion pinned to an older grant version no longer resolves as current. The grant snapshot carries actor binding, institution, issuer institution, authority reference, role/resource scope, validity window, status, version and approval reference.

The trust-key catalogue records tenant, issuer institution, key ID, algorithm, public-key reference, validity window and status. It supports overlapping key registration for rotation and audited revocation. The registry stores a public-key reference (or public material supplied through an appropriately designed adapter), never private signing keys. The current HMAC assertion resolver has not yet been replaced or wired to this trust-key catalogue: key metadata persistence is not equivalent to signature verification or issuer trust federation. The Ed25519 label is an algorithm identifier only; this increment does not implement Ed25519 cryptographic verification.

This remains a local SQLite reference implementation. Tenant IDs are caller-supplied and must be derived from authenticated principals at the API boundary. Grant creation/revision and trust-key registration/revocation accept actor and evidence references but do not independently authenticate those actors, validate legal authority, or verify the underlying approval evidence. The registry does not yet implement issuer-signed grant envelopes, a federation trust policy, public-key verification, identity-provider integration, key lifecycle policy beyond recorded validity/status and revocation, or distributed concurrency/replay guarantees. Production deployment requires an independently governed trust-root process, vetted cryptographic libraries, secure key custody, authenticated issuer/actor adapters, authoritative evidence verification and a hardened shared persistence/replay service.

The new tests cover resolver-compatible grant loading, tenant isolation, immutable grant versions, expected-version revisions, revocation invalidating stale version pins, trust-key rotation metadata, key revocation audit events, duplicate key IDs and unsupported algorithms. These are reference-store tests, not a certification of the trust model.


## Issuer-signed authority-grant provenance and receiver acceptance

The reference implementation now includes `protocol-reference/institutional_grant_provenance.py`. An issuing institution signs a canonical Ed25519 envelope containing the complete immutable grant snapshot, signing key ID and issuer institution ID. The receiver verifies the exact grant claims against its own tenant-scoped trusted-issuer relationship and active key record. Verification checks the issuer/key association, key and trust validity windows, tenant, grant validity and signature. A changed role, resource scope, actor, institution, approval reference or version invalidates the signature.

**Issuance and acceptance are separate decisions.** The issuer may sign only a `PENDING` grant. A valid issuer signature establishes integrity and provenance under a receiver-configured key; it does not activate the grant. The receiving institution must independently authenticate its approver and enforce its own local policy through the injected `verify_receiver_approval` adapter. Acceptance is bound to the exact grant ID/version and canonical SHA-256 digest and returns an `ACTIVE` snapshot plus the original issuer envelope and receiver-approval evidence reference/payload. A receiver mismatch, missing/failed approval, expired grant, untrusted/revoked key, wrong issuer or altered claim fails closed.

The receiver-approval callback is a security boundary, not a convenience hook. Production wiring must resolve the approver from an authenticated receiver-side principal, validate the actor's current authority and role/scope, enforce separation of duties where required, and produce durable evidence. `InstitutionalTrustRegistry.accept_signed_grant(...)` now composes issuer-signature verification, receiver approval and atomic SQLite persistence: the accepted immutable ACTIVE grant snapshot, original issuer envelope, receiver-approval bundle and audit event are committed in one transaction. A duplicate grant or audit-event conflict rolls the transaction back. `load_grant_provenance(...)` retrieves the retained envelope and acceptance evidence for a specific tenant/grant/version. The method still depends on an injected receiver-approval verifier and public-key loader; callers must derive tenant and actor context from authenticated principals. The SQLite transaction is a local reference implementation, not a distributed production transaction. A subsequent grant amendment or revocation must be a new issuer-signed version and a fresh receiver acceptance; current-version pinning must invalidate assertions against earlier versions.

The signature proves that the signed bytes were produced by the holder of the private key corresponding to a key the receiver has explicitly trusted. It does not prove that the issuer is legally competent, that the referenced approval document is genuine, that the approver had lawful authority, or that the receiving institution is legally obliged to accept the grant. Those remain separate institutional governance and evidence-verification obligations. Private signing keys are never stored by this module; signing and public-key resolution are injected at the boundary.

Tests cover canonical signature verification, claim tampering, issuer/key mismatch, unknown and revoked keys, key expiry, grant expiry, cross-tenant rejection, receiver identity mismatch, receiver approval refusal and the separation between issuer signature and receiver activation. This is a reference protocol slice, not a production federation service. The trust registry integration additionally verifies atomic persistence of the ACTIVE grant, original issuer envelope, receiver acceptance and audit event; receiver refusal and audit-context mismatch leave no accepted grant, and transaction failure leaves no partial provenance.


## Institutional identity-provider integration boundary

`protocol-reference/institutional_identity.py` defines a provider-neutral authentication boundary. Each receiving institution selects an adapter from its own tenant-scoped configuration using a configured provider ID; the provider is never selected from an untrusted issuer claim in a credential. An adapter may integrate with an institution's existing OIDC/OAuth identity platform, SAML identity provider, enterprise directory gateway, passkey service or certificate-based authentication infrastructure. KIN-SYS does not require one central identity provider or a common national identity database.

The adapter is responsible for validating the native credential protocol with a vetted implementation: signature/MAC and key status, issuer, audience, token lifetime, nonce, revocation/introspection requirements and protocol-specific constraints. It returns a typed `VerifiedIdentityAssertion`; arbitrary dictionaries or a caller-supplied `verified: true` flag are not accepted. The resolver then applies the receiving institution's own issuer trust policy, allowed audiences, validity window, assurance-level (ACR) and authentication-method (AMR) requirements, freshness limits and tenant scope.

The external issuer/subject pair must map through an active, versioned, tenant-local subject binding to an internal KIN-SYS actor reference. A subject from one tenant cannot be used to resolve an actor in another tenant. Identity-provider group names and role claims are deliberately not copied into KIN-SYS authority. The result contains no delegated roles and explicitly requires a separate current authority grant. Thus authentication answers **who is acting**; the authority registry answers **what that actor is permitted to do**; CEE evaluates the instruction under the applicable institutional rules.

The identity resolver consumes a request-bound nonce using the expiry-aware replay-guard interface. Production adapters must protect credentials in transit and at rest, avoid logging raw credentials or bearer tokens, validate provider metadata and key rotation, and define account linking, suspension, recovery, MFA and incident response. The provider adapter, issuer policy loader and subject-binding loader are trusted deployment components and must be independently governed and tested. The current reference implementation does not include a live OIDC/SAML client, does not authenticate a real person, and does not establish legal identity or legal authority by itself. It defines the integration contract that institution-specific adapters must implement.

The identity-policy and subject-binding record shapes are included in `schemas/institutional-deployment-domain-0.1.schema.json`. These schema definitions describe data shape only; tenant ownership, approval evidence, binding lifecycle and adapter cryptographic validation remain application-level controls.


## Receiver-pinned OIDC discovery and JWKS loading

The OIDC adapter now has a companion loader in `protocol-reference/oidc_discovery.py`. It starts from the receiving institution's configured issuer, constructs that issuer's OIDC discovery endpoint, and requires the returned metadata issuer to match the configured issuer exactly. The metadata's `jwks_uri` must use HTTPS and its hostname must be in the receiver's explicit host allowlist. The provider's advertised ID-token signing algorithms must intersect the receiver's asymmetric algorithm allowlist. The loader rejects duplicate/malformed key IDs, private key material and oversized JWKS key sets.

Discovery and JWKS documents are cached for a bounded configurable interval. An unknown `kid` can trigger one forced refresh through the existing adapter contract, but forced refreshes are rate-limited to avoid turning attacker-controlled unknown key IDs into an outbound-request denial-of-service primitive. Returned JWKS objects are deep-copied so a caller cannot mutate the loader's internal cache. An administrator can explicitly invalidate the cache after a trust-policy change. The loader preserves the configured issuer string exactly; issuer URL normalisation must not silently change the identity being trusted.

**The network transport remains an explicit security boundary.** The loader receives an injected `fetch_json` function and does not itself make network requests. A production transport must validate TLS certificates, refuse redirects, apply bounded connect/read timeouts and response sizes, validate JSON content type, and enforce DNS/IP egress controls to prevent private-network or metadata-service access. The institution must pin/allowlist both the issuer host and any separate JWKS host. The loader's hostname allowlist is not a substitute for transport-level DNS/IP controls or a network egress policy. Provider metadata and URLs must never be accepted from an ID token or an untrusted request.

The loader and adapter are separately configurable; deployment wiring must give both the same issuer and compatible algorithm policy. JWT temporal claims are evaluated against the adapter's injected clock (including exp, iat, nbf and auth_time) so clock policy is deterministic and testable rather than split between application and library clocks. Tests use an injected deterministic fake transport and cover exact issuer matching, host allowlisting, HTTPS-only URLs, code-flow discovery metadata, cache expiry/invalidation, forced-refresh throttling, cache isolation, algorithm intersection, malformed/duplicate/private JWKS keys, response bounds and provider transport failure, plus end-to-end adapter/loader integration and simulated signing-key rotation using ephemeral RSA keys. These tests do not represent a live identity-provider connection or validate a production HTTP transport.


## Institutional runtime binding resolution

`protocol-reference/institutional_runtime_binding.py` adds a fail-closed resolver for the runtime admission boundary. Given a tenant, institution and binding ID from trusted session context, it loads the current institution, binding, jurisdiction package and MINAH domain record from the tenant-scoped registry. It requires a verified institution, an active binding, an active jurisdiction package at the exact version pinned by the binding, and an active MINAH domain record owned by the same institution.

The resolver then asks the institutional MINAH deployment administration service for the active deployment. That service independently checks the deployment lifecycle and its pinned identity-provider status/version. The resolver compares the returned runtime configuration against the binding's exact jurisdiction, model/interface, policy, source and authority-profile pins, and rejects tenant or deployment mismatches. A successful resolution returns a detached runtime context and the required order: MINAH → Identity → Authority → CEE → Final Decision.

The resolver is exposed through an authenticated inspection endpoint, `GET /institutional/runtime/{institution_id}/bindings/{binding_id}`. The tenant is derived from the verified institutional session, never from the URL or request body. The HTTP response deliberately omits endpoint and secret-manager references. The resolver returns the institution's exact active binding, jurisdiction reference/package version, MINAH deployment/runtime versions, and policy/source/authority-profile pins. Its fail-closed tests cover tenant isolation, owner mismatch, inactive/unavailable MINAH, stale jurisdiction versions and configuration-pin mismatches.

The first instruction-execution integration is exposed through authenticated `POST /institutional/instructions`. The request supplies an institution ID, binding ID, a digest-bound governed instruction envelope, and matching evidence content. The tenant is taken exclusively from the verified institutional session. Before execution, the service resolves the active runtime binding, checks that the instruction jurisdiction and rule-pack version match that binding, and constructs a safe version-pin record. It then selects a server-configured pipeline through an injected institution-specific pipeline factory. The factory receives the full trusted runtime context (including private adapter configuration) and must return a durable pipeline configured with that institution's approved MINAH, identity and authority adapters. Private endpoint and secret references are not copied into the instruction pin record or HTTP response.

The reference CEE pipeline accepts the institutional pin record only as a validated internal extension and persists it inside the CEE operation request fingerprint before MINAH review. This binds retries and later audit inspection to the institution, binding, jurisdiction package, MINAH deployment/runtime, policy, source and authority-profile versions used for the operation. The factory is deliberately provider-neutral: KIN-SYS does not prescribe a particular AI model, identity provider, or institution's internal adapter implementation. The current reference pipeline still supports only its synthetic rule context and ACTIVATE transition; this route is an integration seam and tested execution path, not proof of a live institution's AI, identity provider, legal authority or external effect.

The factory must provide stable, durable pipeline instances/stores for the lifetime and recovery of operations; constructing a fresh in-memory pipeline for each request is not sufficient. Production use still requires shared durable registries and journals, production session authentication, live institution-controlled MINAH and identity-provider adapters, independently verified authority grants, trusted evidence services, and a reviewed recovery/consistency design.
