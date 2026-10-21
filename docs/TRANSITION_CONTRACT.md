# KIN-SYS Protocol — Minimal Institutional Transition Contract

**Status:** Draft 0.1 — proposed protocol contract, not a claim of full CISA/IVM/CEE conformance.

## 1. Purpose and scope

This document defines the smallest testable operation for the KIN-SYS protocol research: an authorised actor submits an event requesting a defined state transition; the runtime checks authority and a declared rule version; and an accepted transition records the new state and its history as one local commitment.

The first conformance slice is deliberately local and single-process. It is not a distributed protocol, consensus mechanism, identity provider, cryptographic signature implementation, or production transaction manager.

## 2. Research lineage and authority of sources

The current source record supports the following limited mapping:

- **CRS:** a source document exists at `ivm-research/specifications/crs/CRS_Draft_2026-08-06.docx`. It has not yet been text-verified in this protocol workstream, so no detailed CRS semantics are asserted here.
- **CISA Draft 0.1:** the extracted source defines deterministic transitions, actor/authority checks, deterministic side-effect-free rules, immutable events, explicit transitions, and a prescribed execution order. The original DOCX remains authoritative over the machine-extracted Markdown.
- **IVM:** the available specification artefact is front matter for IVM 1.0 Draft, identifying IVM as the execution architecture responsible for implementing CISA. The complete IVM 1.0 specification is not present in the source archive.
- **CEE:** the CISA document is labelled as part of a CEE volume and describes CEE as the runtime, but a separate complete CEE specification was not located in the source tree inspected. Do not infer missing CEE requirements.
- **IVM runtime experiments:** the v0.3/v0.4 artifacts document a local State/History commitment gap and a recovery-oriented journal with phases `PREPARED → STATE_COMMITTED → HISTORY_ATTACHED → FINALIZED`. Experiment 004A reports process-death recovery under its documented test conditions.
- **Experiment 005:** the available conventional deterministic state-machine comparator passes five tests for its current semantic contract. This is evidence against assuming that the abstraction is inherently unique or that conventional implementations cannot satisfy it.

Source repository: https://github.com/joe933212-pixel/ivm-research  
Published IVM research record: https://doi.org/10.5281/zenodo.22011725

This contract is a KIN-SYS research proposal informed by these sources. It is not a replacement for the source specifications and does not retroactively make the experiments KIN-SYS implementations.

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

The implementation and tests for this contract are a new KIN-SYS protocol prototype. Results should be reported only after the exact committed source is run from a clean environment. The next gate after local conformance is to compare its observable result vector with the IVM v0.4 runtime and Experiment 005, then add crash/restart, concurrency, storage-fault, and independently implemented comparator tests.

Passing this slice would establish only that the tested implementation satisfies these local contract cases. It would not establish full protocol conformance or research novelty.
