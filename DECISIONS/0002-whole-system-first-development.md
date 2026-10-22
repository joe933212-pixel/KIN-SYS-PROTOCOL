> **Authorship & rights:** Joel Kagoro · Copyright © 2026 Joel Kagoro · Licensed under Apache-2.0; see `LICENSE`, `NOTICE`, and `COPYRIGHT.md`.

# Decision 0002 — Whole-system-first KIN-SYS development

- **Status:** Proposed for review; implementation direction to be adopted after repository consistency review.
- **Date:** 2026-10-22
- **Decision owner:** Repository maintainer

## Context

The repository contains useful local transition, institutional workflow, payment lifecycle, edge queue, and integrated demonstrator experiments. These have improved specific properties and exposed important boundaries. However, the current executable system remains a narrow synthetic flow and does not yet represent all intended KIN-SYS responsibilities as one coherent, contract-driven model. Continuing to expand components independently risks making their local interfaces and assumptions define the architecture after the fact.

## Decision proposed

1. Treat the whole-system contract and integrated use cases as the organising authority for implementation priorities.
2. Represent all major logical KIN-SYS responsibilities—including identity/authority, protocol communication, orchestration, evidence, policy/rules, MINAH AI, execution semantics, state/history, independent institutions, economic accounting/remittance, edge/Nexus Pods, governance, privacy/security, external adapters, and conformance—in one architecture and status inventory.
3. Build one executable whole-system model using simulations for unfinished or costly components. A simulation must use the same semantic contract intended for a future real adapter and must label what it does not prove.
4. Require each component change to identify system-level invariants, upstream dependencies, downstream effects, failure propagation, version semantics, and cross-component regression tests.
5. Keep unit/component tests, but do not treat them as a substitute for end-to-end contract tests or evidence that KIN-SYS fulfils its purpose.
6. Compare the integrated model with a fair conventional baseline and preserve evidence that may show a KIN-SYS mechanism is unnecessary, inferior, or too costly.
7. Keep MINAH AI, physical Nexus Pods, real institutional/legal adapters, real payment effects, and $KIN at honest readiness levels. Their roles and interfaces are specified in the system model before expensive implementation is assumed.

## Consequences

The existing experiments are retained as useful evidence and may be refactored when their assumptions conflict with the system contract. The immediate priority becomes repository-wide consistency and a contract-driven integrated model, not a sequence of isolated component enhancements.

The existing decision 0001 remains the initial scope/evidence baseline; this decision, if accepted, updates its implementation sequence and should be reflected in the decision history rather than silently rewriting the original record.
