> **Authorship & rights:** Joel Kagoro · Copyright © 2026 Joel Kagoro · Licensed under Apache-2.0; see `LICENSE`, `NOTICE`, and `COPYRIGHT.md`.
>
> **Status:** Source map, draft 0.1. Reviewed from repository files on `main`; test execution is not newly claimed by this document.

# KIN-SYS Implementation Source Map

This map connects the current executable research slices to the claims they can reasonably support. It is not a conformance certification. The source map must be updated alongside code and tests.

## Current executable entry points

| Entry point | Command from repository root | Purpose | Boundary |
|---|---|---|---|
| Local protocol slice | `python -m unittest discover -s protocol-reference/tests -v` | Tests the local SQLite transition, experimental institutional workflow, payment lifecycle, edge queue, component contract, and integrated demonstrator. | Local research implementation; no distributed consensus or live institutional adapters. |
| Paired local comparison | `PYTHONPATH=protocol-reference python -m comparators.paired_harness` | Runs a matched nine-scenario comparison between the KIN-SYS SQLite transition slice and a conventional SQLite baseline. | Local SQLite comparison only; do not infer multi-node or production superiority. |
| Integrated synthetic demo | `PYTHONPATH=protocol-reference python -m integrated_demo` | Composes origin/destination institutional decisions, synthetic identity/authority, deterministic MINAH stand-in, fictional tax calculation, simulated payment lifecycle, and edge profile. | No real AI/legal authority, national identity, tax law, payment provider, real money, or physical edge hardware. |

The commands above are documented in `protocol-reference/README.md`. This document has not executed them in a fresh environment; current results must be attached to a specific source revision before being reported.

## Source-to-test trace

| Requirement area | Implementation | Test coverage | What the evidence supports | Still not established |
|---|---|---|---|---|
| Local state transition, authority flag, rule eligibility, event idempotency, accepted history | `protocol-reference/transition.py` (verify exact current source before changing); `protocol-reference/tests/test_transition.py` | `TransitionContractTests`; test names include authorised transition, identical duplicate, conflicting event-ID reuse, authority/rule rejection, concurrency and process-failure scenarios (see full file). | Bounded local SQLite transition behaviour under the conditions of the named tests. | Cryptographic identity, federated authority, complete CEE lifecycle, cross-store atomicity, distributed finality, power-loss guarantees. |
| Conventional durable local comparator | `protocol-reference/comparators/conventional_sqlite.py` | Shared paired harness at `protocol-reference/comparators/paired_harness.py` | A conventional implementation can be compared on equivalent local scenarios. | No general proof that KIN-SYS is better, more secure, or cheaper; results depend on scenario and environment. |
| Two-institution workflow with separate stage authorities and rule versions | `protocol-reference/workflows/institutional_workflow.py` | `protocol-reference/tests/test_institutional_workflow.py` | A synthetic local model can represent distinct origin and destination decisions. | Real mandates, live institution-to-institution messaging, cryptographic federation, distributed recovery, jurisdiction-specific legality. |
| Shared component-response envelope | `protocol-reference/component_contracts.py` | `protocol-reference/tests/test_component_contracts.py` | Validates envelope fields, contract version, expected component ID, operation correlation and status/failure consistency. | It is not cryptographic authentication, transport security, provenance proof, or truth verification. |
| Whole-system responsibility inventory | `protocol-reference/system_model.py` | Exercised by integrated demo and its tests | Makes implemented, simulated, interface-modelled and unresolved responsibilities explicit in the demonstrator output. | Inventory entries are not themselves implementations; trace validation does not prove event truth or distributed trace completeness. |
| Synthetic end-to-end workflow | `protocol-reference/integrated_demo.py` | `protocol-reference/tests/test_integrated_demo.py` | Happy path and selected negative/failure paths can be exercised across the composed local adapters. Tests include destination rejection, payment timeout, declined payment, missing MINAH evidence, unauthorised actor, and revoked authority. | Not an actual distributed system; adapters and evidence are synthetic; no live external services or money movement. |
| Payment instruction/acknowledgement/settlement lifecycle | `protocol-reference/payment_lifecycle.py` | `protocol-reference/tests/test_payment_lifecycle.py` | Distinguishes instruction, provider acknowledgement, settlement evidence, and unresolved outcomes in a local model. | No provider integration, cryptographic settlement-evidence verification, refunds/reversals, or production reconciliation. |
| Disconnected-edge queue | `protocol-reference/edge_queue.py` | `protocol-reference/tests/test_edge_queue.py` | Models durable local queuing, idempotent enqueue and explicit reconnect checks. | Not a deployed edge node, no physical Nexus Pod, and no proof of real network-partition safety across nodes. |
| CEE source-to-prototype gap analysis | `docs/CEE_CONFORMANCE_TRACE.md` | References `protocol-reference/tests/test_transition.py` and other limited tests | Identifies gaps against the CEE lifecycle, authority, rules, commitment, history, recovery, and deployment responsibilities. | The trace explicitly disclaims CEE conformance; local tests do not establish full-source conformance. |
| Threat assumptions and candidate controls | `THREAT_MODEL.md` | Security tests remain to be mapped per threat | Gives a threat inventory and candidate controls for future validation. | Not a completed threat assessment, penetration test, cryptographic review, or security certification. |

## Known mismatch between the intended system and the demonstrator

The system model is valuable precisely because it names the gaps. Current source documentation marks the following as simulated, interface-modelled, or unresolved rather than production implementations:

- cryptographic identity, signature verification, credential lifecycle, federation, and real national-identity integration;
- a complete serialized request/wire protocol and transport security;
- authoritative external evidence and real source-provenance verification;
- jurisdiction-specific MINAH AI, legal-source retrieval, evaluation, and non-bypassable enforcement;
- real statutory rules, tax authority integration, and real payment provider integration;
- distributed consensus/finality and multi-node network partition/recovery;
- production proof generation/verification and hardware roots of trust;
- physical Nexus Pod implementation;
- complete governance, disputes/corrections, operational security review, and production deployment runbooks.

These are not reasons to abandon the project. They are boundaries that guide what we implement next and what we may honestly demonstrate now.

## Immediate source-audit checklist

- [ ] Fetch and inspect the exact current `protocol-reference/transition.py` source; map each transition invariant to a test name.
- [ ] Enumerate every test file under `protocol-reference/tests/` and record test counts by capability, not just total count. Known key files include `test_transition.py`, `test_institutional_workflow.py`, `test_component_contracts.py`, `test_integrated_demo.py`, `test_payment_lifecycle.py`, and `test_edge_queue.py`.
- [ ] Inspect `protocol-reference/system_model.py` in full and compare every component status with executable code.
- [ ] Inspect `protocol-reference/integrated_demo.py` and its complete tests, including malformed/misrouted component responses.
- [ ] Inspect the full paired harness, baseline, result-vector validation, and stored CI artifacts.
- [ ] Inspect workflow files and verify the status of the exact head commit for this audit branch; never carry a historical green check forward as a current result.
- [ ] Run the documented commands from a clean checkout and preserve stdout/stderr, revision, environment, and exit code.
- [ ] Convert every unresolved item into an issue or acceptance test before the first non-synthetic institutional pilot.

## Engineering rule

If the implementation is simulated, label it simulated in the interface and output. If a guarantee has only been tested locally, label it local. If a result is unknown, retain the unknown. The demonstrator should make the real work visible—not make unfinished parts look finished.
