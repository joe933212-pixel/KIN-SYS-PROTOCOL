# KIN-SYS Protocol

**A sovereign distributed protocol architecture for verifiable execution, institutional coordination, and interoperable edge infrastructure.**

KIN-SYS is a protocol architecture for coordinating computation, identity, authority, governance, economic accounting, and distributed edge systems. It is not defined as a single user-facing application, nor does it assume one blockchain, cloud provider, or implementation language.

The objective is to specify a system in which independently operated components coordinate under explicit rules, with verifiable state transitions and defined behaviour during disconnection, failure, dispute, and recovery. KIN-SYS research treats the protocol, CEE/IVM execution layers, AI assistance, economic mechanisms such as the proposed $KIN, Nexus Pods/edge infrastructure, institutional authority, governance, identity, and privacy as complementary parts of one system. Component-level work is essential, but system-level claims require integrated evidence.

## Status

**Stage:** Protocol foundation / research specification  
**Release:** 0.1-draft  
**Production status:** Not production-ready. This repository does not claim an end-to-end KIN-SYS deployment.

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

MINAH AI, Nexus Pods, and $KIN are considered within the wider KIN-SYS concept. Their exact authority, trust boundaries, interfaces, operational roles, and economic rules remain subject to specification and evidence. An AI component must not silently acquire institutional authority; an edge device must not be trusted solely because it is physically deployed; and token balances must not be treated as proof of an underlying real-world asset or entitlement without a separately defined and verifiable basis.

## Research lineage and source material

- [Institutional Virtual Machine Research Programme](https://github.com/joe933212-pixel/ivm-research) — executable research on institutional transition semantics, authority and rule evaluation, state/history commitment, failure boundaries, and recovery.
- [Published IVM research record on Zenodo](https://doi.org/10.5281/zenodo.22011725).
- [Earlier KIN-SYS v2 repository](https://github.com/joe933212-pixel/KIN-SYS-v2) — legacy concepts and artifacts to audit; not proof that described capabilities are operational.
- [Earlier KIN-SYS Protocol v2.1 repository](https://github.com/joe933212-pixel/KIN-SYS-PROTOCOL-v2.1) — legacy artifacts to audit, not the specification authority for this repository.

The CRS, CISA, IVM, and CEE research lineage must be traced explicitly in the evidence register. Kagoro DSL remains an independent language/compiler project and is not a required protocol component.

## Start here

1. [Protocol specification](SPECIFICATION.md)
2. [Architecture and trust boundaries](ARCHITECTURE.md)
3. [Source crosswalk: CRS, CISA, IVM, CEE and prior art](docs/SOURCE_CROSSWALK.md)
4. [Full-source CEE 1.0 conformance trace](docs/CEE_CONFORMANCE_TRACE.md)
5. [Integrated cross-border coordination use case](docs/INTEGRATED_USE_CASE.md)
6. [Minimal transition contract](docs/TRANSITION_CONTRACT.md)
7. [Local reference prototype and tests](protocol-reference/README.md)
8. [Research evidence register](RESEARCH_EVIDENCE.md)
9. [Threat model](THREAT_MODEL.md)
10. [Use cases](USE_CASES.md)
11. [Protocol invariants](INVARIANTS.md)
12. [Test strategy](TEST_STRATEGY.md)
13. [Implementation status](IMPLEMENTATION_STATUS.md)
14. [Initial architecture decision](DECISIONS/0001-protocol-scope.md)

## Engineering principle

Every important guarantee must name its assumptions, failure conditions, and verification method. A diagram, proposal, pseudocode function, mock, or cryptographic label is not evidence of a working guarantee. Evidence must distinguish documented intention, an implemented prototype, a reproduced test result, independent evaluation, and production deployment.

Technical criticism, prior-art identification, alternative designs, reproducibility attempts, and falsification are welcome. The repository is licensed under Apache License 2.0; see [LICENSE](LICENSE). The licence does not grant trademark rights in the KIN-SYS name or branding.
