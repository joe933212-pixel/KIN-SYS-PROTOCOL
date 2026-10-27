# CEE–IVM Recovery Contract

**Copyright © 2026 Joel Kagoro.** Licensed under Apache-2.0; see `LICENSE` and `NOTICE`.

**Status:** Research/reference contract for the local KIN-SYS prototype. The recovery path is implemented and covered by CI, but this document is not a production security or distributed-transaction guarantee.

## Purpose

The ingress journal, CEE lifecycle journal and IVM transition store are separate durable SQLite stores. A process can fail after one store commits and before another store records acknowledgement. Recovery must establish what already happened without accepting replacement instructions or blindly applying a transition twice.

The canonical execution path remains **MINAH → identity verification → action-specific authority verification → CEE → IVM inside CEE → final decision**. Recovery does not create an alternate execution engine and does not allow MINAH to grant authority.

## Invariants

- **Operation identity is stable.** Recovery uses the existing operation ID and the original request; a caller cannot submit replacement content through the recovery endpoint.
- **Ingress content is pinned.** The v0.1 instruction and mapped execution request are durably stored with fingerprints. Reusing an operation ID with changed instruction or mapped execution content is a conflict.
- **CEE is the lifecycle authority.** Recovery reads the CEE journal and resumes only from a recognised durable phase. Passive reconciliation additionally requires the CEE request fingerprint, with MINAH-specific pins removed, to match the mapped execution-request fingerprint stored at ingress.
- **MINAH disposition must be durable.** A CEE operation still in `REQUESTED` without a persisted MINAH disposition is ambiguous. Recovery stops rather than automatically asking MINAH again.
- **Profile and approval are pinned.** A changed MINAH profile or approval cannot be substituted during recovery.
- **IVM event identity is stable.** The same IVM event ID and request digest are used on retry. The local IVM store returns the recorded outcome for an identical event and rejects the same ID with different content.
- **Unknown stays unknown.** Missing or inconsistent evidence, an unsupported phase, or a failed recovery does not become a successful decision.
- **Recovery is auditable.** Ingress records recovery start, completion, or blocked attempts, including the phase and bounded diagnostic evidence.

## Failure-point matrix

| Failure point | Durable evidence | Recovery behaviour |
|---|---|---|
| Ingress accepted; no CEE record exists | Ingress row and mapped-request fingerprint only | Return `UNKNOWN`. Do not create a fresh CEE operation automatically; establish whether the CEE store is missing, reset or unavailable first. |
| CEE `REQUESTED`; no MINAH disposition persisted | CEE request, but no durable MINAH decision | Stop for manual reconciliation. MINAH may have been called before the interruption, so blind re-invocation is not assumed safe. |
| MINAH disposition persisted; identity or authority phase incomplete | Pinned CEE request and MINAH disposition | Resume from the durable CEE phase. Identity and authority adapters must be read-only/idempotent verification operations; uncertain checks fail closed. |
| CEE `EXECUTING` / `PREPARED` | Pinned process context and CEE phase | Resume the phase-aware pipeline. Process creation and context checks must be idempotent and must reject immutable-context mismatches. |
| CEE `COMMITTING`; IVM has not committed | CEE phase and stable IVM event ID | Execute the pinned IVM event. The local IVM transaction determines whether it commits. |
| IVM committed; CEE acknowledgement was lost | IVM event and transition history exist; CEE remains `COMMITTING` | Re-submit only the same pinned event to the idempotent IVM store. It returns the prior outcome; CEE then records `COMMITTED` and `FINALIZED`. No second state transition is created. |
| CEE `COMMITTED`; final response was not written | Durable IVM decision stored in CEE | Finalize from the stored IVM decision without reapplying the transition. |
| CEE `FINALIZED`; ingress result was not written | Durable final CEE result | Recovery copies the established result into the ingress journal and records the recovery event. |
| Request, event, profile or approval does not match pinned evidence | Conflicting fingerprints or trust metadata | Fail closed with a conflict or unresolved status. Do not overwrite the original record. |
| CEE/IVM stores disagree, are unavailable, or contain an unsupported state | Incomplete or contradictory durable evidence | Keep `UNKNOWN`; require operator-led reconciliation. Do not infer finality from one incomplete store. |

## HTTP contract

`GET /protocol/operations/{operation_id}` is for inspection and passive reconciliation from an already-finalized CEE result. It verifies that CEE's pinned request fingerprint matches the ingress execution-request fingerprint before copying the result; it does not resume execution.
- `POST /protocol/operations/{operation_id}/recover` requests explicit recovery of an existing uncertain operation. It accepts no replacement instruction body.
- Completed ingress replay returns the stored result without rerunning the pipeline.
- Recovery outcomes are recorded in the ingress recovery-event journal. `GET /protocol/operations/{operation_id}/recovery-events` exposes a sanitized audit projection with action, phase, timestamp and allow-listed evidence; raw exception messages are not returned by this route.
- The recovery and recovery-audit routes are local prototype interfaces without production authentication or rate limiting. They must not be exposed to untrusted networks.

The route currently runs with synthetic adapters and no production authentication. It is suitable for local protocol verification, not as an exposed production recovery endpoint.

## Evidence and acceptance gates

CI includes both an application-level restart test and subprocess tests that terminate the process with `os._exit` at four durable boundaries: after CEE records `REQUESTED` but before a MINAH disposition; immediately before the IVM transaction; after the IVM commit but before CEE acknowledges it; and after CEE records `COMMITTED` but before `FINALIZED`. The stores are reopened and the tests verify the prescribed recovery or manual-reconciliation behaviour, including that IVM transition history never contains a duplicate transition.

These tests demonstrate recovery at four representative boundaries in the reference implementation. They do not cover every instruction between durable writes or prove resilience against every operating-system, filesystem, power-loss, storage-corruption or multi-worker failure.

Still required before production consideration:

- crash testing at all remaining durable phase boundaries and under filesystem/power-loss fault injection;
- reconciliation policy and operator evidence for missing/corrupt CEE or IVM stores;
- transactional/outbox design for any future external side effects;
- authenticated and authorised recovery requests, multi-worker/concurrency analysis, backup/restore drills and storage-integrity checks;
- independent security review and production adapter conformance tests.

Separate SQLite databases do **not** provide cross-store atomicity. Local idempotency does not establish distributed finality, payment finality, legal correctness or exactly-once behaviour for an external provider.
