# CEE-Controlled Integrated Execution: Engineering Plan

Status: DESIGN BASELINE — integration not yet implemented
Audience: Protocol Runtime, CEE, IVM, MINAH, Institutional Integration, Fintech, Edge/Distributed Systems, Security, SRE
Scope: Close the current gap between the runnable MINAH → IVM → CEE instruction slice and the separate institutional/payment/edge reference slices.

## 1. Current verified boundary

The repository currently contains three related but separate execution paths:

- `protocol-reference/protocol_pipeline.py`: a runnable synthetic MINAH → IVM → CEE vertical slice. CEE records lifecycle phases; IVM applies a narrow local SQLite state transition; retries can recover from the tested IVM-commit/CEE-ack interruption.
- `protocol-reference/integrated_demo.py`: a synthetic cross-institution case that calls identity, MINAH, origin and destination decisions, fictional assessment, payment lifecycle and edge queue components. It has important safeguards, including checking current institutional authority and rule versions before creating the assessment/payment, and keeping offline edge replay at “ready for central review.”
- `protocol-reference/payment_lifecycle.py` and `protocol-reference/edge_queue.py`: separate durable local SQLite models with idempotency and explicit unknown/reconciliation or stale-context outcomes.

These are useful reference components, but the legacy integrated demonstrator does **not** run its institutional, payment and edge stages inside the CEE journal / IVM execution lifecycle. Its trace is not proof that the full case has been executed by the newer CEE/IVM path. Do not describe the components as fully integrated or production-ready.

## 1A. Implementation progress since this design baseline

A first CEE-owned synthetic case coordinator has now been implemented in `protocol-reference/cee_case_coordinator.py`, exposed through `POST /cee/cases` and read-only `GET /cee/operations/{operation_id}`. It coordinates MINAH advisory review, origin/destination decisions, an IVM economic gate after both current approvals, fictional assessment and the simulated payment lifecycle. `POST /cee/reconciliation` records source-sequenced synthetic findings.

CI run [#234](https://github.com/joe933212-pixel/KIN-SYS-PROTOCOL/actions/runs/39922195554) passed 185 tests, including rejection gates, missing-evidence deferral, replay/conflict, uncertain payment submission recovery, reconciliation sequencing and HTTP inspection. This is a local synthetic implementation, not proof of production integration.

Remaining gaps include: independent SQLite stores without cross-store atomicity; no production transactional outbox/dispatcher or authenticated provider idempotency; no authenticated/authoritative reconciliation source; no production identity or legal-rule source; limited crash injection around every stage; no settlement-evidence HTTP route; no real MINAH service, distributed finality, or Nexus Pod integration. Caller-supplied reconciliation is demonstrator input only and must not be treated as trusted external evidence.

## 2. Non-negotiable execution invariants

1. **CEE owns orchestration.** Every accepted top-level instruction has a stable operation ID, a pinned request digest, explicit lifecycle events, a durable result and a read-only inspection endpoint.
2. **MINAH is advisory, never self-authorising.** Validate its output schema and provenance; record the disposition and version. A request for more evidence defers execution. A malformed or self-authorising disposition fails closed.
3. **IVM checks the authoritative transition.** Actor/authority, active status, rule-set/version, eligibility and prior state are revalidated at the transition boundary. Client-supplied booleans must not establish authority or legal eligibility.
4. **Institutional approval is stage-specific.** Origin approval does not imply destination approval. Rejection, revoked authority, actor mismatch, stale rules or missing evidence must stop dependent stages.
5. **No economic effect before all prerequisites.** Do not create an actionable payment instruction or invoke a provider until the current, independently recorded approvals and assessment are valid. Provider acknowledgement is not settlement.
6. **Unknown means unknown.** Timeout/unknown provider results remain unresolved until fresh, ordered reconciliation evidence arrives. Never auto-resubmit an uncertain payment.
7. **Edge replay is not approval.** Offline queues may submit requests and later become eligible for central review only after integrity, evidence-version, rule-version and authority checks. Institutional decisions must be made by the appropriate online authority path.
8. **Every boundary is idempotent.** Stable event IDs and request digests prevent duplicate effects; reusing an ID with different content is a conflict.
9. **Recovery is explicit.** For every durable phase, document whether the operation resumes, defers, rejects or requires reconciliation. Do not assume independent SQLite databases commit atomically.
10. **Truthful status.** Synthetic adapters, local SQLite, missing cryptographic authentication, external services, distributed consensus and hardware must remain clearly labelled until implemented and independently tested.

## 3. Required orchestration model

Implement a CEE-owned case execution coordinator as a durable state machine. Avoid wrapping the whole `IntegratedDemonstrator.run_case()` in a single call: that hides internal commit boundaries and makes recovery/side-effect ordering impossible to prove.

Suggested stages (final names are subject to protocol review):

`REQUESTED → ADMITTED → CONTEXT_PINNED → MINAH_REVIEWED → IVM_PRECHECKED → ORIGIN_DECIDED → DESTINATION_DECIDED → OBLIGATION_PREPARED → PAYMENT_PREPARED → PAYMENT_SUBMISSION_PENDING → PAYMENT_OUTCOME_RECORDED → FINALIZED`

Terminal or waiting outcomes must be first-class: `DEFERRED_EVIDENCE`, `REJECTED_AUTHORITY`, `REJECTED_RULES`, `REJECTED_INSTITUTIONAL_STAGE`, `PENDING_RECONCILIATION`, and `FAILED_REQUIRES_OPERATOR`. Never advance to a payment stage when either institutional approval is absent or no positive assessment exists.

For each stage, persist: operation ID, stable event ID, input digest, pinned rule/evidence versions, actor/authority reference, outcome, component/version, timestamps, failure code, retry policy and references to evidence. A retry must inspect durable state and resume from the last confirmed boundary; it must not replay an external side effect blindly.

## 4. Side-effect and recovery design

Before integrating a real provider, add a durable outbox or equivalent provider-command record in the same transactional boundary as the local decision to submit. The dispatcher must use a provider idempotency key derived from the stable payment instruction ID. Record provider request/response references without secrets. If a timeout leaves the result unknown, transition to reconciliation—not another submission.

The current payment model stores its own records in a separate SQLite database from the CEE journal and institutional workflow. Until a shared transactional store or reviewed distributed recovery protocol exists, describe cross-store coordination as a recoverable saga, not an atomic transaction. Each boundary needs a crash-injection test and an operator repair procedure.

## 5. Required integration tests

### Protocol and institutional gates
- MINAH asks for more evidence: no institutional decision, obligation, payment instruction or provider call.
- Malformed/self-authorising MINAH output: reject before IVM and downstream actions.
- Origin rejection: destination, assessment, obligation and payment are not attempted.
- Destination rejection: assessment, obligation and payment are not attempted.
- Wrong actor, revoked authority or changed rule version: no downstream financial action, including on replay after an earlier approval.
- Replayed operation ID with identical content: same durable result and no duplicate side effect.
- Reused operation/event ID with different content: explicit conflict.

### Payment boundaries
- No payment instruction exists before both current institutional approvals and a valid assessment.
- Provider acceptance alone never marks settlement.
- Declined and unknown outcomes remain distinct.
- Timeout followed by process restart does not resubmit automatically.
- Reconciliation must be source-sequenced; stale findings cannot overwrite newer findings.
- Settlement amount/currency/obligation integrity failures roll back the entire local payment transaction.
- Crash after outbox commit, after provider submission, after acknowledgement, and before CEE acknowledgement each has deterministic recovery behavior.

### Edge and distributed execution
- Offline request queues without deciding.
- Reconnect with stale evidence/rules or revoked authority is deferred/rejected.
- Successful reconnect means central-review eligibility only.
- Tampered payload is quarantined.
- Duplicate delivery and reordered messages do not duplicate institutional or financial effects.
- Node restart preserves queued operation state and audit trail.

### Observability and API
- Each operation exposes the ordered MINAH, IVM, CEE, institutional, assessment, payment and edge trace entries that actually occurred.
- Inspection is read-only and never silently resumes an operation.
- Readiness/capabilities report missing dependencies accurately.
- No endpoint claims production authentication, live legal data, real funds movement, distributed finality or hardware integration until those are tested.

## 6. Implementation sequence

1. Introduce a CEE-owned case journal and transition contract that can resume individual institutional and payment stages.
2. Refactor institutional workflow methods into explicit stage adapters; do not hide multiple stages in one opaque call.
3. Make IVM decisions correspond to each protected transition, or document and review a single explicit invariant that explains why a transition is sufficient. Avoid generic activation as a substitute for case-stage authorization.
4. Add durable obligation/payment preparation and outbox semantics; keep the current simulated provider behind the same adapter interface.
5. Bring edge enqueue/replay into the same operation trace while preserving central-only decision authority.
6. Expose a new integrated prototype API route only after these stage contracts and failure tests exist. Keep the legacy demo route explicitly labelled as a legacy synthetic path.
7. Add contract conformance tests, crash/restart tests and security review evidence before declaring the integrated slice complete.

## 7. Production-team handoff requirements

For each component, the implementation handoff must specify the interface/schema, authority source, trust boundary, failure semantics, idempotency key, durable state, observability, threat model, test fixture, acceptance criteria, operational owner and what remains synthetic. Required reviewers include MINAH/AI and legal engineering, IVM/runtime, CEE/orchestration, identity/security, institutional systems, fintech/provider integration, edge/hardware, distributed systems, DevSecOps/SRE and API/client engineering.

This plan is a design baseline, not evidence that the listed integration is implemented. The next code milestone is a reviewed recovery design for settlement evidence, cross-store consistency and production-grade outbox/dispatcher semantics, plus crash-injection tests at every persisted boundary. Only after those boundaries and their security contracts are reviewed should production API routes or real provider adapters be implemented.
