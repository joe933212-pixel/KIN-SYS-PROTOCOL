# PostgreSQL institutional MINAH coordination schema

Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.

`001_institutional_minah_coordination.sql` establishes the durable relational schema required for a shared institutional MINAH deployment authority:

- immutable deployment-version records and a tenant-scoped current head;
- a lifecycle epoch that invalidates leases when deployment state or configuration changes;
- a durable per-tenant/per-deployment fencing sequence;
- lease capabilities represented by verifier digests, never plaintext tokens;
- append-only deployment events with tenant-scoped event identity.

## Current implementation status

The repository now includes `postgresql_minah_deployment_registry.py`,
which implements tenant-scoped registration, immutable version revision,
lifecycle transitions, current-deployment reads and audit-event reads against
this schema. Lifecycle mutations lock the deployment head and update the
current version, lifecycle epoch and audit event in one PostgreSQL transaction.
The first-registration path also uses a transaction-scoped advisory lock to
serialize concurrent creation attempts for the same tenant/deployment.

The PostgreSQL execution coordinator and lifecycle registry share the same
head and epoch rows. A lifecycle transition therefore invalidates leases issued
under the prior epoch. The coordinator's guarded callback holds those rows
locked until callback completion. Connection ownership is explicit: each
operation closes the fresh connection supplied by its connection factory.
Both adapters require the factory to return a fresh, idle connection with no
transaction already open. If session setup such as `SET search_path` is run
before returning the connection, that setup must be committed first.


The repository now includes a PostgreSQL execution lease adapter at
protocol-reference/postgresql_execution_coordinator.py. It implements shared
lease acquisition, authoritative validation, guarded callback execution, and
idempotent release against these tables. It accepts a connection factory and
expects psycopg 3 connections. The PostgreSQL lifecycle registry now supplies durable registration, revision,
and lifecycle transitions, while the separate PostgreSQL institutional domain
registry supplies durable institution, jurisdiction, MINAH and binding
onboarding records. Authority-gated administration remains an application
facade over trusted actor and evidence resolvers. Connection-pool operations
and production deployment controls remain separate work.

The PostgreSQL integration tests exercise independent coordinator instances
against the same real database, persistent monotonic fencing, epoch invalidation,
guarded callbacks, and release. The CI PostgreSQL service is a test dependency,
not a production database deployment.

## Required transaction protocol for lifecycle writers

The PostgreSQL implementation must treat the deployment head, current version, lifecycle epoch, and fencing sequence as one authoritative coordination boundary.

1. Lease acquisition must begin a database transaction and lock the deployment head (or a dedicated per-deployment coordination row) with `SELECT ... FOR UPDATE`.
2. While holding that lock, it must read the current version/status/epoch, reject non-`ACTIVE` deployments, increment the fencing sequence, and insert the lease. The sequence increment and lease insert must commit together.
3. Lifecycle revision, suspension, reactivation, and revocation must take the same lock and update the version head/status and epoch in the same transaction. A lease must never be issued against a lifecycle state that is concurrently being invalidated.
4. Lease validation must compare the capability digest, expiry, release state, current lifecycle status, epoch, and persisted fencing token. Caller-provided claims are not authoritative.
5. The current adapter holds locks on the deployment head, epoch, and lease rows while its guarded callback runs. A lifecycle writer must lock the same head and epoch rows and increment the epoch in the same transaction as its version/status update. The adapter cannot enforce the behavior of an external lifecycle writer.
6. A callback that writes to another database is not made atomic by this mechanism. The adapter serializes the callback against lifecycle writers at the PostgreSQL authority, but it does not create a distributed transaction with IVM or CEE.
7. Independent workers must share the same PostgreSQL authority. Per-node databases, asynchronous replicas, or a Python protocol-interface check do not establish this property.

The current adapter is an initial executable increment, not a complete conformance claim. Remaining tests include true concurrent acquisition races, lifecycle-versus-acquisition races, expiry, tenant/deployment isolation, and rollback after injected failures. A production rollout additionally requires review of PostgreSQL roles, TLS, pool sizing, timeouts, observability, backup/restore, failover semantics, and operational recovery.

The schema deliberately does not claim atomicity with the separate IVM store or CEE journal. That requires a separately designed durable event/outbox and recovery protocol, tested against actual crash windows.
## Applying migrations

The repository includes `protocol-reference/postgresql_migrations.py`. It applies ordered SQL migrations in one database transaction while holding a PostgreSQL transaction-scoped advisory lock. It records each migration's SHA-256 checksum and refuses to continue if an already-applied migration file has changed. Migration files must not issue `BEGIN`, `COMMIT`, or `ROLLBACK`; transaction ownership belongs to the runner.

Install the optional driver with `pip install 'psycopg[binary]>=3.2,<4'`, set `KIN_SYS_POSTGRES_DSN` to the target database connection string, then run `python protocol-reference/postgresql_migrations.py`. The caller is responsible for supplying a least-privilege migration identity, network controls, backups, and an appropriate deployment/change-management process. The runner does not create the PostgreSQL server or configure its security posture.

The conformance workflow now provisions a disposable PostgreSQL 16 service and runs the migration integration suite using the optional dependency set in `protocol-reference/requirements-postgresql.txt`. The integration tests apply the repository migration against a real PostgreSQL server, verify repeat-run idempotency and checksum enforcement, and verify that a later SQL failure rolls back both earlier DDL and migration-history writes. Outside CI, these tests are skipped unless `KIN_SYS_POSTGRES_TEST_DSN` is set. The service is test infrastructure only; it is not a production database deployment.


## Federation replay protection

Migration `002_federation_nonce_guard.sql` adds the shared
`consumed_federation_nonces` table with a tenant-scoped unique nonce key and
an expiry index. `postgresql_nonce_guard.py` implements atomic
insert-if-absent consumption using a fresh psycopg 3 connection per call.
Expired nonce rows are pruned in the same transaction as consumption, and a
database error fails closed. The adapter rejects expired assertions and
assertion lifetimes longer than its configured maximum (330 seconds by default: 300-second message lifetime plus 30-second clock skew).

For a federated receiver, pass
`postgresql_nonce_consumer(PostgreSQLNonceGuard(connection_factory))` as
`consume_nonce`. The adapter hashes the issuer-installation/nonce pair into
the tenant-scoped key, so the same nonce from two different issuer
installations does not collide. Every receiver worker must use the same
PostgreSQL authority; local process locks or per-node databases do not provide
cross-worker replay protection.

The PostgreSQL integration suite verifies independent guard instances,
tenant isolation, concurrent single-consumer behavior, expiry/pruning, and
fail-closed database errors. This is a durable shared replay-store increment. It does not make nonce consumption,
the federation inbox/outbox and the separate CEE/IVM journals one distributed
transaction, and it does not provide exactly-once network execution.


## Durable federation inbox foundation

Migration `003_federation_inbox.sql` adds a receiver-tenant-scoped durable inbox
with unique issuer/message and issuer/idempotency identities, a digest binding
to the stored signed envelope, processing leases, attempt counts, terminal
receiver-decision references, and retry/rejection state.

`postgresql_federation_inbox.py` provides the corresponding PostgreSQL state
machine. A caller first verifies the signed envelope, then calls
`accept_verified(...)` to persist it. Workers use `claim(...)` to acquire an
expiring lease; only the current unexpired lease owner can call `complete(...)`
or `fail(...)`. Expired leases can be reclaimed, and duplicate delivery of
the same envelope is idempotent. Reuse of a message or idempotency identity
with different content is rejected.

The receiver now integrates this state machine through its atomic
authenticated acceptance path. It claims a lease before local governed
execution, persists the receiver decision reference and response on
completion, returns stored acknowledgements for completed duplicate
deliveries, and makes retryable failures or expired leases claimable again.
The receiver uses its existing institutional execution service, whose pinned
CEE journal and IVM event checks provide operation-level replay and recovery
semantics.

The inbox transaction remains separate from the CEE/IVM stores. The
PostgreSQL inbox integration suite now exercises a receiver process restart
after the IVM commit but before inbox completion, reclaims the expired lease,
restores the CEE/IVM adapters from their durable SQLite files, and verifies
that the original IVM event is not executed twice. This validates that
specific reference configuration only; it does not claim a distributed
transaction or exactly-once network execution. The PostgreSQL integration
tests are skipped outside CI unless `KIN_SYS_POSTGRES_TEST_DSN` is configured.


## Durable sender outbox and receiver acknowledgements

Migration \`004_federation_outbox.sql\` and
\`protocol-reference/postgresql_federation_outbox.py\` add a sender-side
PostgreSQL outbox. A signed envelope is persisted before transport, retries
are idempotent, workers claim expiring delivery leases, and a late receiver
acknowledgement can complete the row after a sender worker has lost its lease.
The outbox provides durable at-least-once delivery attempts, not exactly-once
network delivery or cross-database execution.

Receiver acknowledgements use the Ed25519 receipt contract in
\`institutional_federation_acknowledgement.py\`. The sender must verify the
receipt against its own receiver-installation trust configuration and the
exact immutable outbox envelope. The current outbox adapter receives that
verification operation as an injected verifier; application wiring must bind
it to \`verify_receiver_acknowledgement(...)\` and sender-local trust/key
resolvers. A callback that merely returns a caller-constructed
\`verified: true\` object is not a cryptographic trust boundary and must never
be used.

## Federation delivery tenant isolation

Migration `011_federation_delivery_rls.sql` enables and forces PostgreSQL
row-level security on both `federation_inbox` and `federation_outbox`.
Receiver inbox rows are scoped by `receiver_tenant_id`; sender outbox rows
are scoped by `sender_tenant_id`. Both policies fail closed when
`kin_sys.tenant_id` is unset or empty, and enforce the same boundary for
reads and writes.

The inbox and outbox adapters set the tenant context transaction-locally for
every database operation. The tenant value must still come from the
authenticated institutional execution context, not from an untrusted message
or caller-supplied header. This shared-database GUC design is defense in depth:
a database principal permitted to issue arbitrary SQL can set a custom GUC.
Production deployments must use least-privilege runtime roles, controlled
connection access, and independent tenant-context derivation. The PostgreSQL
nonce table, CEE journal, IVM transition store and institutional domain
registry are separate persistence boundaries and are not covered by this
migration.

## PostgreSQL authority and issuer-trust registry

Migration \`005_institutional_trust_registry.sql\` and
\`protocol-reference/postgresql_institutional_trust_registry.py\` add a
PostgreSQL backend for tenant-scoped authority grants, immutable versions,
current-version heads, append-only grant events, retained issuer envelopes,
receiver acceptance receipts, issuer trust relationships and Ed25519 public-key
references. Grant revisions and revocation lock the current head and advance
the pointer with the audit event in one transaction. Grant snapshots,
provenance and event rows have database triggers that reject update/delete;
trust relationships and keys permit only one-way ACTIVE-to-REVOKED changes.
A deferred constraint trigger requires every current grant head to have an
audit event for its selected version and to advance from the immediately
previous head. Issuer/key registration and revocation also require matching
append-only audit events.

The adapter implements \`VerifiedAuthorityGrantStore.load_verified_grant\`.
It pins the current head during verification, rechecks the issuer signature
against the receiver tenant's active trust relationship/key, checks the
canonical grant digest and receiver-signed acceptance receipt, and verifies
the corresponding audit event. The resolver can therefore use the PostgreSQL
store directly without an arbitrary grant callback.

This migration stores issuer-institution trust used for authority grants and
federated actor assertions. It does not yet persist the receiver-installation
trust roots used by the sender outbox acknowledgement verifier; that remains
a separate sender-local trust-store integration.

This is a PostgreSQL reference backend, not a production certification.
Tenant IDs remain caller-supplied and must be derived from authenticated
principals. Receiver approval, receiver acceptance-key trust, public-key
retrieval and private-key custody remain explicit external trust boundaries.

Migration `010_institutional_trust_registry_rls.sql` enables and forces
PostgreSQL row-level security on all eight trust-registry tables. Policies
filter reads and constrain writes using the transaction-local
`kin_sys.tenant_id` setting. The PostgreSQL adapter sets that value with
`set_config(..., true)` inside each operation's transaction, so an unset
setting fails closed and the value does not leak when a connection is reused.
The integration suite exercises these policies under a dedicated NOLOGIN role
that is neither the table owner nor a BYPASSRLS role, including unfiltered
reads, cross-tenant updates and transaction-context reset.

This RLS layer is defense in depth, not a substitute for authenticated tenant
selection: a database principal able to issue arbitrary SQL can set a custom
GUC itself. The service must derive tenant IDs from authenticated principals,
use a narrowly privileged runtime role that is not a superuser and does not
have BYPASSRLS, and prevent untrusted callers from obtaining raw database
credentials or arbitrary SQL execution. Database owners/superusers can still
alter policies or assume privileged roles. Production still requires least-
privilege role provisioning, TLS, backup/restore, failover, migration
governance, HSM/secret-manager integration, independent security review and
operational revocation controls.


**Current revision limitation:** the PostgreSQL adapter's signed acceptance
method currently handles initial grant creation. A federated grant revision
must be a new issuer-signed version with a fresh receiver acceptance, but a
dedicated \`accept_signed_grant_revision\` operation is not yet implemented.
A local/unsigned revision remains untrusted for federated resolution and fails
closed because it has no matching signed provenance and receiver receipt.


## Durable CEE lifecycle journal

Migration `006_cee_journal.sql` and
`protocol-reference/postgresql_cee_journal.py` add a shared PostgreSQL
implementation of the CEE journal contract. Operation creation is idempotent
by operation ID and canonical request digest. Lifecycle phase changes and
their corresponding append-only event are committed in the same database
transaction under a row lock. Recovery observations are separately appended;
the event and observation tables reject ordinary UPDATE and DELETE operations.

`KinSysProtocolPipeline` accepts an injected `cee_journal`, allowing an
institutional pipeline factory to supply `PostgreSQLCEEJournal` while keeping
the SQLite journal as the offline reference default. The PostgreSQL adapter
opens a fresh connection for each operation, so independently constructed
workers read and advance the same durable journal without sharing Python
objects or process-local caches.

The CEE journal and IVM transition store are still separate persistence
boundaries. This adapter does not create a distributed transaction or make
the IVM write atomic with the CEE journal update. The next integration step
must pair the PostgreSQL CEE journal with an independently durable IVM store
and exercise crash windows, conflicting retries, and recovery across separate
database instances. PostgreSQL is a reference backend, not production
certification; tenant identity, database roles, row-level isolation, backups,
TLS, failover, key custody and operational controls require independent review.


## Durable PostgreSQL IVM transition store

Migration `007_ivm_transition_store.sql` and
`protocol-reference/postgresql_protocol_store.py` add a shared PostgreSQL
implementation of the IVM transition-store contract. It persists process
state, event identity and digest, transition history, and execution-fence
watermarks. A successful transition writes its event, state change, history,
and optional fencing watermark in one PostgreSQL transaction. Process rows
are locked for state transitions; event identities and tenant/deployment
fence scopes use transaction advisory locks so independently constructed
workers coordinate through the same database authority.

Identical event retries return the original decision without a second state
transition. Reusing an event ID with different content or a different process
fails closed. A stale fencing token or older execution epoch is rejected.
Transition history is append-only at the database trigger boundary. The
adapter exposes an integrity check that verifies accepted events have one
history row, rejected events have none, and process state versions agree with
the number of committed transitions.

`KinSysProtocolPipeline` accepts an injected `ivm_store`, allowing an
institutional runtime to pair `PostgreSQLCEEJournal` and
`PostgreSQLProtocolStore` while preserving SQLite defaults for offline
demonstrations. These are separate persistence systems: the two adapters do
not create a distributed transaction. The current integration tests exercise
atomicity within the IVM PostgreSQL transaction, concurrent independent
workers, retry identity, fencing, adapter replacement, and the pipeline's
integrity gate. They do not establish atomic commit across CEE and IVM, nor
do they validate separate physical database servers or production
deployment hardening.


## Durable institutional onboarding and domain registry

Migration `008_institutional_domain_registry.sql` adds PostgreSQL persistence
for tenant-scoped institution, jurisdiction, MINAH deployment and institutional
binding records. Immutable version snapshots and their lifecycle evidence are
stored in `institutional_records` and `institutional_events`. Database
triggers reject ordinary UPDATE and DELETE operations on both tables. A
monotonic database sequence gives each snapshot and event a stable insertion
order, so current-record resolution does not depend on wall-clock timestamp
precision.

Migration `012_institutional_domain_registry_rls.sql` enables and forces
row-level security on both domain-registry tables. Their policies scope reads
and writes to the transaction-local `kin_sys.tenant_id` setting and fail
closed when it is absent or empty. The PostgreSQL adapter establishes that
setting inside every operation's transaction and closes the connection after
the transaction completes. The caller must derive the tenant from an
authenticated institutional principal; caller-supplied tenant IDs are not an
authentication mechanism. As with the other custom-GUC policies, this is
defense in depth: production roles must be least-privilege, non-superuser and
must not have `BYPASSRLS`; arbitrary SQL under a role that can set the custom
GUC can select another tenant.

`postgresql_institutional_registry.py` implements the same storage contract
as the SQLite reference `InstitutionalRegistry`. Snapshot creation and
registration evidence commit in one transaction; lifecycle snapshot creation
and its event commit together; tenant-scoped event IDs are idempotent and
content-bound. Transaction-scoped advisory locks serialize competing
lifecycle changes for the same tenant/resource and concurrent reuse of an
event identity. A stale expected version is rejected after the lock is
acquired, rather than allowing two concurrent writers to create competing
current snapshots.

`InstitutionalOnboardingAdministration` now accepts either registry through
the required storage-method contract. The authentication, authority-resolution
and evidence-verification boundaries remain unchanged: the registry adapter
does not authenticate actors, derive tenant identity, or authorise lifecycle
transitions. The application must derive tenant IDs from verified principals
and supply trusted authority and evidence verifiers.

The PostgreSQL integration suite exercises persistence across independently
constructed adapters, tenant isolation, event idempotency and conflict,
concurrent stale-transition serialization, atomic rollback when audit-event
insertion fails, database-enforced append-only records/events, and the
onboarding administration facade using the PostgreSQL backend. This remains
a reference backend, not production certification. Least-privilege database
roles, row-level security or equivalent tenant enforcement, TLS, connection
pooling, backups, failover, observability, secret/key custody and independent
security review remain deployment requirements.


## Sender outbox retry policy

Migration `009_federation_outbox_retry_policy.sql` extends the durable
federation sender outbox with a database-clock retry schedule, a per-message
attempt budget (default eight), and a terminal `DEAD_LETTER` state. Retry
delays use exponential backoff starting at five seconds and capped at one
hour. Retryable rows are not claimable before `next_attempt_at`. Failures on
the final permitted attempt and expired final-attempt leases are dead-lettered
and retained for operator investigation. Dead-letter rows are not
automatically redriven; any future redrive capability must be an explicit,
authorised, audited operator action.
