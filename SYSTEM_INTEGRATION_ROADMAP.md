> **Authorship & rights:** Joel Kagoro · Copyright © 2026 Joel Kagoro · Licensed under Apache-2.0; see `LICENSE`, `NOTICE`, and `COPYRIGHT.md`.

# KIN-SYS Whole-System Integration Roadmap

**Status:** Working architectural baseline, draft 0.1  
**Purpose:** Make the complete intended KIN-SYS system explicit and use that whole-system model—not the convenience of individual components—as the organising principle for specification, implementation, testing, and evaluation.

## 1. Governing decision

KIN-SYS is developed and evaluated as one protocol system. Component implementations are subordinate to system-level contracts. A component can be mocked, simulated, unavailable, or not yet built, but its intended place in the system, authority, inputs, outputs, failure states, dependencies, and evidence status must remain visible.

The repository already contains valuable component experiments and an initial integrated demonstrator. Those experiments are retained. The next phase must not treat the existing demonstrator's current boundaries as the final architecture, nor expand each component independently and hope that interfaces will fit later.

**System-first does not mean building every physical and software component before anything can be tested.** It means first defining the end-to-end system contract and representing all intended capabilities in a coherent executable model. Simulators stand in for expensive or unfinished components while the same system-level interfaces and invariants are exercised. As real components mature, they replace adapters without changing the meaning of the overall protocol.

## 2. The system KIN-SYS is intended to be

KIN-SYS is a sovereign, distributed protocol and execution environment through which independently operated people, institutions, services, economic systems, and edge nodes can coordinate under explicit authority, evidence, policy, state, and accountability rules.

It is not merely an application that calls several APIs. Nor is it necessarily one blockchain, one database, one AI model, or one hardware design. Its purpose is to establish the common protocol obligations that allow heterogeneous technologies and organisations to interoperate without giving any one component unbounded authority.

The system must be capable of specifying:
- who or what is participating, in what capacity, for which purpose, and under whose authority;
- what request, evidence, rule version, and prior state an operation depends on;
- how advice, external facts, institutional decisions, and deterministic execution remain distinguishable;
- how local and cross-domain state changes are authorised, committed, traced, retried, disputed, corrected, and recovered;
- what can happen when connectivity, authority services, data sources, payment providers, AI services, power, or a node becomes unavailable;
- how sovereign control, privacy, governance, operations, and portability are evidenced rather than merely asserted.

## 3. Whole-system logical architecture

The following are logical responsibilities, not a claim that every item is implemented or must be a separate microservice. Deployment may combine or separate them as appropriate, provided the protocol's trust boundaries and contracts remain intact.

| System responsibility | Role in the whole system | Required contract / boundary |
|---|---|---|
| 1. Participants and institutions | People, businesses, agencies, operators, service providers, and their mandates | Identifiable principal, declared role/purpose, accountable decision-maker, dispute route |
| 2. Sovereign identity and authority | Identity references, credentials, delegation, scopes, expiry, revocation, key custody | Identity is not authority; authority is operation-, scope-, purpose-, and time-dependent |
| 3. Protocol ingress and communication | Requests, messages, schema/version checks, routing, audience, replay and correlation controls | Versioned envelope, authentication, integrity, audience, expiry, operation ID, rejection reasons |
| 4. System context and orchestration | Case lifecycle, dependencies, sequencing, policy/profile selection, cross-component coordination | Explicit prerequisites and lifecycle states; no hidden success based on call order |
| 5. Evidence and external facts | Documents, attestations, registries, sensors, oracles, provenance and challenges | A stored assertion is not automatically a true real-world fact; source, freshness, confidence and dispute must be represented |
| 6. Rules, law and institutional policy | Versioned rules, jurisdiction, policy sources, exemptions, deferment and effective dates | Authoritative source and version must be identified; synthetic/demo rules are visibly non-authoritative |
| 7. MINAH AI cognitive assistance | Evidence organisation, explanation, retrieval, anomaly/missing-information detection and recommendations | Model/version and source references; uncertainty and limits; output is untrusted advice and cannot grant authority or commit protected state |
| 8. Deterministic execution (CEE/IVM-informed) | Evaluate authorised transitions, preconditions, state changes, lifecycle and recovery semantics | Explicit input context and pinned versions; reproducible decision semantics within the declared profile; traceable state/history |
| 9. State, history and commitments | Persist state and corresponding events, idempotency, recovery, audit and proofs | Declared commitment boundary; conflicting reuse rejected; state/history consistency; recovery behaviour specified |
| 10. Institutional coordination | Preserve independent institutional mandates while coordinating cross-domain cases | Each institution decides within its own authority; another institution, protocol coordinator, or AI cannot impersonate or override it |
| 11. Economic accounting and statutory remittance | Obligations, fees, taxes, incentives, payments, settlement evidence, refunds, reconciliation | Distinguish legal obligation from calculation, instruction, provider acknowledgement, settlement and reconciliation; duplicate-effect controls |
| 12. Distributed network and deployment profiles | Central, regional, institution-operated, intermittent edge, and multi-domain operation | Message delivery assumptions, trust domains, replication/coordination model, freshness and failure modes declared |
| 13. Nexus Pods / physical edge infrastructure | Potential edge compute, protected keys, local storage, connectivity, power and cooling | Hardware is a deployment substrate, not authority; hardware, energy, thermal, network, physical security, maintenance and lifecycle constraints are specified |
| 14. Governance, disputes and upgrades | Rule changes, software releases, emergency powers, appeals, corrections, key incidents and exit | Who may propose/approve/activate; separation of duties; old decisions remain auditable; version migration is explicit |
| 15. Privacy and security controls | Data minimisation, purpose limitation, access, encryption, retention, incident response | Threat model and proof obligations; no cryptographic/privacy claim without implementation and evaluation evidence |
| 16. External adapters and user-facing tools | Institutional systems, payment rails, registries, AI providers, operator consoles and applications | Adapters cannot silently change protocol meaning; capability, version, provenance, errors and unsupported states are explicit |
| 17. Economic mechanism / $KIN (unresolved) | Potential fee, incentive, access, governance or settlement mechanism only if justified | No token dependency by default; define purpose, accounting, legal basis, abuse model and comparative necessity before implementation |
| 18. Conformance and evidence | Whole-system scenarios, component contracts, adversarial tests, baseline comparison and deployment acceptance | Every guarantee links to an invariant, test/evidence, assumptions, limitations, and status |

### Enabling research, not substitute systems

- **CEE and IVM** inform execution environment, transition, lifecycle, commitment, and recovery semantics. They do not replace the broader KIN-SYS architecture.
- **CRS and CISA** remain distinct source artifacts whose exact requirements and version relationships must be reconciled against the whole-system contract.
- **MINAH AI** is a proposed cognitive layer. It may be developed later or replaced by another implementation, but the protocol must define its safe role now.
- **Nexus Pods** are a proposed physical edge deployment form. Their system interfaces and constraints must be represented now; physical engineering and procurement can proceed when requirements, partners, and resources are available.
- **Kagoro DSL** remains a separate project and is not a mandatory KIN-SYS component.
- **$KIN** is not assumed necessary. Its inclusion must be justified by a system-level use case and comparison with simpler accounting/settlement mechanisms.

## 4. One shared end-to-end operation model

Every consequential operation, regardless of which component receives it, should be traceable to a common logical context. The wire encoding remains undecided; this is the semantic contract that implementations must preserve.

A case/operation context should identify, where applicable:
- protocol and contract version;
- stable case, operation, and causal/prior-state references;
- authenticated principal, acting node, delegated role, authority basis, and requested purpose;
- intended recipient/audience and applicable trust domain;
- evidence references, provenance, integrity/freshness state, and disclosure classification;
- applicable jurisdiction, policy/rule-pack and version, execution profile, and effective-time assumptions;
- MINAH request and output references, model/adapter version, source citations, uncertainty, and human/institutional disposition;
- institutional decision references, stage, authorised actor, and current validity basis;
- calculated obligation references and input amounts/units;
- economic effect ID, payment instruction/provider evidence/settlement/reconciliation state, if applicable;
- state/history commitment reference, outcome status, outstanding blockers, and recovery/reconciliation requirement.

Only relevant fields should be included in a specific message. Do not put secrets, full identity documents, or unnecessary personal data into a generic envelope. The contract is not a requirement to centralise all data in one database: references and proofs may cross system boundaries, subject to explicit trust and availability rules.

## 5. End-to-end lifecycle and failure semantics

A typical protected operation should follow this conceptual lifecycle. A use case can omit irrelevant stages, but must not invent a successful result for an unmet prerequisite.

1. **Initiate:** a participant submits a uniquely identified request and declared purpose.
2. **Validate:** validate schema/version, identity, audience, integrity, replay, expiry and input constraints.
3. **Establish context:** select applicable authorities, jurisdiction, policy/rule versions, evidence requirements and execution/deployment profile.
4. **Resolve evidence:** obtain or verify required evidence and record provenance, freshness, conflicts and missing information.
5. **Assist, where appropriate:** MINAH or another advisory service may analyse evidence; its result is versioned, cited, uncertainty-aware and non-authoritative.
6. **Authorise:** check current authority and operation-specific permissions at the protected action boundary.
7. **Evaluate:** deterministic policy/transition execution evaluates preconditions against explicit inputs and pinned versions.
8. **Obtain independent decisions:** each institution that has a mandate makes its own decision; one approval cannot substitute for another.
9. **Assess economic consequences, if applicable:** calculate only from approved inputs and authoritative or explicitly synthetic rules; preserve calculation provenance.
10. **Execute external effects, if applicable:** issue stable instructions, distinguish acknowledgements from settlement, and define unknown/timeout/reconciliation behaviour.
11. **Commit and record:** persist state and required history according to the declared commitment boundary; retries must not create duplicate effects.
12. **Synchronise and reconcile:** edge or remote work is accepted, deferred, rejected, or reviewed according to its declared offline authority; stale evidence and conflicting updates are surfaced.
13. **Finalise or remain unresolved:** expose a reasoned final, rejected, deferred, pending, or unknown result; never collapse these into a generic success boolean.
14. **Challenge and recover:** allow attributable correction, appeal, dispute, revocation, compensation, and recovery without silently rewriting prior history.

Failure is a system event, not merely an exception in the component that noticed it. Each adapter failure must map to a defined whole-system outcome and determine which later actions are forbidden, permitted, or deferred.

## 6. System-level invariants

These invariants govern component and integration work. They are proposed acceptance conditions until the listed evidence exists.

- **SYS-001 — Authority:** no protected effect without valid authority for the operation, scope, purpose, and required freshness.
- **SYS-002 — Independent mandates:** no institution, AI, node operator, or coordinator silently assumes another institution's authority.
- **SYS-003 — Evidence provenance:** consequential outcomes identify the evidence and rule versions they rely on; stale, conflicting or unverifiable evidence is not silently normalised.
- **SYS-004 — Shared operation identity:** retries of the same semantic operation are idempotent; conflicting reuse is rejected.
- **SYS-005 — State/history consistency:** a committed transition and its required history cannot silently diverge under the declared failure model.
- **SYS-006 — Explicit status:** received, accepted, committed, final, rejected, deferred, pending, and unknown have distinct meanings.
- **SYS-007 — No invented external truth:** AI, a ledger, an oracle, a provider acknowledgement, or an edge queue cannot alone establish a real-world fact outside its declared authority.
- **SYS-008 — Economic effect integrity:** obligations, instructions, settlements, refunds and reconciliations remain distinct, attributable and protected against duplicate effects.
- **SYS-009 — Bounded offline operation:** offline nodes can perform only declared offline-safe actions; other actions remain deferred or require reconciliation.
- **SYS-010 — Privacy by purpose:** only necessary information is disclosed to an authorised recipient for a declared purpose.
- **SYS-011 — Version coherence:** protocol, adapter, schema, rule, model and deployment versions used by an outcome are traceable; version drift has explicit semantics.
- **SYS-012 — Failure propagation:** a component failure or uncertainty blocks, defers or changes downstream actions according to the use-case contract; it must not be converted into success by orchestration.
- **SYS-013 — Replaceable implementations:** a component can be replaced by another conforming implementation without changing the protocol's semantic obligations.
- **SYS-014 — Deployment accountability:** control of keys, policies, data, software updates, hardware, energy, network, and physical access is assigned and auditable.
- **SYS-015 — Falsifiability:** integrated results are compared with a fair conventional baseline, and evidence may show a KIN-SYS feature is unnecessary or inferior for a use case.

## 7. Integrated validation scenario

Retain the agricultural consignment scenario as the first shared test case, but treat it as one system trace rather than a tax/payment feature test.

The trace should make visible:
- exporter/actor identity and scoped authority;
- evidence and its source/freshness;
- MINAH advice and its limitations;
- independent origin and destination decisions;
- execution context and rule versions;
- the point at which an obligation is legally and procedurally permitted to exist;
- economic instruction, provider response, settlement and reconciliation;
- a central deployment and a simulated Nexus Pod/edge deployment;
- connectivity loss, stale data, revocation, version drift, AI outage/uncertainty, payment uncertainty, correction and restart;
- the state and history each participant can see, and what they are not permitted to see;
- why the case is final, rejected, deferred, pending, or unresolved.

Additional scenarios must be chosen to challenge the protocol's generality, not merely to add more test cases to the same business process. Candidate domains include offline public-service delivery, regulated supply chains, asset/infrastructure records, and cross-institution payments; each requires its own stakeholder and legal validation.

## 8. Representation of unfinished components

Every component must have one of these explicit implementation statuses:

- **Specified:** purpose and obligations documented.
- **Interface modelled:** request, response, authority, version and failure contracts represented.
- **Simulated:** executable substitute exercises the same declared contract but does not perform the real-world function.
- **Prototype:** a real implementation exists for a limited profile and has stated limitations.
- **Integrated:** exercised in at least one end-to-end scenario through the shared contract.
- **Independently validated:** an independent reviewer has reproduced or evaluated the evidence.
- **Production accepted:** a specific deployment profile has passed its legal, security, operational, performance, governance and acceptance gates.

A component may occupy different statuses for different profiles. Never upgrade a status based only on a document, diagram, passing unit test, mock response, or successful local demonstration.

## 9. Development sequence: system first, then implementation depth

### Gate A — Whole-system contract
Reconcile the existing architecture, specification, use cases, interface contracts, invariants, threat model, research lineage, and status inventory into one consistent model. Identify contradictions, missing actors, unowned responsibilities, and decisions that remain open. No new component feature work should outrun this reconciliation.

### Gate B — Executable system model
Represent the full end-to-end case and every major logical component in a single runnable demonstrator. Use explicit adapters for simulated identity, MINAH, institutional policy, statutory rules, payment providers, edge resources, governance and evidence. The orchestrator must consume contract responses, not call component internals or infer success from expected call order.

### Gate C — Cross-component challenge suite
Test global invariants across combinations of components: revoked authority plus restart; stale rule plus queued edge work; AI uncertainty plus missing evidence; payment timeout plus retry; inconsistent source evidence plus institutional disagreement; component outage plus recovery; schema/version mismatch across adapters. Assert both expected state and forbidden downstream effects.

### Gate D — Fair baseline and falsification
Run the same use cases and failure vectors against a well-engineered conventional architecture with independently implemented components. Compare correctness, recovery, operability, integration burden, complexity, cost assumptions, sovereignty/control and user/institutional outcomes where measurable. Do not infer superiority from equal outcomes or from test counts.

### Gate E — Replace simulations in dependency order
Prioritise real components only after their interface, acceptance criteria, deployment requirements, and resource needs are clear. Candidate work includes real cryptographic identity/authority, a tested MINAH model under a non-authoritative contract, authoritative rule adapters, provider sandbox integration, and a physical edge/Nexus Pod profile. Hardware planning must account for compute/GPU choice, power generation and storage, thermal envelope, networking, secure boot/key custody, ruggedisation, maintenance, supply chain, and total cost—not GPU selection alone.

### Gate F — Independent challenge and deployment profiles
Invite institutions, implementers, security reviewers, and researchers to challenge the whole system. Record unresolved objections and update the protocol when evidence contradicts the design. No production claim until the specific deployment profile passes its acceptance gates.

## 10. Repository alignment rules

1. This document sets the integration direction; `SPECIFICATION.md` defines protocol semantics; `ARCHITECTURE.md` defines logical boundaries; `INVARIANTS.md` maps system invariants; `docs/INTEGRATED_INTERFACE_CONTRACTS.md` defines demonstrator adapter contracts; implementation and test reports provide evidence.
2. If documents conflict, do not silently choose whichever supports current code. Record the contradiction and resolve it through a documented decision.
3. Every new component feature must identify its contribution to at least one system-level use case and invariant, plus its effect on adjacent components and failure behaviour.
4. Every adapter must have contract tests for success, refusal, malformed input, timeout/unavailability, version mismatch, stale/revoked authority where relevant, retries, and recovery.
5. System-level regression tests must run when a component contract changes.
6. Retain conventional baselines and negative results. The protocol may prove unnecessary or more costly for a particular use case.
7. Keep the status of physical infrastructure, AI models, live institutional integrations, legal rules, and real money effects honest and explicit.

## 11. Immediate engineering work

The first alignment step is now committed: `protocol-reference/system_model.py` inventories 19 logical responsibilities with explicit status/dependencies/limitations, both central and disconnected-edge outputs expose that inventory, and the demonstrator emits a validated case-linked trace for selected components. The trace validator checks structure and case linkage only; the trace is not persisted, distributed, or independently verified. The 116-test suite passes in [run 39753822785](https://github.com/joe933212-pixel/KIN-SYS-PROTOCOL/actions/runs/39753822785).

The next implementation step is to audit the current `integrated_demo.py` against this whole-system contract and refactor the demonstrator so that:
- each logical component has a typed/versioned adapter boundary, even when backed by a mock;
- the orchestration layer depends only on contract responses;
- all major components appear in a machine-readable system trace with status, provenance, versions and limitations; (a first partial trace exists; broaden and validate it as contracts mature)
- missing, stale, contradictory or unavailable component responses propagate to a defined case outcome;
- cross-component invariants are exercised in the same tests;
- the conventional baseline is compared on the same external contract and observable outcomes.

This step may require revising current interfaces and tests. Such revisions are expected: the system contract is the organising authority, while existing component experiments are evidence and implementation inputs—not fixed architecture by default.
