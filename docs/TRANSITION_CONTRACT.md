> **Authorship & rights:** Joel Kagoro · Copyright © 2026 Joel Kagoro · Licensed under Apache-2.0; see `LICENSE`, `NOTICE`, and `COPYRIGHT.md`.

# KIN-SYS Protocol — Minimal Institutional Transition Contract

**Status:** Draft 0.1 — proposed protocol contract, not a claim of full CISA/IVM/CEE conformance.

## 1. Purpose and scope

This document defines the smallest testable operation for the KIN-SYS protocol research: an authorised actor submits an event requesting a defined state transition; the runtime checks authority and a declared rule version; and an accepted transition records the new state and its history as one local commitment.

The first conformance slice is deliberately local and single-process. It is not a distributed protocol, consensus mechanism, identity provider, cryptographic signature implementation, or production transaction manager.

## 2. Research lineage and authority of sources

The current source record supports the following limited mapping:

- **CRS:** the public IVM archive includes `specifications/crs/CRS_Draft_2026-08-06.docx`; the user's Library also contains a fuller `CRS.docx` draft with extractable text. The exact relationship/version lineage between these copies must be reconciled before treating them as identical.
- **CISA Draft 0.1:** the extracted source defines deterministic transitions, actor/authority checks, deterministic side-effect-free rules, immutable events, explicit transitions, and a prescribed execution order. The original DOCX remains authoritative over the machine-extracted Markdown.
- **IVM:** the public IVM archive inspected earlier includes front matter only, but the user's Library contains a full IVM 1.0 Draft. The complete document must be used to reconcile the exact execution semantics; the public archive's front matter alone is insufficient.
- **CEE:** a full 335-page CEE 1.0 Draft is available in the user's Library. It defines the operational environment and a lifecycle `REQUESTED → ADMITTED → CONTEXT_ESTABLISHED → AUTHORITY_EVALUATED → EXECUTING → PREPARED → COMMITTING → COMMITTED → FINALIZED`. The public IVM archive inspected earlier does not contain this full CEE document, so the contract must be reconciled against the Library source before being considered settled.
- **IVM runtime experiments:** the v0.3/v0.4 artifacts document a local State/History commitment gap and a recovery-oriented journal with phases `PREPARED → STATE_COMMITTED → HISTORY_ATTACHED → FINALIZED`. Experiment 004A reports process-death recovery under its documented test conditions.
- **Prior-art analysis:** the complete 75-page literature analysis is available in the user's Library and continues beyond Section 53 through the comparative experimental programme. The public repository's literature package notes the continuation point at Section 54 because that full document was not imported there.
- **Experiment 005:** the available conventional deterministic state-machine comparator passes five tests for its current semantic contract. This is evidence against assuming that the abstraction is inherently unique or that conventional implementations cannot satisfy it.

Source repository: https://github.com/joe933212-pixel/ivm-research  
Published IVM research record: https://doi.org/10.5281/zenodo.22011725

This contract is a KIN-SYS research proposal informed by these sources. It is not a replacement for the source specifications and does not retroactively make the experiments KIN-SYS implementations. The local prototype below covers only a narrow subset of the CEE lifecycle and is not a CEE implementation.

## 3. Operation model

A submitted event contains, at minimum:

- a unique event identifier;
- a process identifier;
- an actor identifier;
- an event type;
- the declared rule-set identifier and rule version;
- the input payload needed to evaluate the transition.

A deployment will additionally need a defined authentication/signature envelope, credential and revocation semantics, clock policy, canonical encoding, and network message format. Those are intentionally not invented by this local slice.

For the first example, a process begins in `PENDING`; an authorised actor submits `ACTIVATE`; a named rule version evaluates to true; and the process may move to `ACTIVE`.

## 4. Normative decision sequence for this slice

For an event that has passed basic structural validation, an implementation of this slice MUST:

1. Identify the event by its event ID and compare repeated submissions against the original canonical request.
2. Reject an event ID reused with different request content as an identifier conflict.
3. Resolve the target process and its configured authority.
4. Verify that the actor is the configured actor and that the authority has not been revoked.
5. Resolve the explicitly named rule set and rule version; evaluate its declared predicate without side effects.
6. Verify that the requested transition is defined for the current state.
7. For an accepted transition, persist the resulting state, the accepted event record, and the corresponding transition-history record in one local database transaction.
8. Return a result that distinguishes a newly accepted transition, an identical duplicate, and a rejected/conflicting request.

A rejection MUST NOT change authoritative process state. A structurally valid rejected event MUST be retained in the event/decision record. Rejected operations do not receive a successful transition-history entry.

This sequence defines the scope of the first slice; it does not claim to implement every instruction or every requirement in CISA Draft 0.1.

## 5. Commitment semantics

For this slice, **committed** means that the local database transaction atomically persists the new process state, accepted event outcome, and matching transition-history row. If the transaction fails before commit, none of those changes may remain.

This is a deliberately narrower commitment definition than a distributed or sovereign-network finality guarantee. It depends on the database's local transaction semantics and does not establish replication, Byzantine fault tolerance, power-loss durability on arbitrary hardware, or cross-institution atomicity.

The IVM v0.4 journal phases are useful experimental precedent, but the local database transaction in this slice is not claimed to reproduce the v0.4 journal implementation or prove its broader guarantees.

## 6. Idempotency and replay

- Re-submitting the same event ID with the same canonical request MUST NOT create a second state transition or a second history entry.
- Reusing an event ID for different content MUST return an explicit conflict and MUST NOT mutate state.
- The result for an identical duplicate is a replay of the recorded outcome, not a new evaluation under current rules.
- Each accepted history record MUST retain the rule-set identifier and rule version used at the time of acceptance. A later rule version MUST NOT rewrite past history.

The slice uses event IDs as idempotency keys. A production protocol still needs a canonical request format and a cryptographic binding between the identifier and request content.

## 7. Minimal invariants to test

| ID | Invariant | Observable test |
|---|---|---|
| TC-01 | Only a configured, active authority may cause the transition. | Wrong or revoked actor is rejected; state remains unchanged. |
| TC-02 | A rule that evaluates false cannot change state. | Rejected decision is retained; state remains unchanged. |
| TC-03 | Only a defined transition from the current state may execute. | Invalid source state or event type is rejected. |
| TC-04 | State and transition history commit together. | Injected failure before commit leaves neither state change nor success history. |
| TC-05 | Identical duplicate events do not execute twice. | State version and history count remain unchanged on retry. |
| TC-06 | An event ID cannot identify two different requests. | Conflicting reuse returns a conflict without mutation. |
| TC-07 | Accepted history pins the evaluated rule version. | Changing the configured rule later does not rewrite the recorded version. |
| TC-08 | Valid rejected events remain auditable. | Rejection outcome is persisted without a transition-history entry. |
| TC-09 | A reported success has a corresponding committed history record. | Successful result can be matched to exactly one accepted event and history row. |

## 8. Explicit exclusions

The first slice does not prove or implement:

- cryptographic actor authentication or signature verification;
- national identity integration, zero-knowledge proofs, or identity-bound encryption;
- distributed consensus, multi-node replication, partition recovery, or cross-domain atomicity;
- hardware security modules or physical edge-node security;
- MINAH AI authority or autonomous decision-making;
- a token, wallet, ledger, monetary policy, or $KIN economic model;
- deployed Nexus Pods or production deployment;
- formal verification, security certification, novelty, or commercial superiority.

These are separate research and implementation questions. They must not be represented as implemented because they appear in the wider KIN-SYS vision.

## 9. Evidence and next gate

The implementation and tests for this contract are a KIN-SYS research prototype. The CEE lifecycle requires a strict distinction between provisional execution and institutional commitment: interruption before commitment must not create authoritative state, while interruption after commitment must recover the committed result rather than execute a second transition. The current local experiments test selected pieces of that requirement, not the full CEE lifecycle.

**Verified CI evidence (2026-10-23):** GitHub Actions run [#179](https://github.com/joe933212-pixel/KIN-SYS-PROTOCOL/actions/runs/39912990805), commit `c347307ce0ea00d7826298d1e0d0920138b7a55d`, completed successfully on a clean runner: all 134 discovered tests passed. The suite includes process-termination recovery cases and injected in-process failures after provider-acknowledgement, settlement-evidence, and reconciliation events are written; each failed operation must roll back its event and state together. Retry tests cover duplicate and concurrent acknowledgements, stale acknowledgement/reconciliation retries, and a new provider acknowledgement arriving after a reconciliation finding. Because this prototype has no trustworthy ordering metadata for that late message, it records the evidence but returns the payment to `UNKNOWN_PENDING_RECONCILIATION` until a fresh reconciliation resolves it. An identical acknowledgement retry remains idempotent even after the payment has settled, returning current durable state; a new acknowledgement after settlement is rejected. A cross-border case also verifies that origin rejection prevents destination execution and downstream financial effects. The workflow generated and verified the paired comparator reports and integrated demonstrator reports. These are synthetic, local SQLite experiments; payment funds are never moved.

The next gate is to expand the matched failure matrix across both the KIN-SYS candidate and its conventional comparator: interruption before and after each commitment boundary, repeated requests after an uncertain response, concurrent retries, storage errors, and changed authority or rule context during recovery. Then test cross-institution coordination under delayed and reordered messages, including explicit message ordering/version semantics rather than relying on arrival order. Report comparator differences and failures as well as passing cases.

Passing these tests establishes only that the tested implementation satisfies these specific local cases. It does not establish full CEE conformance, distributed consensus, cross-domain atomicity, production readiness, or research novelty.
