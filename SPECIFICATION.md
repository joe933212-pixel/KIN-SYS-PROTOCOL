> **Authorship & rights:** Joel Kagoro · Copyright © 2026 Joel Kagoro · Licensed under Apache-2.0; see `LICENSE`, `NOTICE`, and `COPYRIGHT.md`.

# KIN-SYS Protocol Specification

**Status:** Draft 0.1 — foundation, not a final wire protocol.

## 1. Purpose and scope

Define a protocol architecture through which independently operated nodes and institutions exchange authenticated requests, evaluate authority and rules, commit state transitions, retain verifiable histories, and recover safely from faults.

The foundation covers node identity; institutional principals, authority and revocation; request/event envelopes; deterministic rule evaluation; commitment, history, replay and recovery; inter-domain messages; economic-effect accounting; privacy; governance; protocol evolution; and conformance testing.

It does not yet define a production wire encoding, consensus algorithm, cryptographic suite, token contract, national identity integration, hardware specification, or legal settlement mechanism. Those choices require explicit decisions and tests.

## 2. Design principles

### Explicit authority
Every protected operation must be attributable to a principal and an authority basis. Authentication alone is not authorisation. A valid identity does not imply unlimited authority.

### Deterministic transitions
Given the same validated input, authorised context, rule version, and prior state, a conforming deterministic evaluator should produce the same decision and transition description. Nondeterministic services must be isolated behind declared interfaces; material outputs become explicit inputs to consequential decisions.

### Atomic meaning
A transition must not be reported as complete if its required state commitment and corresponding history record are inconsistent. Every intermediate commitment state needs defined recovery behaviour.

### Idempotency and replay control
Retries must not create duplicate institutional or economic effects. Each operation needs a defined identity, deduplication rule, and response to conflicting reuse.

### Privacy by purpose
Data collection and disclosure must be limited to a declared purpose and authorised recipient. Auditability does not justify unrestricted disclosure of personal data. Where suitable, the protocol should support proof-backed claims that allow a verifier to check a defined condition without receiving the underlying private inputs. A proof does not establish the truth of an external input or the correctness of the encoded policy; source provenance, circuit/rule review, and the trusted-input boundary remain explicit.

### Sovereignty through control and portability
A deployment should identify who controls keys, policy, data, software updates, physical infrastructure, and exit/portability. Sovereignty is a measurable set of operational and governance properties, not a marketing label.

### Failure is part of the protocol
Disconnection, clock skew, unavailable authorities, revoked credentials, storage faults, malformed messages, partial commitment, and conflicting updates require specified outcomes.

## 3. Proposed logical message envelope

The final encoding remains undecided. A request/event envelope should define, as applicable: protocol/schema version; unique operation identifier; issuing principal and authenticated node; intended audience; operation type and declared purpose; applicable authority and rule version; causal or prior-state reference; payload or payload commitment; validity constraints; integrity/authentication material; and privacy classification.

Fields must be minimised for each message type. This is a design checklist, not a frozen schema.

## 4. Canonical transition lifecycle

A protected transition is expected to follow explicit validation, authority evaluation, rule evaluation, durable commitment, history attachment, and finalisation. The exact state machine must be defined independently of any one implementation.

The IVM research archive describes a prototype lifecycle of PREPARED → STATE_COMMITTED → HISTORY_ATTACHED → FINALIZED and experiments on process-death recovery. This is relevant evidence for a local commitment problem; it does not establish distributed atomicity, power-loss durability, concurrency safety, or production readiness.

## 5. Edge and disconnected operation

Each operation class must declare whether it is online-required, offline-safe under a bounded pre-authorised policy, offline-deferred pending reconciliation, or prohibited offline. Local acceptance must not be misrepresented as final settlement. Reconciliation must specify conflict handling, replay protection, expiry, audit records, and rejection or compensation paths.

Where a use case employs zero-knowledge proofs, the protocol should define proof-verification obligations, statement and case/operation binding, public inputs, circuit/proof-system and relevant rule versions, and freshness/replay behaviour. Verification failure, unknown versions, stale evidence, or unavailable required verifiers must follow an explicit reject/defer policy. These are protocol semantics and do not require a Nexus Pod.

Nexus Pods are one optional compatible hardware implementation intended to combine an inbuilt HSM with local compute for proof generation and verification. HSM key protection and proof generation are separate responsibilities. Proof-based exchange can reduce raw-data disclosure, data movement, and central storage, but it does not imply zero storage: deployments may still need commitments, replay-prevention state, proof references, version metadata, revocation/freshness data, minimal audit events, and legally required records. The Pod is not a mandatory protocol node or a deployment constraint; no production HSM integration or zero-knowledge pipeline is claimed by this draft.

## 6. Economic effects

No token, reward, fee, or balance-changing operation is conformant unless it has a defined authority source, unique effect identifier, accounting rule, duplicate protection, reconciliation path, and dispute handling. Tokenisation alone does not prove ownership, legal title, asset existence, valuation, or enforceable redemption.

The semantics and role of $KIN remain an open design decision. This specification does not define a supply, allocation, exchange rate, or financial promise.

## 7. AI-assisted components

MINAH is intended as the jurisprudential oracle and sovereignty gatekeeper for a configured jurisdiction or governing domain. She evaluates applicable law and institutional rules against case evidence and produces a traceable disposition. Jurisdiction-specific training/adaptation, authoritative source provenance, effective-date handling, conflict and uncertainty treatment, and acceptance evaluation must be specified. The protocol must not rely on MINAH's voluntary compliance to prevent bypass: IVM/CEE-informed execution and the wider KIN-SYS controls must enforce mandatory transition and authority constraints even if MINAH returns an incorrect or malicious result. MINAH cannot rewrite her own governing rules, grant herself authority, or directly commit protected state. Decisions reserved by law to an independent institution or official must remain with that authority.

## 8. Governance and upgrades

A deployment must define who may propose, approve, sign, and activate changes; version compatibility; emergency powers and auditability; key-compromise and authority-revocation procedures; and how the rules and software version behind an outcome can be verified.

## 9. Deployment profiles and hardware independence

KIN-SYS is software-first and hardware-independent. Conforming implementations may run on suitable existing institutional infrastructure; no proprietary appliance, Nexus Pod, HSM, TEE, or zero-knowledge capability is mandatory for protocol participation. Hardware-backed security is an optional deployment control when required by a declared threat model. It does not replace core protocol checks.

Deployments should declare one of the profile targets in [Deployment Profiles](DEPLOYMENT_PROFILES.md): standard software; constrained connectivity and power; or optional hardware-enhanced security. All preserve the same core protocol obligations. The constrained profile must define which operation classes are safe offline, deferred, or prohibited, plus persistence, freshness, reconnection, conflict and recovery behaviour. The hardware-enhanced profile must state the precise security boundary, supported operations, failure behaviour, and evidence. These are design targets, not certified products.

Zero-knowledge proofs are optional. Where used, a deployment must define the statement, public inputs, proof/circuit and rule versions, source provenance, replay/freshness controls, verifier failure semantics and dispute path. Any storage or bandwidth savings must be measured against a fair baseline; proofs do not establish external-data truth and do not eliminate all retention obligations.

## 10. Conformance and open decisions

A conforming implementation must pass tests for its declared profile and disclose unsupported requirements. Wire format, cryptographic profiles, trust-root distribution, node discovery, coordination/consensus, replication, clock assumptions, key lifecycle, privacy-preserving proofs, governance quorum, economic model, and deployment profiles remain undecided.
