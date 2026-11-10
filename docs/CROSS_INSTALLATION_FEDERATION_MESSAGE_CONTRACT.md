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

Still not implemented: network transport, authenticated installation discovery, outbound delivery/retry queues, acknowledgement/outbox protocol, cross-installation operation status, and cross-store recovery. A first durable inbox persistence primitive is now present (migration `003_federation_inbox.sql` and `protocol-reference/postgresql_federation_inbox.py`), but it is not yet integrated into the receiver execution path. The integration fixture uses in-memory stores, local test lifecycle resolvers, generated Ed25519 keys, and the reference pipeline's synthetic MINAH/identity/authority adapters. It does not yet exercise the full persistent institutional registry and identity-provider registry on both sides, live OIDC authentication, independent deployed hosts, or production infrastructure. It is an integration milestone, not evidence of live federation or production readiness.

The next engineering increment should replace the lightweight runtime fixture with two fully seeded persistent institutional registries, identity-provider registries, receiver-local trust stores and independently configured MINAH deployment resolvers. Then add a durable federation inbox/acknowledgement boundary with idempotent message acceptance, persisted receiver decision references and restart/failure recovery. Prove that both installations retain separate decision rights and audit histories; do not claim exactly-once execution across CEE/IVM until its cross-store recovery protocol is independently implemented and tested.


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

This is deliberately a persistence primitive, not yet a complete end-to-end
receiver workflow. It is not currently wired into
`InstitutionalFederatedInstructionReceiver.receive()`. In particular, the
current verifier consumes the replay nonce before the caller persists the
inbox row, so a crash in that interval can leave a consumed nonce without a
durable inbox record. A further integration step must unify authenticated
message acceptance and replay registration in one database transaction, then
connect claim/execute/decision recording and define acknowledgement delivery.
A crash after CEE/IVM commits but before inbox completion can also cause a
reclaimed worker to retry execution; the current lease alone does not prevent
that. Exactly-once execution across CEE/IVM remains unproven and must not be
claimed.


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

The receiver refuses to invoke local execution again when the atomic acceptance
callback reports an existing record. This closes the earlier crash interval
between nonce consumption and inbox persistence for deployments using this
shared PostgreSQL acceptance path. The legacy nonce-only callback remains for
compatibility and does not provide this atomic inbox guarantee.

This does not close the separate crash interval after CEE/IVM execution commits
but before the inbox records its decision. Durable execution-result
reconciliation or a transactionally coupled execution receipt is still needed
before retry-safe exactly-once semantics can be claimed.
