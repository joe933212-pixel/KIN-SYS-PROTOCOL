# Cross-Installation Federation Message Contract

**Status:** Reference security boundary, draft v0.1.  
**Copyright:** Joel Kagoro © 2026.  
**Implementation:** `protocol-reference/institutional_federation.py`

## Purpose

This increment establishes the signed, destination-addressed message boundary needed to exchange an instruction between independently operated KIN-SYS installations. It is deliberately transport-neutral: it does not assume that the installations share a database, identity provider, MINAH, jurisdiction package or authority registry.

## Message and verification contract

An origin installation signs a canonical Ed25519 envelope containing:

- protocol version, message ID, operation ID and idempotency key;
- origin installation and institution identifiers;
- exact destination installation and institution identifiers;
- signing key ID, issue/expiry times and a single-use nonce; and
- the complete instruction whose own payload digest is independently verified.

The receiving installation must independently check the destination identifiers, message lifetime, issuer-to-key binding, receiver-local active trust relationship, key and issuer validity windows, Ed25519 signature, instruction digest and operation/idempotency binding. It then atomically consumes the nonce in a receiver-local replay store.

The verifier returns the authenticated origin/destination metadata and the digest-bound instruction. It explicitly returns `authority_inherited: false` and `execution_authorised: false`.

## Non-negotiable boundary

A valid signature establishes message integrity and origin under a key the receiver currently trusts. It does **not**:

- authenticate the human or service principal named inside the instruction;
- establish that the origin institution has legal competence;
- transfer or delegate the origin institution's authority to the receiver;
- approve the transaction or satisfy the receiver's jurisdictional rules; or
- permit bypass of the receiving installation's own MINAH → CEE → Final Decision path.

After successful message verification, the receiver must resolve the instruction against its own tenant, institution, active deployment binding, identity provider, jurisdiction package, policy/source versions and authority grants. It must run the local governed execution service. The receiving MINAH may reject, defer or pass the instruction only within its configured mandate; CEE remains the governed execution boundary and final admission decision remains independent of MINAH.

## Replay storage

`sqlite_nonce_consumer(...)` adapts the local `SQLiteNonceGuard`; it is suitable only for workers sharing one SQLite file. The new `PostgreSQLNonceGuard` in `protocol-reference/postgresql_nonce_guard.py`, introduced by migration `002_federation_nonce_guard.sql`, provides a shared multi-worker replay boundary. Use `postgresql_nonce_consumer(PostgreSQLNonceGuard(connection_factory))` for a federated receiver whose workers share the same PostgreSQL authority. The adapter uses a tenant-scoped unique key derived from issuer installation plus nonce, atomically inserts once, prunes expired entries, and fails closed on database errors. Its maximum expiry window is 330 seconds to accommodate the 300-second message lifetime plus 30 seconds of permitted clock skew.

The callback passed to the verifier must implement atomic insert-if-absent semantics and return `True` only for the first valid consumption. A replay-store outage fails closed. The PostgreSQL integration tests cover independent guard instances, concurrent duplicate consumption, tenant separation, expiry and store failure. This is durable replay protection, not yet a durable federation inbox or exactly-once execution mechanism.

## Current scope and next increment

Implemented here: canonical signed envelope creation, receiver-side cryptographic and routing verification, bounded freshness, receiver-local trust-key checks, replay callback boundary, receiver-side execution orchestration and adversarial unit tests. `protocol-reference/tests/test_cross_installation_runtime_integration.py` additionally connects two isolated installation fixtures through the real `InstitutionalInstructionExecutionService` and reference `KinSysProtocolPipeline`, with separate CEE/IVM stores, MINAH profiles, jurisdiction/policy pins and execution-lease state. It verifies receiver-local execution, jurisdiction rejection and replay rejection.

Still not implemented: network transport, authenticated installation discovery, automated delivery scheduling, and cross-installation operation status. The receiver has an integrated durable inbox acceptance/claim/complete path. Sender-side durable delivery state and receiver-signed acknowledgement are present as reference components. The new `protocol-reference/tests/test_postgresql_two_installation_federation.py` connects two isolated installation configurations through separate PostgreSQL schemas: sender outbox and receiver inbox are persisted independently; the receiver executes through its own institutional execution service, signs the completed decision, and the sender verifies and records the receipt using sender-local receiver trust. It also reconstructs the inbox/receiver adapters over the same durable schema, redelivers the exact envelope, and checks that the stored decision and signed receipt are replayed without a second receiver execution. This is a PostgreSQL-backed cross-installation state integration test, not two separate PostgreSQL server instances: the schemas share the CI PostgreSQL service. The institutional deployment and identity-provider registries, MINAH lifecycle resolver, and CEE/IVM reference journals in this fixture remain synthetic/in-memory; the test does not establish crash recovery after a CEE/IVM commit but before inbox completion, live OIDC authentication, independent deployed hosts, or production readiness. A real transport adapter, authenticated discovery, retry scheduler, dead-letter policy and operation-status API remain integration work.

The next engineering increment is failure-injection recovery across the durable boundary: interrupt receiver processing after its local CEE/IVM commit but before inbox completion, reconstruct the receiver service and store adapters, reclaim the inbox lease, and prove that the original pinned operation is recovered and the same signed acknowledgement is completed without a second execution. Then replace the synthetic installation lifecycle, identity-provider and MINAH resolvers with independently persisted institutional registries and trust configuration on both sides. Keep the two PostgreSQL schemas as test isolation only; validate separate database instances and independently deployed hosts before claiming operational federation. Prove separate decision rights and audit histories; do not claim exactly-once execution across CEE/IVM or the network.


## Receiver-local execution adapter

`protocol-reference/institutional_federated_receiver.py` adds the next boundary: after successful envelope verification, it selects the receiver tenant, institution and binding exclusively from receiver-local configuration and passes the digest-verified instruction and its evidence to the injected local governed execution service. The expected production integration is the existing `InstitutionalInstructionExecutionService`, which resolves the active receiver binding, pins its own identity provider/jurisdiction/MINAH deployment and enforces the local MINAH, identity, authority, CEE and final-decision execution order.

The adapter never uses origin claims to select the receiving institution or binding. It does not translate a verified message into approval; a receiver-side rejection remains a receiver-side rejection. Its unit tests use a recording executor to prove routing and fail-closed behavior. The separate cross-installation runtime integration test now exercises the adapter with the real institutional execution service and reference pipeline on the receiver side, while retaining the limitations described above.

## Next integration test target

The next test increment must instantiate two isolated installation configurations and connect each adapter to its own real institutional execution service, registry, MINAH deployment resolver, identity provider, authority grant store and CEE/IVM persistence. It must prove that a receiver-local jurisdiction or authority rejection does not alter the origin's history, and that an acknowledgement is correlated to the original message/operation without sharing decision authority.


## Durable receiver inbox foundation

Migration `003_federation_inbox.sql` and
`protocol-reference/postgresql_federation_inbox.py` add a PostgreSQL-backed
receiver inbox state machine. After an envelope has been independently
verified, a caller can persist the exact envelope with receiver tenant,
issuer installation, message, operation and idempotency identities. The
stored canonical-envelope digest prevents a repeated message/idempotency
identity from being rebound to different content. Duplicate delivery of the
same envelope returns the existing record.

Workers claim deliveries using database-backed expiring leases. Only the
current lease owner can record a terminal receiver decision or release the
message as retryable/permanently rejected. An expired lease can be reclaimed
after a worker stops making progress. The integration suite covers duplicate
acceptance, content-conflict rejection, concurrent single-worker claims,
lease-owner enforcement, retry and lease-expiry reclaim.

The inbox is now wired into `InstitutionalFederatedInstructionReceiver.receive()`
through the shared PostgreSQL acceptance path. After authentication, the
receiver claims a database lease, invokes its own governed execution service,
and persists the resulting receiver decision reference and response before
returning a completed acknowledgement. Duplicate completed deliveries return
the stored response without re-executing. Retryable failures and expired
processing leases can be reclaimed; the receiver execution service's pinned
CEE journal and IVM idempotency/recovery checks remain responsible for
reconciling an interrupted operation.

A process crash after CEE/IVM commits but before inbox completion still creates
a recovery interval. The current architecture is designed to reconcile that
interval through the persisted operation journal and IVM event checks, but
the full crash/restart path across separate durable stores still requires
dedicated integration tests. Exactly-once execution across CEE/IVM remains
unproven and must not be claimed.


### Atomic authenticated acceptance

The PostgreSQL inbox now exposes `accept_authenticated(...)`, and
`institutional_federation.postgresql_inbox_acceptor(...)` adapts it to the
federation verifier's post-signature acceptance hook. When configured on
`InstitutionalFederatedInstructionReceiver`, signature, trust, destination,
freshness and payload checks happen first; then the receiver commits the
issuer-scoped replay nonce and durable inbox row in one PostgreSQL transaction.
If nonce insertion fails as a replay, the inbox insertion rolls back. If the
same already-persisted message is redelivered with the same canonical envelope,
the existing inbox record is returned idempotently.

The receiver now claims the durable inbox record before local execution.
A completed duplicate returns the stored response; an active lease yields a
pending acknowledgement; a retryable failure or expired lease may be reclaimed.
After the receiver's local MINAH/CEE/IVM/final-decision service returns a bound
decision, the receiver records that response and its stable CEE execution
reference under the current lease before returning completion. Execution
exceptions release the lease into retryable state where possible. The legacy
nonce-only callback remains available for non-durable compatibility paths and
does not provide the atomic inbox guarantee.

The inbox lease does not itself make the CEE/IVM stores transactional with
PostgreSQL. On a reclaimed delivery, the receiver now asks the institutional
execution service to reconcile the operation through its original pinned CEE
journal before it attempts a new execution. The recovery request is bound to
the complete digest-verified instruction; reusing an operation ID with
different instruction content is rejected. If no journaled operation exists,
the receiver may proceed through the normal MINAH → CEE → Final Decision
execution path. If an operation exists, the service performs its existing
cross-store integrity checks and recovery procedure under the original
institutional runtime pins and active deployment lifecycle.

If the original journal cannot be located after a process restart, the
receiver fails closed and leaves the inbox retryable; it must not interpret an
unavailable recovery store as proof that execution never happened. A fresh
execution is allowed only when the configured operation resolver (or the
known local journal) authoritatively reports that no operation exists.

This closes the previously missing application-level recovery handoff, but
does not make the CEE/IVM stores transactional with PostgreSQL. The remaining
validation gate is a PostgreSQL-backed process restart/failure-injection test
at the exact interval after local IVM/CEE commit and before inbox completion,
followed by lease reclaim and proof that the receiver returns the recovered
decision without a second execution. Exactly-once delivery across installations
is not claimed; the design remains at-least-once delivery with receiver-side
idempotency and durable result recovery.


## Sender-side durable outbox and signed acknowledgement

The reference implementation now includes `protocol-reference/institutional_federation_acknowledgement.py`, `protocol-reference/postgresql_federation_outbox.py`, and migration `004_federation_outbox.sql`.

The sender persists the exact signed instruction envelope before attempting transport. The outbox binds the sender tenant and installation, message ID, operation ID, idempotency key, receiver installation and receiver institution to a canonical envelope digest. Re-enqueueing identical content is idempotent; reuse of a message or idempotency identity with different content is rejected. Workers claim deliveries under expiring PostgreSQL leases, and transport failures release the lease into a retryable state. Multiple workers share the same database authority and cannot claim the same active delivery concurrently.

A receiver acknowledgement receipt binds the original envelope digest and stable receiver response to the exact message, operation, idempotency key, sender installation/institution and receiver installation/institution. The receiver signs the receipt with Ed25519. The sender verifies the signature against its own receiver trust configuration before the outbox records the acknowledgement. The outbox permits a valid late acknowledgement to complete a message even after the original sender worker has crashed and its lease has expired. Repeating the same verified receipt is idempotent; a different receipt for an already acknowledged message is rejected as a conflict.

Volatile delivery metadata (for example, whether the response came from a duplicate delivery) is deliberately excluded from the signed stable response. The decision, runtime pins and result remain bound. The receipt authenticates the receiver's attestation under a sender-trusted key; it does not prove legal competence, evidence sufficiency or external settlement.

The receiver adapter can now sign the completed stable response when configured with a receiver-owned signing key ID and key-custody loader. The receipt is included in the response persisted by the durable inbox, so duplicate delivery returns the same signed receipt while volatile duplicate-delivery metadata may change. The sender outbox accepts a receipt only through its injected verifier, which must use sender-local trust configuration and verify against the exact stored original envelope. This remains a protocol/state-machine integration, not a complete transport service: no outbound HTTP/mTLS transport, authenticated discovery, retry scheduler, dead-letter policy or operation-status API is included. The PostgreSQL integration tests exercise sender persistence, worker claims, retry, late acknowledgement after lease expiry, duplicate receipt idempotency and conflicting/tampered receipt rejection. The tests do not establish live two-host federation. Delivery remains at-least-once; no exactly-once network guarantee is claimed.


## PostgreSQL institutional trust and authority registry

Migration \`005_institutional_trust_registry.sql\` and
\`protocol-reference/postgresql_institutional_trust_registry.py\` add a
PostgreSQL reference backend for immutable authority-grant versions, issuer
trust roots, Ed25519 key references, grant provenance and receiver-signed
acceptance receipts. The adapter implements the same
\`VerifiedAuthorityGrantStore\` contract as the SQLite reference registry and
can be passed directly to \`InstitutionalAuthorityResolver\`.

The database uses tenant-composite keys, expected-version head locking,
transactional grant/audit writes, append-only snapshot/event/provenance
triggers, one-way issuer/key revocation triggers and a deferred check that
every current grant head has an audit event and advances from the prior head.
It stores and verifies a canonical snapshot digest as well as the issuer
envelope, receiver acceptance receipt and matching audit evidence. Public and
private key material is not stored in these tables; public-key and
receiver-acceptance resolvers remain separate trust adapters.

The PostgreSQL integration tests exercise real database transactions when
\`KIN_SYS_POSTGRES_TEST_DSN\` is configured (CI provisions PostgreSQL 16).
They cover local resolver integration, tenant isolation, immutable revisions,
revocation, scope and expiry, overlapping key rotation, receiver acceptance
receipts and issuer-trust revocation. This remains reference persistence:
production still requires authenticated tenant derivation, least-privilege
roles and row-level isolation, external key custody/HSM, a governed receiver
approval workflow, backup/failover and migration operations, legal authority
adjudication and independent security review.

The PostgreSQL registry introduced by migration \`005\` stores issuer-institution
trust used for grant provenance and federated actor assertions. It does not
yet store the receiver-installation trust roots used by the sender outbox
acknowledgement verifier. That remains a separate sender-local trust-store
integration. The outbox's injected verifier is still an explicit trust
boundary and must be wired to \`verify_receiver_acknowledgement(...)\` plus a
separately governed receiver-key resolver; a callback that simply returns
\`verified: true\` is not sufficient.


The current PostgreSQL trust-store increment accepts and persists an initial
issuer-signed grant with a receiver-signed acceptance receipt. A dedicated
transaction for accepting a subsequent issuer-signed grant revision is still
outstanding. Until that exists, a locally created unsigned revision has no
fresh provenance/receipt and must fail closed under federated resolution; the
previous version's signature and approval are never inherited.

## Failure-injection recovery validation

`protocol-reference/tests/test_postgresql_federation_commit_recovery.py` now injects a failure at the precise boundary after the receiver's local CEE/IVM operation has committed and its signed acknowledgement has been constructed, but before PostgreSQL inbox completion. The test expires the still-active inbox lease, reconstructs the receiver adapter over the same receiver inbox schema, redelivers the identical signed envelope, and requires recovery through the original pinned operation. It checks that the inbox reaches `COMPLETED`, the deterministic signed acknowledgement is unchanged, and the CEE journal contains exactly one `REQUESTED` event for the operation.

The stable decision projection excludes retry metadata (including the reference executor's `duplicate` indicator). That flag describes how an attempt reached the decision, not the decision itself; including it would change the signed response digest on recovery even when the original committed outcome and runtime pins are unchanged.

The test deliberately retains the same reference execution-service/journal object while reconstructing the receiver adapter. It therefore validates the failure handoff, lease reclaim and recovery orchestration against the existing journal; it does **not** yet prove restoration of CEE/IVM journal state into a separate process or independent database server. That remains a separate durability gate and must be tested before claiming crash-safe multi-host recovery.


## Authenticated HTTP ingress boundary (reference adapter)

`protocol-reference/federation_http_transport.py` adds a narrow WSGI ingress at
`POST /federation/v1/messages`. It is deliberately separate from the local
synthetic prototype API. The adapter requires a bounded JSON body and a
configured `peer_identity_resolver`; it compares the authenticated transport
peer installation ID with the issuer installation ID in the signed envelope
before invoking `InstitutionalFederatedInstructionReceiver.receive(...)`.
The receiver still independently verifies the Ed25519 envelope, receiver-local
trust, destination, freshness, replay state and local execution authority.

The adapter rejects missing/invalid content lengths, oversized or truncated
bodies, non-JSON content types, duplicate JSON members, non-finite JSON values,
wrong routes/methods, missing peer identity and transport/envelope issuer
mismatches. Receiver exceptions are not exposed to remote callers. The response
is 200 for a completed delivery and 202 while the durable receiver reports a
non-completed state. The adapter does not accept tenant, institution, binding
or authority selectors from the HTTP body.

**Deployment trust requirement:** this WSGI module does not terminate TLS or
validate certificates itself. The deployment must terminate mutually
authenticated TLS at a trusted edge and configure the peer resolver to read
only certificate identity metadata inserted by that edge after successful
verification. The edge must strip caller-supplied copies of those metadata
headers, prevent direct bypass to the WSGI process, and enforce its own
certificate-chain, revocation, hostname/SAN, rate, connection and request
limits. A resolver that trusts ordinary caller-controlled HTTP headers is
unsafe and invalidates the transport-authentication boundary. The adapter's
peer identity check is an additional binding; it does not replace the
receiver's signed-message trust verification or confer execution authority.

Adversarial unit tests cover authenticated delivery, peer/issuer mismatch,
missing peer identity, caller-header spoofing, request size, route/content-type,
duplicate JSON keys, truncated bodies and error-detail suppression. These are
deterministic adapter tests, not a live mTLS or two-host deployment test.

The new `protocol-reference/federation_mtls_identity.py` supplies a concrete
peer resolver for a trusted TLS terminator. It reads only the server-controlled
`SSL_CLIENT_CERT` WSGI environ value and extracts exactly one installation
identity from a `spiffe://kin-sys/installations/{installation_id}` URI SAN.
It rejects missing, malformed, oversized or ambiguous certificates and never
falls back to caller-controlled `HTTP_*` headers. The TLS terminator remains
responsible for client-certificate chain, validity, EKU and revocation checks;
it must require a client certificate, strip any caller-supplied certificate
headers, inject the verified certificate into `SSL_CLIENT_CERT`, and prevent
direct access to the WSGI application. `mtls_peer_identity_resolver` can be
passed directly to `FederationHttpIngressApplication`. Tests cover the URI
identity contract and header-spoofing rejection; they do not yet exercise a
live TLS handshake.

A live mTLS socket integration test now runs the sender's
`FederationHTTPSJSONTransport` against a separately listening receiver WSGI
service. It creates a test CA, a receiver server certificate and a sender
client certificate carrying the installation SPIFFE URI. The receiver TLS
socket requires and validates the client certificate; the sender validates
the receiver chain and hostname; the trusted WSGI adapter receives the peer
certificate from the TLS socket and binds it to the signed-envelope issuer.
The test also checks issuer mismatch rejection and refusal of clients without
a certificate. DNS resolution and the final socket route are injected only to
keep the test deterministic on loopback; the TLS handshake and HTTP exchange
are real sockets, not mocked connections.

This test validates the live TLS/mTLS transport boundary, but its receiver
service runs in a separate server thread in the test process and uses an
in-memory test receiver. It does not yet establish independent operating
system processes, separate PostgreSQL databases, independent host/network
deployment, real institutional key custody or the complete MINAH → CEE →
Final Decision execution path. Those remain the next integration gates.

Still outstanding: authenticated installation discovery and a two-process,
two-database federation test using separately configured sender and receiver
installations, including receiver-local governance and durable acknowledgement
recovery.


## Sender-side HTTPS transport and outbox delivery worker

`protocol-reference/federation_https_transport.py` adds the sender-side
counterpart to the HTTP ingress adapter. `FederationHTTPSJSONTransport`
resolves a destination only from the sender institution's configured
installation-to-endpoint registry; it does not accept a URL from the
instruction or envelope. It requires HTTPS, verifies the TLS certificate chain
and hostname, validates DNS answers as globally routable, pins each connection
to a validated address, rejects redirects and bounds request/response sizes
and timeouts. An institution may supply an SSL context configured with its
client certificate for mutual TLS; deployment-specific certificate lifecycle
and peer identity mapping remain explicit configuration responsibilities.

`FederationOutboxDeliveryWorker` accepts a message already claimed from the
PostgreSQL outbox, sends its immutable signed envelope, and passes a completed
receiver acknowledgement receipt to the outbox's injected verifier and
acknowledgement persistence method. It never treats an unsigned HTTP response
as proof of a decision. Pending responses, transport failures and invalid
receipts are returned to retryable state with bounded error codes. The
PostgreSQL outbox remains responsible for cryptographic verification against
the original envelope and sender-local receiver trust configuration.

The new unit tests cover registry-only destination selection, HTTPS/path
restrictions, rejection of private/loopback DNS answers, redirect/status
rejection, acknowledgement handoff to the outbox verifier and retry when a
receipt is absent. They use deterministic fake connections; they are not a
live network, certificate, mTLS or independent-host integration test.

Outstanding deployment validation includes a concrete institutional endpoint
registry adapter, mTLS client-certificate provisioning and rotation, trusted
receiver certificate identity mapping, rate limiting and operational retry
scheduling/dead-letter policy. The sender worker is a one-claim delivery
primitive, not a continuously running scheduler.

## Sender outbox batch dispatch

The PostgreSQL sender outbox exposes `claim_batch(...)` for bounded concurrent
dispatch. It leases at most 100 pending, retryable, or expired-delivery rows in
one short database transaction using `FOR UPDATE SKIP LOCKED`. Network calls
are made only after the claim transaction has committed. Each claim carries its
sender tenant/installation, message and receiver installation identifiers,
original signed envelope, attempt count and lease owner.

`FederationOutboxDispatcher.run_once()` performs one bounded poll and returns
per-message delivery outcomes. It does not create threads, sleep, or run an
unbounded loop. An institution may invoke it from its own supervised worker or
scheduled job, with deployment-controlled poll cadence and concurrency. All
dispatchers for one sender installation must share that installation's
PostgreSQL outbox so row leases coordinate their work.

The delivery worker persists completion only through the outbox's
receiver-acknowledgement verifier. A transport response or HTTP status alone is
not completion. Failed or incomplete deliveries release the active lease as
retryable when the lease owner still holds it. A worker crash leaves the lease
to expire for later reclamation.

Migration `009_federation_outbox_retry_policy.sql` adds a durable
`next_attempt_at`, a per-message bounded `max_attempts` budget (default 8),
and an auditable `DEAD_LETTER` terminal state. Retry scheduling uses a
database-clock exponential delay beginning at 5 seconds and capped at one
hour. A retryable failure is not claimable until its due time. A failure on
the final permitted attempt moves the row to `DEAD_LETTER`; an expired lease
on the final attempt is also dead-lettered rather than reclaimed indefinitely.
The attempt counter advances when a lease is claimed, so process crashes count
against the same finite delivery budget. Dead-letter rows are retained for
operator investigation and are not automatically redriven.

This remains a bounded dispatcher, not an always-on process supervisor.
Lease renewal for long-running requests, operator-authorised dead-letter
redrive, and independent-host mTLS delivery tests remain outstanding.
