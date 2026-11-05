> **Authorship & rights:** Joel Kagoro · Copyright © 2026 Joel Kagoro · Licensed under Apache-2.0; see `LICENSE`, `NOTICE`, and `COPYRIGHT.md`.

# KIN-SYS Protocol

**A sovereign distributed protocol architecture for verifiable execution, institutional coordination, and interoperable edge infrastructure.**

KIN-SYS is a protocol architecture for coordinating computation, identity, authority, governance, economic accounting, and distributed edge systems. It is not defined as a single user-facing application, nor does it assume one blockchain, cloud provider, or implementation language.

The objective is to specify a system in which independently operated components coordinate under explicit rules, with verifiable state transitions and defined behaviour during disconnection, failure, dispute, and recovery. KIN-SYS research treats the protocol, CEE/IVM execution layers, AI assistance, economic mechanisms such as the proposed $KIN, Nexus Pods/edge infrastructure, institutional authority, governance, identity, and privacy as complementary parts of one system. Component-level work is essential, but system-level claims require integrated evidence.

## Architecture and reference contracts

- [Provider-neutral execution architecture](docs/PROVIDER_NEUTRAL_EXECUTION_ARCHITECTURE.md)
- [MINAH deployment profiles and institutional ownership](docs/MINAH_DEPLOYMENT_PROFILES.md)
- [MINAH-first ingress gate](docs/MINAH_FIRST_INGRESS_GATE.md)
- [Governed instruction/decision contract v0.1](docs/GOVERNED_INSTRUCTION_DECISION_CONTRACT_V0_1.md)
- [CEE–IVM crash recovery contract and failure-point matrix](docs/CEE_IVM_RECOVERY_CONTRACT.md)

MINAH is an institution-owned gatekeeper deployment, not a single global service. The reference pipeline supports a versioned deployment profile and pins it to each operation's durable request fingerprint and MINAH disposition trace. This remains a research reference implementation, not production deployment or legal assurance.

## Status

**Stage:** Protocol foundation / runnable local reference prototype  
**Release candidate:** v0.1.0  
**Production status:** Not production-ready. The repository contains a runnable synthetic governed execution path, not a complete live KIN-SYS deployment.

This is the new protocol specification baseline. Earlier KIN-SYS repositories are source material to assess, not automatically authoritative implementations. Proposed capabilities must not be described as implemented until supported by reproducible tests and evidence.

## What the protocol is intended to address

- Sovereign operation across independently governed administrative and technical domains.
- Explicit identity, authority, permissions, and revocation boundaries.
- Deterministic, auditable execution of authorised institutional transitions.
- Interoperation between edge nodes and central or regional infrastructure, including defined degraded/offline operation.
- Economic accounting that can be reconciled and protected against duplicate or unauthorised effects.
- Privacy minimisation: disclose only information required for an authorised purpose.
- Governance, dispute, upgrade, recovery, and failure behaviour that can be tested rather than left implicit.

These are design objectives and research questions, not a claim that every property has been delivered.

## Conceptual elements

MINAH is intended to serve as KIN-SYS's jurisprudential oracle and sovereignty gatekeeper, configured and trained for the applicable jurisdiction or governing domain. Her dispositions must be grounded in traceable, versioned legal sources and evidence. IVM/CEE-informed execution and wider KIN-SYS safeguards must prevent any single component—including MINAH—from bypassing mandatory rules or committing protected state outside the authorised transition path. Kagoro DSL is a candidate formal language for expressing legal and institutional rules; whether it becomes a required protocol component must be decided through semantic and conformance evaluation. Nexus Pods are an optional physical means of running KIN-SYS in jurisdictions where power and connectivity cannot be guaranteed; they are not the protocol, a mandatory node type, or a deployment constraint. Their intended hardware includes an inbuilt HSM and local compute for zero-knowledge proof generation/verification, so raw data can remain local where appropriate while proofs or minimal results are exchanged. The protocol itself remains hardware-independent and can run on other conforming infrastructure. Proofs can reduce data movement and central storage, but some commitments, replay-prevention state, version metadata, audit events, and legally required records may still need retention. These are intended capabilities, not claims of implemented hardware or production cryptography. $KIN remains subject to its own specification and evidence requirements. An edge device is not authority merely because it is physically deployed, and token balances are not proof of an underlying real-world asset or entitlement without a separately defined and verifiable basis.

## Deployment model

KIN-SYS is software-first and hardware-independent. Institutions should be able to adopt it on suitable existing infrastructure, with adapters and profile-specific conformance work as required. The initial design profiles are standard software, constrained connectivity/power, and optional hardware-enhanced security; none requires a Nexus Pod. Hardware-backed key custody, trusted execution, and zero-knowledge proof workloads are optional deployment choices. See [Deployment Profiles](DEPLOYMENT_PROFILES.md) and the machine-readable targets in protocol-reference/deployment_profiles.py.

## Research lineage and source material

- [Institutional Virtual Machine Research Programme](https://github.com/joe933212-pixel/ivm-research) — executable research on institutional transition semantics, authority and rule evaluation, state/history commitment, failure boundaries, and recovery.
- [Published IVM research record on Zenodo](https://doi.org/10.5281/zenodo.22011725).
- [Earlier KIN-SYS v2 repository](https://github.com/joe933212-pixel/KIN-SYS-v2) — legacy concepts and artifacts to audit; not proof that described capabilities are operational.
- [Earlier KIN-SYS Protocol v2.1 repository](https://github.com/joe933212-pixel/KIN-SYS-PROTOCOL-v2.1) — legacy artifacts to audit, not the specification authority for this repository.

The CRS, CISA, IVM, and CEE research lineage must be traced explicitly in the evidence register. Kagoro DSL is a separate language/compiler project whose possible role as KIN-SYS's formal legal/policy language is an open design decision pending conformance evidence.

## Start here

0. [Whole-system integration roadmap: complete architecture, shared invariants, and build sequence](SYSTEM_INTEGRATION_ROADMAP.md)
0. [Production engineering handoff: API routes, workstreams, ownership, and acceptance gates](docs/PRODUCTION_ENGINEERING_HANDOFF.md)
0. [CEE-controlled integrated execution plan: institutional, payment, edge, recovery and acceptance gates](docs/CEE_INTEGRATION_PLAN.md)
0. [Draft production integration API contract (OpenAPI 3.1 JSON; not yet routed)](api/openapi.json)
0. [API implementation matrix: owner, dependencies and acceptance gate for every route](api/implementation-matrix.json)
0. [Provider-neutral instruction and decision contract v0.1](docs/GOVERNED_INSTRUCTION_DECISION_CONTRACT_V0_1.md) — draft envelope validation and digest verification integrated into the synthetic MINAH-first HTTP ingress and reference pipeline; not a production wire standard.
0. [Provider-neutral execution architecture and human usability](docs/PROVIDER_NEUTRAL_EXECUTION_ARCHITECTURE.md) — deployment profiles, adapter boundaries and conformance requirements.
0. [MINAH-first ingress gate](docs/MINAH_FIRST_INGRESS_GATE.md) — reference orchestration boundary and fail-closed tests; wired into the governed v0.1 HTTP instruction route through durable ingress.

- [Local HTTP prototype adapter](protocol-reference/http_service.py) — runnable synthetic vertical slice, not the production API. It exposes health/readiness/capabilities, the governed `POST /protocol/instructions` route, operation inspection and explicit recovery. Legacy/demo and CEE case routes are synthetic and opt-in. The governed route runs MINAH-first ingress and the canonical reference pipeline; CEE and IVM use separate local SQLite stores, without cross-store atomicity or distributed finality.

Run a process-level smoke test first (Python 3.12 or later, standard library only):

```bash
python scripts/prototype_smoke_test.py
```

The smoke test starts the HTTP service as a separate process with disposable SQLite stores, checks health/readiness, submits one synthetic governed instruction through MINAH-first ingress, and inspects the stored decision. It verifies that the local prototype can start and complete its reference path; it does not test production integrations or security.

To run the service interactively from the repository root:

```bash
PYTHONPATH=protocol-reference python -m http_service
```

The built-in reference server binds to a loopback IP (`127.0.0.1:8080` by default) and refuses non-loopback bind addresses. The three `/system/health`, `/system/readiness`, and `/system/capabilities` endpoints are public; operational routes reject non-loopback callers unless an institutional authorizer is injected. `create_app(authorizer=...)` accepts an authorizer implementing `authorize(environ, scope, resource_id)` (or a callable with that signature); recovery, audit, operation-read, instruction-submission, and legacy CEE/demo actions receive distinct scopes. The default loopback mode is a local-development boundary, not caller authentication or production security. Remote deployment requires a production WSGI server, TLS, and an institutional authorizer at the service boundary. Submit only synthetic test data. A normal service run creates file-backed stores under `.kin-sys-prototype/`; configure `KIN_SYS_DATA_DIR`, or override the individual paths with `KIN_SYS_CEE_DB` and `KIN_SYS_IVM_DB`. Tests use isolated in-memory stores unless a test explicitly exercises restart recovery. The built-in `wsgiref` server is single-threaded. The test suite includes cross-connection SQLite contention cases for duplicate ingress and event-ID conflicts, but does not establish shared-connection thread safety, multi-worker HTTP safety, distributed locking or production-scale concurrency.

- `POST /demo/instructions` runs the legacy integrated demonstrator.
- `POST /protocol/instructions` accepts a governed v0.1 instruction envelope and runs it through durable MINAH-first ingress, identity verification, action-specific authority verification, CEE admission/context, IVM transition checks coordinated by CEE, and a final decision. The current implementation is a synthetic reference path; CEE and IVM persist to separate SQLite stores, so it does not provide cross-store atomicity, distributed finality, or exactly-once external effects.
- `GET /protocol/operations/{operation_id}` reads the saved lifecycle, component trace, result and IVM event for an operation without mutating or resuming it. This is intended for review and recovery diagnostics.
- `POST /cee/cases` runs the new CEE-controlled synthetic case path: MINAH advisory review, origin and destination decisions, IVM economic gate, synthetic assessment and simulated payment lifecycle. `GET /cee/operations/{operation_id}` provides a read-only view of its recorded events and trace. `POST /cee/reconciliation` records source-sequenced synthetic reconciliation evidence and does not resubmit a payment. `POST /cee/settlement-evidence` records caller-supplied synthetic settlement evidence only when amount/currency match the pinned obligation; it supports replay recovery if the payment store commits before the CEE journal update. These are prototype orchestration routes, not the 29 proposed production API routes.

The local case coordinator is an additional integration milestone, but not production readiness: identity and authority are synthetic, MINAH is a deterministic stand-in, rules and assessment are fictional, and CEE, IVM, institutional workflow and payment lifecycle use separate SQLite stores. It records a durable submission intent and fails into reconciliation rather than blindly repeating a provider submission after an uncertain crash boundary. Settlement evidence has an explicit HTTP route and a tested payment-commit/CEE-write recovery boundary, but caller input is not authenticated provider evidence. There is no cross-store atomic commit or distributed finality, no live provider integration, and no real funds move. The 29 proposed production API routes remain unimplemented.
1. [Protocol specification](SPECIFICATION.md)
2. [Architecture and trust boundaries](ARCHITECTURE.md)
3. [Source crosswalk: CRS, CISA, IVM, CEE and prior art](docs/SOURCE_CROSSWALK.md)
4. [Full-source CEE 1.0 conformance trace](docs/CEE_CONFORMANCE_TRACE.md)
5. [Integrated cross-border coordination use case](docs/INTEGRATED_USE_CASE.md)
6. [Institutional coordination test matrix](docs/INSTITUTIONAL_COORDINATION_TEST_MATRIX.md)
7. [Integrated demonstrator blueprint: protocol, MINAH AI, statutory remittance, edge profile and validation gates](docs/INTEGRATED_DEMONSTRATOR_BLUEPRINT.md)
8. [Integrated demonstrator interface contracts](docs/INTEGRATED_INTERFACE_CONTRACTS.md)
9. [Common observable result vector for fair comparisons](docs/OBSERVABLE_RESULT_VECTOR.md)
10. [Comparator assessment: IVM Experiment 005 and a fair durable baseline](docs/COMPARATOR_ASSESSMENT.md)
11. [Minimal transition contract](docs/TRANSITION_CONTRACT.md)
12. [Local reference prototype and tests](protocol-reference/README.md)
13. [Research evidence register](RESEARCH_EVIDENCE.md)
14. [Threat model](THREAT_MODEL.md)
15. [Use cases](USE_CASES.md)
16. [Protocol invariants](INVARIANTS.md)
17. [Test strategy](TEST_STRATEGY.md)
18. [Implementation status](IMPLEMENTATION_STATUS.md)
19. [Initial architecture decision](DECISIONS/0001-protocol-scope.md)

## Engineering principle

Every important guarantee must name its assumptions, failure conditions, and verification method. A diagram, proposal, pseudocode function, mock, or cryptographic label is not evidence of a working guarantee. Evidence must distinguish documented intention, an implemented prototype, a reproduced test result, independent evaluation, and production deployment.

Technical criticism, prior-art identification, alternative designs, reproducibility attempts, and falsification are welcome. Original repository materials are attributed to Joel Kagoro; see [COPYRIGHT](COPYRIGHT.md) and [NOTICE](NOTICE). The repository is licensed under Apache License 2.0; see [LICENSE](LICENSE). The licence does not grant trademark rights in the KIN-SYS name or branding.
