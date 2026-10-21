# Integrated KIN-SYS Use Case: Cross-Border Trade Coordination

**Status:** Research scenario, not a deployment claim.  
**Purpose:** Test whether the integrated KIN-SYS architecture can improve coordination across independent institutions without replacing their mandates.

## 1. Research question

Can independently governed institutions coordinate a cross-border trade process with fewer ambiguous outcomes, duplicate effects, and unresolved hand-offs than a well-designed conventional workflow, while preserving each institution's authority and limiting unnecessary disclosure?

This is a candidate validation scenario for the Africa Single Digital Market problem raised in the FITSPA discussion. It does not assume that KIN-SYS is necessary, novel, or superior. The conventional baseline must receive the same inputs, rules, failure conditions, and success criteria.

## 2. Scenario boundary

A trader seeks to move an agricultural consignment from one jurisdiction to another. The process may require trader identification, an origin-side permit or inspection, a border-side decision, logistics coordination, and confirmation of any applicable payment or fee. The exact legal steps and authority remain jurisdiction-specific and must be supplied by participating institutions; this document does not invent them.

Participants may include:

- **Trader or exporter:** submits a request and evidence for the consignment.
- **Origin-side public authority:** makes decisions within its own legal mandate.
- **Destination or border authority:** makes its own admissibility and clearance decision.
- **Private-sector participants:** logistics provider, bank or payment provider, and other authorised service providers.
- **Development partners:** may fund, convene, advise, or evaluate; funding alone does not confer authority to approve a consignment.
- **Independent reviewer or dispute body:** examines contested outcomes under the applicable governance and legal arrangements.

No participant automatically inherits another participant's authority. A shared protocol must not silently centralise legal decision-making.

## 3. Candidate end-to-end flow

1. **Request:** The trader submits a uniquely identified request with the minimum required evidence.
2. **Identity and purpose:** The receiving institution verifies the relevant credential and checks whether the requested disclosure is necessary and authorised.
3. **Origin-side decision:** The origin authority evaluates the request under its own mandate, applicable rules, and rule version.
4. **Evidence hand-off:** The protocol carries a verifiable statement of the origin-side result and its scope, not an unqualified claim that every external fact is true.
5. **Destination-side decision:** The destination authority independently evaluates the evidence under its own mandate and rules. It may accept, reject, request more information, or refer the matter for review.
6. **External services:** If a fee or payment is required, the relevant provider's result is handled through an explicit external-interaction contract. A timeout or acknowledgement alone must not be mistaken for proof that the institutional transition committed.
7. **Commitment and history:** Each accepted institutional transition has an explicit commitment boundary and an auditable link between the authoritative state and corresponding history.
8. **Notification and reconciliation:** Participants receive outcomes that distinguish received, pending, rejected, committed, and unknown states. Retries must not automatically create duplicate institutional effects.
9. **Dispute and correction:** Corrections occur through authorised, recorded procedures; history is not silently rewritten.

The flow is a test hypothesis. Actual sequencing must be adapted to the applicable law and the participating institutions' agreed process.

## 4. Component responsibilities and falsifiable hypotheses

| Component or capability | Candidate responsibility | What the experiment must establish |
|---|---|---|
| KIN-SYS protocol architecture | Define cross-domain message, authority, transition, evidence, governance, and dispute boundaries. | Independent institutions can interoperate without one institution or protocol operator silently acquiring all decision authority. |
| CRS / CISA / IVM / CEE | Trace requirements, instruction semantics, execution semantics, and operational behaviour as distinct but related layers. | The selected lifecycle and result semantics are consistent across the source versions and implementations being compared. |
| Identity and authority | Bind an action to a principal, mandate, permitted purpose, and current authority status. | Identity proof is not confused with legal authority; revocation and delegation are handled explicitly. |
| Privacy-preserving proofs | Where justified, prove a specific eligibility or attribute claim without disclosing unnecessary underlying data. | The proof's claim, trust assumptions, verifier, revocation model, and failure cases are explicit; a proof does not establish facts outside its defined claim. |
| MINAH AI | Optionally assist with document interpretation, missing-information checks, or explanations. | AI output is treated as untrusted assistance, is reviewable, and cannot bypass institutional rules or make an unauthorised final decision. Compare against a no-AI baseline. |
| $KIN or another economic mechanism | Consider only if a defined economic function is required by the use case. | The proposed mechanism improves a specified outcome and does not introduce unjustified fees, speculative incentives, duplicate settlement effects, or legal ambiguity. Compare against conventional payments/accounting. |
| Nexus Pods / edge infrastructure | Potentially support local execution or evidence handling during constrained connectivity. | Hardware, connectivity, key custody, data retention, operator privileges, offline limits, and reconciliation assumptions can be tested. No deployed hardware is assumed. |
| External systems and data providers | Supply payment, identity, inspection, logistics, or other scoped assertions. | Source accountability and uncertainty are preserved; external messages do not directly override authoritative state. |
| Governance and dispute handling | Define upgrades, emergency powers, appeals, and cross-domain disagreements. | There is an accountable path for conflicts, key compromise, mistaken decisions, and rule changes without silent alteration of prior commitments. |

A component may be unnecessary for this use case. The research must allow that conclusion.

## 5. Required failure and adversarial tests

| Test condition | Required observable outcome |
|---|---|
| Same request delivered repeatedly | At most one corresponding institutional commitment where the applicable semantics define the deliveries as duplicates; retries return the recorded outcome or an explicit unresolved status. |
| Same identifier reused with different request content | Explicit conflict; no silent mutation or reinterpretation of the original request. |
| Authority revoked before the decision commits | The transition is rejected or follows a specifically defined revocation race rule; the result must be deterministic and auditable. |
| Rule changes while a request is pending | The system records which rule version governs the decision and does not silently reinterpret committed history. |
| Origin-side approval exists but destination-side authority rejects | The two decisions remain distinct; origin approval does not override destination authority. |
| Payment provider times out after possible payment execution | The system represents uncertainty and reconciles against the provider's defined evidence; it does not blindly charge or commit twice. |
| Network fails before commitment | The last valid authoritative state remains valid; incomplete execution does not become a committed transition. |
| Network fails after commitment but before notification | Recovery/retry identifies the already committed result and does not create a second transition. |
| Edge node operates offline | Only explicitly permitted offline actions may proceed; actions requiring current external authority or data are deferred or handled under a defined policy. |
| AI produces a false or manipulated recommendation | The recommendation cannot bypass policy, authority checks, or human/institutional review required by the process. |
| Privacy proof is valid but its real-world source is wrong or revoked | The system distinguishes proof validity from source truth and handles revocation/dispute under the defined trust model. |
| Institutions disagree about a record or rule version | The system preserves evidence, identifies the disputed scope, and invokes an agreed resolution path rather than inventing a single truth. |
| Operator or key is compromised | The threat model identifies affected authority, revocation, containment, recovery, and audit procedures. |
| Infrastructure is replaced or restored | The recovered state/history relationship and institutional identity remain consistent with the specified continuity guarantees. |

## 6. Shared result vector

The KIN-SYS slice, IVM reference runtime, and conventional comparator should receive the same test cases. Record at least:

- scenario and input identifiers;
- pre-state and post-state for each institution;
- actor identity, authority scope, and authority outcome;
- applicable rule-set and rule version;
- external evidence identifiers and verification status;
- decision outcome and reason;
- commitment status and history reference;
- duplicate/replay outcome;
- recovery result and whether a second institutional transition occurred;
- privacy disclosure set and the purpose authorising each disclosure;
- latency, availability, and resource cost where measured;
- unresolved disputes or manual intervention required;
- expected outcome, actual outcome, and reproducible test artifact.

Do not collapse “message received,” “rule passed,” “payment provider replied,” “committed,” and “finalised” into one success flag.

## 7. Baselines and evaluation

At minimum, compare:

1. **Current-practice baseline:** the participating institutions' actual workflow, if it can be documented with permission and without exposing protected data.
2. **Conventional technical baseline:** a conventional API/workflow implementation with explicit authority, idempotency, transaction boundaries, audit records, and recovery handling.
3. **KIN-SYS candidate:** the same case and observable result vector implemented against the proposed protocol boundaries and the relevant CRS/CISA/IVM/CEE versions.

The conventional baseline must not be deliberately weakened. If it produces equivalent institutional outcomes with less complexity or cost, record that result. If KIN-SYS improves an outcome, identify which mechanism caused the improvement and reproduce it.

Evaluate three different questions:

- **Component validity:** does each component satisfy its declared scope?
- **Interface validity:** do components preserve the right semantics across failures and hand-offs?
- **System usefulness:** does the integrated approach measurably improve coordination, accountability, privacy, recovery, cost, or time relative to the baselines?

## 8. Legal, ethical, and operational constraints

Before a real-world pilot, participating institutions and qualified local experts must establish the applicable legal basis, permitted data uses, retention/deletion rules, responsibility for errors, appeal rights, cross-border data conditions, payment obligations, and procurement/security requirements. The prototype must use synthetic or appropriately authorised test data until those arrangements are settled.

Development partners can support common standards, convening, capacity-building, and independent evaluation; they should not be treated as default decision-makers. A protocol cannot create legal authority where none exists.

## 9. Pilot exit criteria

A limited pilot should not be called successful merely because a happy-path transaction completes. It should have:

- documented mandates, actors, trust assumptions, and source versions;
- reproducible success, rejection, duplicate, interruption, recovery, revocation, and dispute tests;
- independently checkable state/history evidence;
- explicit privacy and external-data assumptions;
- measured comparison against the conventional baseline;
- documented failures and manual workarounds;
- an independent review of the result vector;
- no unresolved critical authority, security, legal, or data-protection issue.

The output of the pilot may be a decision not to deploy KIN-SYS, not to use $KIN, not to use AI, or not to use edge hardware for that scenario. That is a valid research result.

## 10. Relation to CEE conformance

This use case does not expand the current SQLite prototype into a full CEE implementation. The local prototype tests only a narrow state transition and local transaction boundary. CEE-specific execution, commitment, history, recovery, verification, external-interaction, security, and deployment requirements must be mapped to the full CEE 1.0 Draft and tested separately.

See [CEE 1.0 Full-Source Conformance Trace](CEE_CONFORMANCE_TRACE.md) and [Source Crosswalk](SOURCE_CROSSWALK.md).
