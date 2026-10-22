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

Nexus Pods are intended to include an inbuilt HSM and the local compute capability to generate and verify zero-knowledge proofs. The HSM protects keys and performs supported cryptographic operations; proof generation is performed by the Pod's compute environment, not assumed to be an HSM capability. Where a proof can safely replace disclosure of raw inputs, the node should transmit the proof and minimum required public inputs/metadata. The protocol must bind a proof to its statement, case/operation, public inputs, circuit/proof-system version, relevant rule version, and freshness/replay state. Verification failure, unknown versions, stale evidence, or unavailable required verifiers must follow an explicit reject/defer policy.

Proof-based exchange is not a zero-storage architecture. The deployment must identify what must remain locally or centrally available: for example commitments, nullifiers or equivalent replay-prevention state, proof references, circuit and rule versions, revocation/freshness data, minimal audit events, and legally required records. Resource usage and storage/bandwidth reduction must be measured against a fair baseline. These requirements are architectural direction; no production HSM integration or zero-knowledge pipeline is claimed by this draft.

## 6. Economic effects

No token, reward, fee, or balance-changing operation is conformant unless it has a defined authority source, unique effect identifier, accounting rule, duplicate protection, reconciliation path, and dispute handling. Tokenisation alone does not prove ownership, legal title, asset existence, valuation, or enforceable redemption.

The semantics and role of $KIN remain an open design decision. This specification does not define a supply, allocation, exchange rate, or financial promise.

## 7. AI-assisted components

MINAH is intended as the jurisprudential oracle and sovereignty gatekeeper for a configured jurisdiction or governing domain. She evaluates applicable law and institutional rules against case evidence and produces a traceable disposition. Jurisdiction-specific training/adaptation, authoritative source provenance, effective-date handling, conflict and uncertainty treatment, and acceptance evaluation must be specified. The protocol must not rely on MINAH's voluntary compliance to prevent bypass: IVM/CEE-informed execution and the wider KIN-SYS controls must enforce mandatory transition and authority constraints even if MINAH returns an incorrect or malicious result. MINAH cannot rewrite her own governing rules, grant herself authority, or directly commit protected state. Decisions reserved by law to an independent institution or official must remain with that authority.

## 8. Governance and upgrades

A deployment must define who may propose, approve, sign, and activate changes; version compatibility; emergency powers and auditability; key-compromise and authority-revocation procedures; and how the rules and software version behind an outcome can be verified.

## 9. Conformance and open decisions

A conforming implementation must pass tests for its declared profile and disclose unsupported requirements. Wire format, cryptographic profiles, trust-root distribution, node discovery, coordination/consensus, replication, clock assumptions, key lifecycle, privacy-preserving proofs, governance quorum, economic model, and deployment profiles remain undecided.
