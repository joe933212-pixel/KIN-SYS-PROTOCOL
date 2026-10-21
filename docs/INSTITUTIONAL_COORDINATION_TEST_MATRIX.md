> **Authorship & rights:** Joel Kagoro · Copyright © 2026 Joel Kagoro · Licensed under Apache-2.0; see `LICENSE`, `NOTICE`, and `COPYRIGHT.md`.

# Institutional Coordination Test Matrix

**Status:** Research design, draft 0.1. This document defines proposed tests; it does not claim that the current prototype implements the workflow.

## 1. Hypothesis

Can independently governed institutions complete a multi-stage process while each retains authority over its own decision, and while hand-offs, retries, interruptions, and disputed evidence remain observable and recoverable?

Compare the KIN-SYS candidate against a conventional workflow/API with explicit authority checks, idempotency, durable audit records, and recovery. Do not credit KIN-SYS for properties the conventional baseline also satisfies.

## 2. Synthetic scenario

An exporter submits an agricultural consignment request. An origin authority makes its own decision; a destination authority independently makes its decision; a payment provider may supply evidence if a fee applies; a coordination service routes requests and reports workflow status; an authorised reviewer handles disputes.

These are abstract roles, not assertions about any particular country's laws. Before a real pilot, participants must specify actual mandates, rules, data permissions, retention, appeals, and external-evidence contracts.

## 3. Proposed lifecycle

| Stage | Responsible role | Output | Boundary |
|---|---|---|---|
| C0 Request registered | Coordination service | Unique request and pending status | Registration is not approval. |
| C1 Origin decision | Origin authority | Approve, reject, or request more information | Only origin authority decides this stage. |
| C2 Destination decision | Destination authority | Approve, reject, or request more information | Origin approval cannot override destination authority. |
| C3 Payment evidence (if required) | Payment provider | Confirmed, rejected, or unresolved | Timeout is not proof of payment or non-payment. |
| C4 Workflow disposition | Coordination service | Ready, rejected, or unresolved | Service derives status; it does not create approvals. |
| C5 Notification/recovery | Coordination service | Outcome references and retry-safe delivery | Lost notification does not undo commitment. |
| C6 Dispute/correction | Authorised reviewer and institution(s) | Linked resolution/correction event | Prior history is not silently rewritten. |

The actual order and whether C3 is required must be established by participating institutions.

## 4. Minimum falsifiable tests

| ID | Condition | Required result | Failure condition |
|---|---|---|---|
| IC-01 | Origin approves; destination rejects | Decisions remain separately attributed; workflow is not fully approved | Origin approval overrides destination rejection |
| IC-02 | Coordination service submits a decision without the required institutional authority | Rejected or unresolved; no institutional decision created | Service privilege is silently treated as institutional authority |
| IC-03 | Authority revoked before its decision boundary | Declared revocation rule is applied deterministically and recorded | Revocation ignored or undocumented race |
| IC-04 | Same stage request delivered twice | One logical effect; retry returns prior result or explicit uncertainty | Duplicate institutional effect |
| IC-05 | Same ID reused with different content | Explicit conflict; original outcome remains unchanged | Silent overwrite or reinterpretation |
| IC-06 | Origin commits; process stops before destination stage | Origin remains committed; destination remains pending | Lost origin outcome or fabricated destination decision |
| IC-07 | Destination commits; acknowledgement is lost | Retry finds committed outcome without a second effect | Duplicate decision/history effect |
| IC-08 | Payment provider times out after possible execution | Unresolved pending reconciliation evidence; no blind duplicate payment | Timeout treated as confirmed success/failure without evidence |
| IC-09 | Rule changes while a stage is pending | Governing rule version is defined and recorded | Silent rule swap or reinterpretation of history |
| IC-10 | Evidence is disputed | Source and disputed claim remain identifiable; declared review path is used | Coordination layer invents a single truth or erases dispute |
| IC-11 | Notification unavailable after final disposition | Outcome remains queryable; notification can be retried separately | Delivery conflated with commitment |
| IC-12 | Correction is authorised after a decision | Linked correction event records authority and basis | Prior history silently edited |

For every test, preserve synthetic input IDs, per-role state, decision actor, authority basis, rule version, history references, fault point, recovery outcome, raw output, and manual intervention.

## 5. Matched comparison

Run the same logical cases against:

1. A conventional workflow/API with explicit authority checks, idempotency, durable audit records, and recovery.
2. The KIN-SYS candidate, using only components required by the case.
3. The current local prototype only for cases it can express honestly.

The original `ProtocolStore` still models one local transition and one configured authority. A separate experimental two-stage SQLite model and an independent conventional counterpart now cover origin and destination decisions, stage-specific authority/rule checks, duplicate/conflict handling, retained history, and file-backed reopen. They do not yet implement C0–C6 end to end. A process-death test now checks recovery when the origin decision has committed but the destination stage has not begun. Payment reconciliation, notification retries, and cross-institution message delivery remain unimplemented. Correction handling is a two-step experiment: a configured reviewer may propose a linked correction without changing state, but only the institution's own active stage authority can apply it. Separate proposal and application events are tested; original history is retained. This does not establish legal validity or a complete appeals/joint-review process. Unsupported cases are not passes and must not be hidden in aggregate scores. Both implementations must receive the same fixtures and expected observable result vectors, but must retain independent internal implementations.

## 6. Initial implementation gate

Before adding AI, token economics, privacy proofs, or distributed edge deployment, extend the model only enough to represent distinct origin and destination decisions, separate actor/authority bases, per-stage state and rule versions, stable operation IDs, durable history, retries, interruption between stages, unresolved external evidence, and linked dispute/correction events.

The conventional implementation must satisfy the same observable contract without importing KIN-SYS transition logic. Measure performance only after semantic and failure coverage is matched.

## 7. Decision rule and evidence links

The hypothesis weakens if a well-designed conventional workflow achieves the same authority separation, recovery, auditability, privacy scope, and outcomes with equal or lower complexity and cost. A KIN-SYS-specific benefit must be reproducible and tied to a defined mechanism. If the protocol layer is unnecessary for this case, record that result.

Related documents: [Integrated Use Case](INTEGRATED_USE_CASE.md), [Observable Result Vector](OBSERVABLE_RESULT_VECTOR.md), [Comparator Assessment](COMPARATOR_ASSESSMENT.md), and [CEE Conformance Trace](CEE_CONFORMANCE_TRACE.md).

A passing local test suite is not proof of full CEE conformance, distributed security, legal validity, or successful institutional coordination.
