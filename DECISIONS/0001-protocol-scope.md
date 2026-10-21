# Decision 0001 — Protocol scope and evidence discipline

- **Status:** Accepted as the initial working baseline; revisable through a documented decision.
- **Date:** 2026-10-21
- **Decision owner:** Repository maintainer

## Context

Earlier KIN-SYS material includes application-oriented artifacts and broad descriptions of possible integrations. This repository is intended to define a protocol architecture that can be reviewed, tested, implemented by more than one party, and evaluated for real-world deployment. It must not inherit claims merely because they appear in an earlier repository.

The existing IVM research archive provides relevant work on executable institutional transitions and local failure/recovery experiments. It is research input, not automatic proof that a complete KIN-SYS system exists.

## Decision

1. Treat KIN-SYS as a protocol architecture and ecosystem, not a single application.
2. Separate protocol requirements from implementations, deployment profiles, and product interfaces.
3. Treat MINAH AI, Nexus Pods, and $KIN as conceptual elements whose exact roles and guarantees must be specified and tested.
4. Use the existing IVM research programme, its published Zenodo record, and source repositories as traceable research inputs.
5. Trace CRS, CISA, IVM, and CEE to exact source artifacts before declaring their interfaces or dependencies.
6. Keep Kagoro DSL an independent project and do not make it a mandatory protocol component.
7. Label each claim according to the available evidence; do not claim novelty, security, privacy, sovereignty, production readiness, or legal effect without supporting evidence.
8. Build and test a narrow end-to-end protocol slice before broadening the implementation.

## Consequences

The repository begins with specifications, threat boundaries, use cases, invariants, and a research evidence register. It does not begin by selecting a blockchain, AI provider, hardware design, or token contract. Those decisions require explicit requirements and comparative evidence.

Legacy repositories remain accessible as historical source material. Corrections to claims should be recorded through the evidence register rather than silently assumed.
