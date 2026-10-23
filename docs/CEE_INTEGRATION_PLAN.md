# Unified CEE Execution Boundary: Engineering Plan

Status: PARTIAL IMPLEMENTATION — canonical synthetic instruction slice refactored; case-level integration remains incomplete
Audience: Protocol Runtime, CEE, IVM, MINAH, Institutional Integration, Fintech, Edge/Distributed Systems, Security, SRE
Scope: Align the implementation with one public protocol path: MINAH -> CEE -> final decision. The execution engine and its environment are one CEE boundary; internal implementation details must not appear as separate protocol paths. Applicable institutional, payment, and edge effects must be brought under the same case-linked trace where relevant.

## 1. Current verified boundary

The repository contains a partially unified canonical instruction slice and related legacy research components that are not yet integrated into one conformant case path:

- `protocol-reference/protocol_pipeline.py`: a runnable synthetic instruction slice. The current refactor records MINAH review before CEE admission and exposes one final-decision contract. The CEE lifecycle journal and private execution tables now share one local SQLite connection. Their commit/acknowledgement boundary remains separate and is covered by recovery tests; this is one public execution path, not a second runtime.
- `protocol-reference/integrated_demo.py`: a synthetic cross-institution case that calls identity, MINAH, origin and destination decisions, fictional assessment, payment lifecycle and edge queue components. It has important safeguards, including checking current institutional authority and rule versions before creating the assessment/payment, and keeping offline edge replay at “ready for central review.”
- `protocol-reference/payment_lifecycle.py` and `protocol-reference/edge_queue.py`: separate durable local SQLite models with idempotency and explicit unknown/reconciliation or stale-context outcomes.

These are useful reference components, but the legacy integrated demonstrator does **not** yet run its institutional, payment and edge stages inside the canonical MINAH-first CEE lifecycle. Its trace is not proof that a full case has been executed through one unified path. Do not describe the components as fully integrated or production-ready.

## 1A. Implementation progress since this design baseline

A first CEE-owned synthetic case coordinator has now been implemented in `protocol-reference/cee_case_coordinator.py`, exposed through `POST /cee/cases` and read-only `GET /cee/operations/{operation_id}`. It coordinates a deterministic MINAH stand-in, origin/destination decisions, a private reference execution gate after both current approvals, fictional assessment and the simulated payment lifecycle. The case coordinator now follows MINAH-first ordering and shares its CEE-owned local SQLite connection with the private execution runtime; institutional and payment adapters retain separate stores. All adapters remain synthetic. `POST /cee/reconciliation` records source-sequenced synthetic findings; `POST /cee/settlement-evidence` validates caller-supplied synthetic settlement evidence against the pinned obligation.

CI run [#241](https://github.com/joe933212-pixel/KIN-SYS-PROTOCOL/actions/runs/39923814382) passed 190 tests, including rejection gates, missing-evidence deferral, replay/conflict, uncertain payment submission recovery, reconciliation sequencing, settlement amount/currency validation, settlement event-ID conflicts, split-store crash recovery and HTTP inspection. This is a local synthetic implementation, not proof of production integration.

Remaining gaps include: the legacy case coordinator still uses independent SQLite stores without cross-store atomicity; no production transactional outbox/dispatcher or authenticated provider idempotency; no authenticated/authoritative reconciliation source; no production identity or legal-rule source; limited crash injection around every stage; no production outbox/dispatcher; no authenticated settlement or reconciliation source; no real MINAH service, distributed finality, or Nexus Pod integration. Caller-supplied reconciliation is demonstrator input only and must not be treated as trusted external evidence.

## 1B. Mandatory MINAH-first instruction conformance sequence

**MINAH is the first protocol-facing recipient of every citizen, operator, enterprise, or machine instruction.** She is the sovereign AI gatekeeper and jurisprudential oracle, not a downstream adviser called only after CEE admission. Voice is an input channel, not proof of identity. MINAH must establish a trustworthy instruction context and complete required identity, authority, legal-context, and preliminary checks before handing an approved instruction downstream.

The target sequence is one public execution path:

`MINAH_RECEIVES_INSTRUCTION → INPUT_AND_CHANNEL_VALIDATED → IDENTITY_AND_ACTION_AUTHORITY_CHECKS → JURISDICTION_AND_RULE_CONTEXT_PINNED → PRELIMINARY_EVIDENCE_AND_POLICY_CHECKS → MINAH_DISPOSITION_AND_INTEGRITY_PROTECTED_HANDOFF → CEE_ACCEPTS_HANDOFF → CEE_EXECUTES_APPLICABLE_PROTOCOL_SEMANTICS_AND_REQUIRED_EFFECTS → FINAL_DECISION(PASSED|REJECTED|DEFERRED) → READ_ONLY_INSPECTION`

This is the mandatory order for applicable stages, not a claim that every instruction needs payment, two institutional decisions, or edge execution. The instruction-class contract must mark inapplicable stages explicitly with a reason. MINAH must not hand an instruction downstream if identity is unverified, the actor's required authority is absent/expired/out of scope, mandatory preliminary checks fail, or the legal context cannot be established with sufficient confidence. Such requests are rejected, deferred, or routed for authorised human review as policy requires; dependent downstream side effects must not occur.

Identity must be checked through an authorised national or enterprise identity-registry API, not by direct ungoverned database access. The implementation must record the issuer/source, verification result, credential or subject reference, verification time, and status/version where available, while minimising retained personal data. A voiceprint, caller ID, or spoken name alone is not conclusive identity proof. Authority verification is a separate check from identity: a known person may still lack permission for the requested action. Authority must be scoped to action, resource, jurisdiction, validity period, delegation, and relevant limits.

MINAH's legal grounding must be jurisdiction-aware and versioned: the applicable national, enterprise, or company rules and their effective dates/source references must be pinned to the instruction. Ambiguous legal interpretation, stale/missing authoritative sources, conflicting provisions, or high-impact exceptions must follow an explicit defer/escalation policy. MINAH records a traceable jurisprudential disposition and rationale references; she does not grant herself execution authority or bypass CEE enforcement.

CEE's durable execution record begins when it receives the MINAH-approved handoff envelope; CEE owns runtime context, lifecycle/orchestration, persistence and inspection. This is one execution boundary, not separate engines or public paths. A prototype may use separate storage adapters, but that implementation choice must not imply competing authorities. MINAH ingress itself must have durable receipt/audit correlation so rejected and deferred instructions remain inspectable. The handoff envelope must bind the original request digest, verified principal/authority references, jurisdiction, pinned legal/rule versions, evidence references, MINAH model/policy version, disposition, and stable correlation/operation ID. CEE validates envelope integrity and runtime preconditions, then enforces the applicable execution semantics. It must not blindly trust an unchecked MINAH assertion.

The `/protocol/instructions` and `/cee/cases` synthetic paths now record MINAH review before CEE admission, but neither is compliant production ingress: MINAH and pre-admission authority checks are synthetic and do not implement national-registry-backed identity verification or authoritative legal sources. `/demo/instructions` remains a legacy research demonstrator. Production conformance tests must prove that no downstream CEE admission, institutional decision, payment preparation, provider call, or edge action occurs before required MINAH identity/authority/preliminary checks pass. Health, capability, and read-only inspection calls are not top-level execution instructions and must not mutate or resume an operation.

## 2. Non-negotiable execution invariants

1. **CEE is the single execution boundary and owns runtime lifecycle/orchestration and applicable execution semantics; internal runtime details are not separate protocol paths.** Every accepted top-level instruction has a stable operation ID, a pinned request digest, explicit lifecycle events, a durable result and a read-only inspection endpoint.
2. **MINAH is the first gate, but not the sole enforcement authority.** She receives the instruction first and performs identity verification through authorised registry APIs, intent normalisation, jurisdiction/legal-version pinning, scoped authority verification, and preliminary checks before issuing a disposition and handoff envelope. Validate output schema and provenance; a request for more evidence defers execution. A malformed or self-authorising disposition fails closed. IVM and CEE independently enforce their own invariants.
3. **CEE execution semantics are enforced within the unified CEE boundary.** Actor/authority, active status, applicable semantic/rule version, eligibility and prior state are revalidated at the transition boundary. Client-supplied booleans must not establish authority or legal eligibility.
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
- MINAH asks for more evidence: no CEE execution admission, IVM transition, institutional decision, obligation, payment instruction, provider call or edge action.
- Unverified identity, expired/out-of-scope authority, or failed preliminary checks: fail closed before downstream execution; test each case separately.
- Voice input without an independently verified identity credential: no privileged action.
- Registry API timeout, stale identity status, missing jurisdiction/legal source, or ambiguous high-impact rule: defer/escalate, never silently assume approval.
- Replay or tampering of a MINAH handoff envelope: reject unless its integrity, correlation ID, request digest, versions and verification references validate.
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
