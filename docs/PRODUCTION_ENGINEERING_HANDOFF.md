# KIN-SYS Production Engineering Handoff

**Status:** Engineering handoff baseline 0.1 — work in progress  
**Purpose:** Turn the KIN-SYS architecture into a buildable, testable production programme with explicit interfaces, ownership, dependencies and acceptance gates.

> **Critical status distinction:** The repository contains a tested research prototype and synthetic integrated demonstrator, not a production service. The OpenAPI document at `api/openapi.json` defines proposed target routes. These routes are not currently exposed by a live HTTP server. A route is not “connected” merely because it appears in a specification.

## 1. Definition of done

A component is ready for handoff only when its engineering package includes:

- A versioned request/response schema, operation identifiers, idempotency and error semantics.
- An owner, dependencies, trust boundary, data classification and threat model.
- A working implementation or explicitly labelled simulator behind the same interface.
- Positive, negative, concurrency, retry, timeout, interruption, restart and recovery tests appropriate to its risk.
- Logs, metrics, traces, audit events, health/readiness checks and operational runbooks.
- Configuration, secret/key handling, migrations, deployment instructions and rollback strategy.
- Acceptance evidence linked to a reproducible CI run, plus a list of unsupported guarantees.

Production readiness is a deployment-specific acceptance decision. Passing unit tests alone is not sufficient.

## 2. API contract and integration rule

Architecture clarification: the public protocol path is MINAH -> CEE -> final decision. CEE is the single execution boundary, including its execution semantics and runtime environment; internal implementation details must not be designed or exposed as independent execution paths. Separate SQLite stores in a research prototype are an implementation limitation, not a target architecture requirement. The proposed route inventory is in [`api/openapi.json`](../api/openapi.json). The route-by-route [implementation matrix](../api/implementation-matrix.json) names the owning team, dependencies, route-specific requirement and acceptance gate for each operation. It covers system health/capabilities, identity and authority, cases and institution-owned decisions, MINAH dispositions, evidence and rule packs, obligations and payments, reconciliation, edge queues, audit history and governance changes.

The OpenAPI contract is deliberately marked `CONTRACT_DRAFT_NOT_ROUTED`. Before implementation, the API team must review resource names, schemas, authentication, status codes, pagination, rate limits, idempotency retention, webhook authentication, data minimisation, compatibility and error conventions. No external system should be called directly from arbitrary application code: each integration must sit behind a named adapter and emit the shared trace/correlation metadata.

**Shared invariants for every protected operation:** authenticated principal; action-specific authority; declared purpose; pinned rule and policy context; operation/idempotency identity; freshness and revocation checks; explicit result status; durable event/state relationship; traceable evidence; no implicit promotion of unknown or simulated results to success.

**Ingress order is a protocol invariant:** MINAH is the first protocol-facing recipient of each citizen, operator, enterprise, or machine instruction. Before handing an instruction to CEE, MINAH must validate the input/channel, verify identity through an authorised registry API, normalise the requested action, pin jurisdiction and authoritative law/rule versions, verify action-specific authority/scope/validity/delegation, and complete required preliminary evidence and policy checks. She then emits a traceable, integrity-protected handoff envelope. CEE accepts the envelope, maintains runtime context, lifecycle/orchestration and inspection, enforces applicable execution semantics, and returns the authoritative final decision. This is one execution boundary; it must not blindly trust an unchecked MINAH assertion. Voice, caller ID, or a spoken name alone is not identity proof. Rejections and deferrals must also be traceable at the MINAH ingress. Current research endpoints do not yet satisfy this production invariant.

## 3. Workstream ownership and build packages

| Workstream | Build package | Contract and test obligations | Exit gate |
|---|---|---|---|
| Protocol/runtime | Canonical envelope, transition engine, version negotiation, idempotency, event/history, failure/recovery | Deterministic state transitions; conflicting ID reuse; atomic state/history; crash recovery; concurrency; schema compatibility | Independent implementation passes conformance suite |
| Identity, authority and security | Principal/node identities, credentials, delegation, scopes, revocation, key lifecycle, trust roots | Authentication is separate from authorisation; fail-closed protected actions; expiry/revocation races; rotation/compromise/recovery; audit | Threat-model review and tested key/credential lifecycle |
| MINAH AI engineering | MINAH-first ingress/gateway; authorised national or enterprise identity-registry API adapter; jurisdiction profile; authoritative source ingestion; retrieval/provenance; model gateway; structured disposition and signed/integrity-protected handoff envelope; evaluation and human escalation | Identity proof and registry failure/staleness; separate identity vs action-specific authorisation; source/effective-date traceability; conflicting law; missing evidence; uncertainty; prompt injection; model drift; unavailable model; replay/tampering of handoff; no self-authorisation or direct state commitment | Jurisdiction-specific evaluation thresholds approved by legal/domain owners and safety reviewers; identity and authority gates pass adversarial tests |
| Legal/rule engineering | Versioned rule packs, source register, effective dates, exemptions, calculation engine, approval and activation workflow | Golden legal cases; arithmetic/rounding; conflicting/expired rules; pinned case versions; authorised activation and rollback | Competent domain owner signs off on rule pack and test corpus |
| Institutional integration | Institution-specific adapters, decision ownership, callbacks, correction/dispute and appeal workflows | Each institution remains authoritative for its own decisions; delayed/reordered messages; correction history; authority changes during a case | Each institution's integration contract and conformance evidence accepted |
| Fintech/payment engineering | Provider adapters, webhook verification, payment state machine, settlement evidence, reconciliation, refund/reversal/dispute workflows | Duplicate/late callbacks; signature verification; amount/currency binding; idempotent instructions; uncertain outcomes; no blind resubmission; source-sequenced reconciliation | Sandbox and provider certification complete; reconciliation independently tested |
| Evidence and privacy | Evidence metadata/store, digests, access policy, retention, encryption, privacy controls, proof pipeline if justified | Provenance/freshness; tamper detection; purpose-bound access; retention and deletion policy; proof version/freshness; external facts remain separately verified | Privacy/security review and measured proof/storage trade-offs |
| Distributed systems/network | Node identity, signed messages, delivery/retry, replication or consensus decision, partition handling, clock assumptions | Duplicate, loss, reordering, partitions, stale leaders/authority, conflicting writes, recovery and replay | Multi-process/multi-node fault-injection suite passes declared profile |
| Nexus Pod/hardware | Separate hardware profile, power/thermal/network budget, HSM/key custody, secure boot/updates, storage and telemetry; optional proof acceleration | Power loss, tampering, replacement, key provisioning/rotation, degraded operation, attestation assumptions; benchmark proof workloads | Hardware threat model, lab acceptance and software/hardware interface conformance |
| DevSecOps/SRE | Reproducible builds, CI, artifact provenance, secret management, deployment manifests, monitoring, backups, incident response | SBOM/dependency scanning, signed releases, restore drills, SLOs, alerting, rollback and incident runbooks | Deployment rehearsals and operational readiness review |
| Client/API developer experience | SDKs, generated clients, examples, sandbox, operator/admin tooling | Contract tests, clear error states, no bypass of authority rules, version compatibility | External team implements a client against the documented API without private knowledge |

## 4. What is implemented versus what remains

### Existing executable research

- Local SQLite transition and institutional-workflow models, with event identity, authority/rule checks and selected recovery cases.
- Synthetic integrated workflow with separate institutional stages, deterministic MINAH stand-in, fictional statutory assessment, simulated payment lifecycle and disconnected-edge queue.
- Payment lifecycle reference model with durable obligation/instruction records, acknowledgement/settlement separation, rollback tests, retry protection and source-sequenced reconciliation.
- Independent conventional comparison implementations and matched observable-result tests for selected scenarios.
- Machine-readable component response and deployment-profile contracts.

### Not yet connected as production infrastructure

- A production HTTP API server: **not implemented by the OpenAPI file**.
- Production authentication, cryptographic request signing, identity registry/NIN integrations or federated trust roots.
- A live MINAH model, governed legal corpus, jurisdiction-specific evaluation and operational human-review process.
- Authoritative statutory/rule sources and legally approved assessment logic.
- Real payment provider adapters, authenticated webhooks, settlement/reconciliation service, refunds/reversals or provider certification.
- Distributed multi-node execution, message transport, consensus/replication, production database strategy or cross-service transaction/recovery.
- Cryptographically validated audit proofs or a production zero-knowledge proof pipeline.
- Physical Nexus Pod hardware, firmware, HSM integration, secure boot and measured proof-workload performance.
- Production security assessment, load tests, disaster recovery drills, SLOs and operational acceptance.

## 5. Required interface lifecycle

1. **Contract review:** protocol and workstream owners agree schemas, status semantics, versioning, trust boundaries and ownership.
2. **Simulator:** every external dependency has a deterministic simulator with controllable delay, duplicate, malformed response, timeout, refusal and outage modes.
3. **Contract tests:** the same suite runs against simulator and real adapter; simulator-only behaviour is labelled.
4. **Sandbox integration:** connect one external dependency at a time, verify credentials and signatures, and capture reproducible evidence.
5. **Failure and adversarial testing:** exercise late/reordered messages, replay, authority revocation, rule drift, process death, storage faults and partial outages.
6. **Operational validation:** metrics, alerting, restore/rollback, key rotation, incident handling, deployment configuration and runbooks.
7. **Acceptance:** engineering owner and domain/security owner sign the evidence record; unresolved requirements remain visibly blocked.

## 6. First integration sequence

1. Freeze the shared operation envelope, error model, version policy and idempotency rules.
2. Convert the current Python component boundaries into serialised, versioned interfaces and make all workflow adapters use them natively.
3. Add a minimal API service implementing health/readiness/capabilities and one case workflow with simulator adapters.
4. Add identity/authority adapter with a test issuer first; enforce checks at every protected operation boundary.
5. Integrate the MINAH disposition contract using a simulator first, then a governed model gateway and approved evaluation corpus.
6. Integrate approved rule-pack and evidence services.
7. Add payment provider sandbox integration, signed webhook verification, source sequencing, reconciliation, refunds and reversals.
8. Run the same contract suite across central and disconnected profiles; add multi-process and multi-node transport tests.
9. Specify and prototype Pod hardware interfaces independently against the same node/runtime contract.
10. Complete security, load, disaster-recovery, deployment and independent review gates before production approval.

## 7. Production gate checklist

- [ ] API schemas reviewed and frozen for a versioned release.
- [ ] Every advertised route implemented or marked unsupported in capability discovery.
- [ ] Authentication, authorisation, signing and key rotation implemented and tested.
- [ ] State transitions, idempotency and audit persistence pass failure-injection tests.
- [ ] No simulated outcome can be mistaken for a live institutional or financial outcome.
- [ ] MINAH evaluation, legal provenance and human escalation are accepted by accountable domain owners.
- [ ] Financial callbacks are authenticated; settlement, refunds, reversals and reconciliation are tested with the provider.
- [ ] Multi-node delay, duplication, reordering, partition and recovery tests pass.
- [ ] Data retention, privacy, threat model and independent security review completed.
- [ ] Backups, restoration, observability, incident response, rollback and operational ownership are tested.
- [ ] Nexus Pod hardware is separately qualified if selected; no protocol claim depends on untested hardware.
- [ ] Every exit gate has a named owner, evidence artifact and recorded approval.

## 8. Non-negotiable engineering discipline

Do not use “implemented”, “connected”, “secure”, “sovereign”, “settled”, “production-ready” or “hardware-backed” as a label without the corresponding implementation and evidence. A simulator is useful and expected during development, but its boundary must be visible. Keep the protocol hardware-independent; let Nexus Pods implement an optional profile rather than make the protocol dependent on a device that has not yet been built.
