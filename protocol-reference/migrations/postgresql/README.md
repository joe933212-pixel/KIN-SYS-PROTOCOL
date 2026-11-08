# PostgreSQL institutional MINAH coordination schema

Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.

`001_institutional_minah_coordination.sql` establishes the durable relational schema required for a shared institutional MINAH deployment authority:

- immutable deployment-version records and a tenant-scoped current head;
- a lifecycle epoch that invalidates leases when deployment state or configuration changes;
- a durable per-tenant/per-deployment fencing sequence;
- lease capabilities represented by verifier digests, never plaintext tokens;
- append-only deployment events with tenant-scoped event identity.

## Important status

This is a schema foundation only. The SQLite registry remains the current executable reference implementation. No PostgreSQL runtime adapter, migration runner, connection-pool policy, or production deployment guarantee is provided by this SQL file.

## Required transaction protocol for the future adapter

The PostgreSQL implementation must treat the deployment head, current version, lifecycle epoch, and fencing sequence as one authoritative coordination boundary.

1. Lease acquisition must begin a database transaction and lock the deployment head (or a dedicated per-deployment coordination row) with `SELECT ... FOR UPDATE`.
2. While holding that lock, it must read the current version/status/epoch, reject non-`ACTIVE` deployments, increment the fencing sequence, and insert the lease. The sequence increment and lease insert must commit together.
3. Lifecycle revision, suspension, reactivation, and revocation must take the same lock and update the version head/status and epoch in the same transaction. A lease must never be issued against a lifecycle state that is concurrently being invalidated.
4. Lease validation must compare the capability digest, expiry, release state, current lifecycle status, epoch, and persisted fencing token. Caller-provided claims are not authoritative.
5. A guarded database action must use the same PostgreSQL transaction and connection as the lease validation when the protected state is PostgreSQL-owned. A callback that writes to another database is not made atomic by this mechanism.
6. Independent workers must share the same PostgreSQL authority. Per-node databases, asynchronous replicas, or a Python protocol-interface check do not establish this property.

Before an adapter is considered conformant, integration tests must exercise concurrent acquisitions from independent connections/processes, lifecycle-versus-acquisition races, restart persistence, token uniqueness/monotonicity, tenant/deployment isolation, lease expiry/release, and rollback after injected failures.

The schema deliberately does not claim atomicity with the separate IVM store or CEE journal. That requires a separately designed durable event/outbox and recovery protocol, tested against actual crash windows.
## Applying migrations

The repository includes `protocol-reference/postgresql_migrations.py`. It applies ordered SQL migrations in one database transaction while holding a PostgreSQL transaction-scoped advisory lock. It records each migration's SHA-256 checksum and refuses to continue if an already-applied migration file has changed. Migration files must not issue `BEGIN`, `COMMIT`, or `ROLLBACK`; transaction ownership belongs to the runner.

Install the optional driver with `pip install 'psycopg[binary]>=3.2,<4'`, set `KIN_SYS_POSTGRES_DSN` to the target database connection string, then run `python protocol-reference/postgresql_migrations.py`. The caller is responsible for supplying a least-privilege migration identity, network controls, backups, and an appropriate deployment/change-management process. The runner does not create the PostgreSQL server or configure its security posture.
