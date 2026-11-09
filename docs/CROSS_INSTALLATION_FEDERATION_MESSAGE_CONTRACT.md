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

`sqlite_nonce_consumer(...)` adapts the existing `SQLiteNonceGuard` to a composite scope of receiver tenant, issuer installation and nonce. This is suitable for a local/shared SQLite file only. Independent hosts must use a common durable atomic replay service; separate per-node SQLite files do not prevent cross-host replay.

The callback passed to the verifier must implement atomic insert-if-absent semantics and return `True` only for the first valid consumption. A replay-store outage fails closed.

## Current scope and next increment

Implemented here: canonical signed envelope creation, receiver-side cryptographic and routing verification, bounded freshness, receiver-local trust-key checks, replay callback boundary and adversarial unit tests.

Not yet implemented: network transport, authenticated installation discovery, outbound delivery/retry queues, durable message inbox/outbox, acknowledgement protocol, receiver-side execution orchestration, cross-installation operation status, cross-store recovery, and end-to-end testing with two independently configured runtime stacks. The current tests use generated Ed25519 keys and test doubles; they are not evidence of live federation or production readiness.

The next engineering increment should build two isolated installation fixtures with separate registries, trust stores, identity policies, MINAH pipelines and CEE journals. A signed message should cross the boundary, be verified by the receiver, and then enter only the receiver's local governed execution path. Tests must prove both sides retain separate decision rights and audit histories.


## Receiver-local execution adapter

`protocol-reference/institutional_federated_receiver.py` adds the next boundary: after successful envelope verification, it selects the receiver tenant, institution and binding exclusively from receiver-local configuration and passes the digest-verified instruction and its evidence to the injected local governed execution service. The expected production integration is the existing `InstitutionalInstructionExecutionService`, which resolves the active receiver binding, pins its own identity provider/jurisdiction/MINAH deployment and enforces the local MINAH, identity, authority, CEE and final-decision execution order.

The adapter never uses origin claims to select the receiving institution or binding. It does not translate a verified message into approval; a receiver-side rejection remains a receiver-side rejection. Its unit tests use a recording executor to prove routing and fail-closed behavior. They do not yet instantiate two complete production-configured execution stacks.

## Next integration test target

The next test increment must instantiate two isolated installation configurations and connect each adapter to its own real institutional execution service, registry, MINAH deployment resolver, identity provider, authority grant store and CEE/IVM persistence. It must prove that a receiver-local jurisdiction or authority rejection does not alter the origin's history, and that an acknowledgement is correlated to the original message/operation without sharing decision authority.
