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
Data collection and disclosure must be limited to a declared purpose and authorised recipient. Auditability does not justify unrestricted disclosure of personal data.

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

## 6. Economic effects

No token, reward, fee, or balance-changing operation is conformant unless it has a defined authority source, unique effect identifier, accounting rule, duplicate protection, reconciliation path, and dispute handling. Tokenisation alone does not prove ownership, legal title, asset existence, valuation, or enforceable redemption.

The semantics and role of $KIN remain an open design decision. This specification does not define a supply, allocation, exchange rate, or financial promise.

## 7. AI-assisted components

AI may assist with interpretation, routing, analysis, or recommendations only within an explicitly bounded role. Consequential institutional transitions must pass through deterministic policy and authority checks. MINAH AI is conceptual; its permissions, reliability envelope, evaluation method, and prohibited actions remain to be specified.

## 8. Governance and upgrades

A deployment must define who may propose, approve, sign, and activate changes; version compatibility; emergency powers and auditability; key-compromise and authority-revocation procedures; and how the rules and software version behind an outcome can be verified.

## 9. Conformance and open decisions

A conforming implementation must pass tests for its declared profile and disclose unsupported requirements. Wire format, cryptographic profiles, trust-root distribution, node discovery, coordination/consensus, replication, clock assumptions, key lifecycle, privacy-preserving proofs, governance quorum, economic model, and deployment profiles remain undecided.
