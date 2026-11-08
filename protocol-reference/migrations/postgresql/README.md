# PostgreSQL institutional MINAH coordination schema

Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.

`001_institutional_minah_coordination.sql` establishes the durable relational schema required for a shared institutional MINAH deployment authority:

- immutable deployment-version records and a tenant-scoped current head;
- a lifecycle epoch that invalidates leases when deployment state or configuration changes;
- a durable per-tenant/per-deployment fencing sequence;
- lease capabilities represented by verifier digests, never plaintext tokens;
- append-only deployment events with tenant-scoped event identity.

## Current implementation status

The repository now includes a PostgreSQL execution lease adapter at
protocol-reference/postgresql_execution_coordinator.py. It implements shared
lease acquisition, authoritative validation, guarded callback execution, and
idempotent release against these tables. It accepts a connection factory and
expects psycopg 3 connections. The adapter does not yet replace the complete
SQLite deployment registry: PostgreSQL registration, revision, lifecycle
administration, authority-gated onboarding, connection-pool operations, and
production deployment controls remain separate work.

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
