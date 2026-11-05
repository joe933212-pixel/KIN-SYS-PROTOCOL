# KIN-SYS Operator Reconciliation: Design Requirements

Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.

**Status: Proposed design only. Not implemented.**

This document defines the minimum safety requirements for handling contradictory or incomplete durable records in the KIN-SYS reference execution path. It does not authorise an operator to alter CEE or IVM records, and it does not introduce a recovery override.

## 1. Purpose and boundary

The reference pipeline must stop when its evidence is contradictory, structurally damaged, or insufficient to establish whether a protected transition occurred. That stop is intentional. A human decision must not be treated as permission to bypass MINAH, CEE, IVM, identity checks, action-specific authority, or the institution's own mandate.

Operator reconciliation is a separately governed incident procedure. It is not a normal execution phase and must never be callable as an ordinary instruction or recovery option.

## 2. Mandatory preservation rules

1. Preserve the original CEE and IVM database files as evidence. Do not edit, truncate, replace, or delete the source records during evidence collection.
2. Capture stable, read-only snapshots of both stores. Record each source and snapshot's cryptographic file digest, capture time, database schema/version, SQLite integrity-check result, and the exact operation/event identifiers under review.
3. Capture the relevant CEE operation, lifecycle events and observations, IVM event, transition history and process state, pinned request digest, profile/policy version references, software commit, and deployment identity.
4. Keep the two stores' provenance separate. Never infer that independently valid CEE and IVM backups form a coherent pair. A restore set must have verifiable common provenance and a documented capture point.
5. Preserve evidence references and metadata, not raw identity documents, credentials, private keys, access tokens, or unrelated personal data. Apply the institution's retention, privacy, and legal-hold rules.
6. Record every evidence access, export, verification, decision, and subsequent action in an independently protected audit record.

SQLite `integrity_check` is only a structural check. It is not proof of authenticity, non-tampering, or backup provenance.

## 3. Authentication, authority, and approval

A future reconciliation service must integrate with an institution-approved identity and authorisation system. It must:

- authenticate the human operator and verify that the credential is active;
- authorise the operator for the exact institution, environment, operation, incident, and requested reconciliation action;
- verify the decision signer's key against a trusted key registry, including key status, algorithm policy, permitted usage, signer role, and scope;
- bind the signed decision to the exact evidence-bundle digest and the exact CEE/IVM store identifiers and versions;
- require separation of duties: the person proposing a consequential restore or outcome classification cannot be its sole approver;
- fail closed if identity, authorisation, signature, revocation, or audit services are unavailable or return an indeterminate result.

A signature alone is not authority. The trust registry and institution's delegation policy determine whether the signer may approve the specific action.

## 4. Reconciliation decision record

A future versioned decision envelope should include, at minimum:

- schema/version and unique reconciliation identifier;
- institution, environment, incident, operation, and related event identifiers;
- evidence-bundle digest and the digests/provenance of both source stores and snapshots;
- observed conflict codes and a machine-readable description of the inconsistency;
- selected disposition and bounded rationale;
- operator and independent approver identity references and roles;
- signed timestamps, signer key references, signature algorithm, and signatures;
- applicable policy/delegation versions, expiry, and revocation-check evidence;
- software build/commit and the exact procedure version used;
- references to the append-only audit record and any approved restoration/change plan.

The signed payload must use a documented canonical encoding. Unknown fields, unrecognised dispositions, stale approvals, mismatched evidence digests, or unverifiable signatures must be rejected.

## 5. Permitted dispositions

The first implementation should support only conservative, explicit dispositions:

- **PRESERVE_UNRESOLVED** — retain both stores unchanged, quarantine the operation, and escalate. This is the safe default.
- **RESTORE_VERIFIED_BACKUP_SET** — permit a separately executed, approved restoration from a coherent, provenance-verified CEE/IVM backup set. The restore procedure must preserve the pre-restore files and record before/after digests.
- **CLOSE_WITH_NO_TRANSITION** — classify the operation as not having committed only when the evidence and applicable protocol rules establish that conclusion. This disposition must not manufacture or delete an IVM event.
- **COMPENSATION_OR_EXTERNAL_REVIEW_REQUIRED** — record that an external effect may have occurred and require the relevant institution/provider's independently governed compensation or investigation process.

These dispositions are classifications or controlled operational plans, not instructions to rewrite historical CEE/IVM rows. A future version may add a further disposition only with a threat model, policy definition, implementation and adversarial tests.

The system must not offer a generic **force commit**, **force approve**, **ignore conflict**, or **rewrite history** operation.

## 6. Execution and audit model

- Reconciliation must be unavailable while the operation is eligible for ordinary safe recovery. Existing deterministic recovery takes precedence.
- When reconciliation is required, quarantine the operation so no retry or external dispatch can proceed until the incident is resolved.
- The decision must be signed over the evidence-bundle digest and exact action scope. Any change to evidence, stores, request, or proposed action invalidates the approval.
- Restoration must occur through a separately controlled, reviewed procedure with preflight checks, a verified backup pair, a preservation copy, an explicit change window, rollback plan, and post-restore consistency checks.
- Never rewrite old journal events to make the new state appear historically continuous. Record the reconciliation as a new, linked, append-only audit event in a separately protected audit store.
- Protect the audit record against alteration using institution-controlled access controls and an independently retained or externally anchored integrity mechanism. A second SQLite table in the same database is not sufficient proof of immutability.
- Reconciliation must not itself trigger a payment, transfer, legal approval, or other external side effect. External effects require their own idempotency, confirmation, and compensation contracts.

## 7. Minimum acceptance tests before implementation

A future implementation must demonstrate that:

1. an unauthenticated operator cannot inspect protected evidence or submit a decision;
2. a valid signer without the exact institution/operation/action scope is rejected;
3. operator and approver separation-of-duties violations are rejected;
4. changed evidence or store digests invalidate an otherwise valid signature;
5. revoked, expired, unknown, or unavailable credentials/keys fail closed;
6. unknown decision fields and dispositions are rejected;
7. evidence export excludes raw identity documents, secrets, and unrelated personal data;
8. no reconciliation path mutates the original CEE/IVM evidence or silently resumes execution;
9. a restore requires a provenance-verified paired backup and records pre/post digests;
10. audit unavailability prevents consequential reconciliation;
11. a failed or interrupted reconciliation leaves the operation quarantined and evidence preserved;
12. independent reviewers can reproduce the decision from the recorded evidence and policy versions.

## 8. Current implementation boundary

The repository currently implements local CEE/IVM consistency checks, SQLite structural checks, controlled backup-skew tests, and controlled restart/fault-injection tests. It does **not** implement an authenticated operator identity service, trusted signer registry, signed reconciliation envelope, quarantine control, external immutable audit store, or backup-pair provenance mechanism.

Until those dependencies and controls exist, contradictions must continue to stop for manual investigation outside the protocol execution path. This document is a design input, not evidence that operator reconciliation is available or production-ready.


## 9. Read-only evidence bundle prototype

A method-only prototype is implemented in `protocol-reference/recovery_evidence.py`. It creates a versioned allow-listed diagnostic bundle from the live reference pipeline connections. The bundle includes relevant CEE lifecycle events and safe observations, request digest and selected policy/context references, the related IVM event/process/history, SQLite structural-check outcomes, a cross-store consistency result, and a canonical payload digest that excludes the variable capture timestamp.

The builder does not invoke recovery, mutate either store, or expose raw request bodies, raw evidence objects, identity documents, credentials, or arbitrary adapter payloads. Tests cover committed and rejected outcomes, a deliberately contradictory record, missing operations, data minimisation, and unchanged row counts.

This is **not** the evidence-preservation system specified above. It does not create stable read-only database snapshots, calculate database-file digests, establish backup provenance, authenticate an operator, sign a bundle, provide a trusted timestamp, or write to an independently protected audit store. It is not exposed as a remote HTTP route. The bundle digest is an integrity checksum over the generated JSON payload only; it is not a signature, proof of origin, or tamper-proof record.
