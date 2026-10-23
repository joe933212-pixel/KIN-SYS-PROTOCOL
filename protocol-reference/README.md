> **Authorship & rights:** Joel Kagoro · Copyright © 2026 Joel Kagoro · Licensed under Apache-2.0; see `LICENSE`, `NOTICE`, and `COPYRIGHT.md`.

# KIN-SYS Protocol Reference Slice

This repository contains small executable research models for two distinct questions: a local transition contract and a two-institution workflow in which origin and destination authorities retain separate decision rights.

The current one-stage slice uses Python's standard-library SQLite support to exercise authority checks, rule outcomes, event idempotency, retained decisions, and atomic state/history commitment. A separate experimental multi-stage model represents origin and destination decisions, stage-specific authority and rule versions, duplicate/conflicting operation IDs, and durable decision history. It is deliberately separate from the original `ProtocolStore` until its semantics and evidence are reviewed.

## Run tests

From the repository root:

```sh
python -m unittest discover -s protocol-reference/tests -v
```

No third-party packages are required.

## Paired local transition comparison

Run the shared nine-scenario comparison from the repository root:

```sh
PYTHONPATH=protocol-reference python -m comparators.paired_harness
```

The JSON report compares the original KIN-SYS SQLite slice with the independent conventional SQLite baseline across accepted transitions, duplicate replay, conflicting IDs, authority and rule rejection, pre-commit failure, concurrent identical submissions, process termination before commitment, and retry after commitment but before acknowledgement.

## Integrated synthetic demonstrator

The whole-system inventory lives in `protocol-reference/system_model.py` and is included in both central and disconnected-edge demo outputs. The shared semantic response contract is implemented in `protocol-reference/component_contracts.py`; local identity, MINAH, fictional tax and simulated payment adapters emit envelopes, while institutional decision results are wrapped and validated before their data is used. A misrouted MINAH response is tested to stop the case before institutional history or payment obligations are created. It explicitly lists implemented, simulated, specified, interface-modelled, and unresolved logical responsibilities. The `system_trace` output records selected case-linked component events using a shared event schema; validation checks schema/case linkage, not event truth or distributed trace completeness. See the root `SYSTEM_INTEGRATION_ROADMAP.md` before treating the local slice as the system architecture. Neither inventory nor trace claims every component is implemented. The response contract validates envelope shape, component identity, operation correlation, version and status semantics; it is not cryptographic authentication, transport security, or proof of external facts. CI run [39757877800](https://github.com/joe933212-pixel/KIN-SYS-PROTOCOL/actions/runs/39757877800) passed 124 tests on 2026-10-22.

Run the end-to-end synthetic demonstration from the repository root:

```sh
PYTHONPATH=protocol-reference python -m integrated_demo
```

It composes the existing two-institution SQLite model with test-only identity/authority checks, a deterministic MINAH stand-in, a fictional versioned statutory-obligation calculator, a simulated payment provider, and a disconnected-edge profile. Test cases are in `protocol-reference/tests/test_integrated_demo.py`.

The adapters are deliberately simulated. The tax rule is fictional, no money moves, no real national identity or institutional systems are contacted, MINAH is not a real AI model or legal authority, and the edge profile is not physical Nexus Pod hardware. In particular, this legacy research demonstrator does not implement the canonical MINAH-first ingress, national/enterprise registry API identity verification, or pre-handoff scoped authority checks. The report states these limits. This is an initial integrated software slice, not the full KIN-SYS system.

## CEE-controlled synthetic institutional/payment case path

`protocol-reference/cee_case_coordinator.py` is a synthetic case-coordination demonstrator. The coordinator extends the canonical MINAH -> CEE -> final-decision boundary across a synthetic institutional/payment case. MINAH's disposition is validated and recorded before CEE admission; the case journal and private execution runtime share one CEE-owned local SQLite connection. Origin/destination institutions and payment lifecycle remain synthetic adapters with separate local stores, so cross-adapter atomicity is not claimed. The local HTTP prototype exposes `POST /cee/cases`, `GET /cee/operations/{operation_id}`, `POST /cee/reconciliation`, and `POST /cee/settlement-evidence`. Case results include a canonical `final_decision` field in addition to detailed workflow/payment status. Both reconciliation and settlement-evidence routes accept only synthetic caller-supplied input; neither authenticates or connects to a provider or bank. Settlement evidence is checked against the pinned obligation and tested for idempotent recovery when the payment store commits before the CEE journal. Unknown outcomes remain pending, reconciliation requires monotonically increasing source sequence, and the coordinator does not automatically resubmit a payment after a crash leaves submission outcome uncertain. Cross-store atomicity is not claimed. Tests live in `protocol-reference/tests/test_cee_case_coordinator.py` and `protocol-reference/tests/test_http_service.py`.

## Payment lifecycle boundary experiment

`protocol-reference/payment_lifecycle.py` models separate records for an obligation, a stable payment instruction, a provider acknowledgement, settlement evidence, and reconciliation findings. It uses SQLite-backed IDs and request fingerprints to reject conflicting reuse; retries of the same instruction return the existing record rather than submitting anything to a provider. A provider's acceptance is explicitly **not** settlement. Unknown outcomes remain unresolved, and reconciliation does not resubmit a payment. Settlement requires matching amount/currency evidence and a settlement reference.

Tests are in `protocol-reference/tests/test_payment_lifecycle.py`. The model is wired into `integrated_demo.py` for simulated confirmed, declined, and unknown outcomes; the integrated report includes a separate lifecycle trace and explicitly labels settlement as simulated. There is no provider adapter, real money movement, distributed transaction, cryptographic evidence validation, reversal/refund workflow, or production reconciliation service. An independent conventional payment-lifecycle implementation and paired observable-contract tests exist in `protocol-reference/comparators/conventional_payment_lifecycle.py` and `protocol-reference/tests/test_paired_payment_lifecycle.py`. A matched synthetic whole-flow comparator is now in `protocol-reference/comparators/paired_integrated_harness.py`; run it with `PYTHONPATH=protocol-reference python -m comparators.paired_integrated_harness`. It compares thirteen declared scenarios across institutional outcomes, synthetic tax assessment, payment statuses, disconnected queueing, and reconnect checks for current context, stale evidence, rule drift, and revoked authority. The comparison is limited to a shared observable result vector and does not establish a performance, cost, security, or novelty advantage.

## Simulated offline queue and reconnect checks

`protocol-reference/edge_queue.py` provides a local SQLite queue for offline operations. It pins evidence and rule versions, rejects conflicting operation-ID reuse, and revalidates authority, evidence version, and rule version at reconnect. A successful replay means only `READY_FOR_CENTRAL_REVIEW`; it is not an institutional approval. Deferred or rejected operations cannot be silently reactivated under the same ID; changed evidence/context requires a newly identified operation. Tests in `protocol-reference/tests/test_edge_queue.py` cover duplicate enqueue, stale evidence, rule drift, authority revocation, recovery with a fresh ID, and file-backed reopen. The queue is wired into the integrated demonstrator's disconnected submission path, and the paired whole-flow comparator now checks reconnect outcomes against the independent `protocol-reference/comparators/conventional_edge_queue.py` implementation.

## Paired integrated workflow comparison

Run the paired nine-scenario synthetic comparison from the repository root:

```sh
PYTHONPATH=protocol-reference python -m comparators.paired_integrated_harness
```

The candidate and independently implemented conventional path are compared on a declared observable vector for happy path, destination rejection, unauthorised origin, rule drift, revoked origin authority, payment decline, unknown payment outcome, synthetic exemption, and disconnected edge profile. Equivalence means only that the listed fields match in these synthetic cases; it does not establish advantage, security, production readiness, or real institutional compatibility.

## Two-institution workflow experiment

The experimental KIN-SYS research model is in `protocol-reference/workflows/institutional_workflow.py`; its independently implemented conventional baseline is `protocol-reference/comparators/conventional_workflow.py`. Matched contract tests are in `protocol-reference/tests/test_institutional_workflow.py`.

The model currently covers two independent decision stages, stage-specific actor and rule checks, retained outcomes, duplicate/conflict handling, and file-backed reopen. Correction handling is a bounded sequence: a configured reviewer proposes a linked correction; the institution may apply or refuse it; the reviewer may dispute a refusal; and an optional, separately configured appeal authority may record `UPHOLD_REFUSAL` or `REMAND_FOR_RECONSIDERATION`. Appeal authority is subject to explicit identity, active-status, and rule-version checks. Neither appeal disposition directly overwrites institutional state, and remand does not automatically reopen or reverse the decision. Payment reconciliation, inter-institution message delivery, notification retries, full legal appeals/joint review, and process-death fault injection during correction handling remain out of scope. Tests compare a limited observable result vector and do not demonstrate a KIN-SYS advantage; the conventional baseline is expected to meet the same contract.

## Scope warning

These are research prototypes, not production runtimes. They use local SQLite databases and caller-supplied actor/rule context. They do not implement cryptographic authentication, networking, distributed consensus, or full CISA/IVM/CEE conformance. The target architecture has one execution path: IVM runs within CEE and executes CISA-defined machine semantics; CEE is not a parallel executor. Process termination tests exercise local process failure, not power-loss durability. A passing test suite is evidence only for the cases actually run; unsupported workflow cases are not passes.


## Canonical MINAH -> CEE instruction slice

`protocol-reference/protocol_pipeline.py` implements the first refactoring step for `POST /protocol/instructions`: MINAH's synthetic preliminary disposition is validated and durably recorded before CEE admission; missing evidence is deferred before admission; malformed or self-authorising output is rejected; and the public result is a single final decision (`PASSED`, `REJECTED`, or `DEFERRED`). The public component trace begins with MINAH and continues through CEE. The underlying transition store is private prototype implementation detail, not a separate public protocol stage. The lifecycle journal and private execution runtime now share one CEE-owned local SQLite connection, although the lifecycle acknowledgement and execution commit are still separate transactions with a tested recovery boundary. The draft branch passed [CI run #281](https://github.com/joe933212-pixel/KIN-SYS-PROTOCOL/actions/runs/39932639900) with 196 tests. This remains a synthetic local research slice: the pre-admission authority policy is server-configured demo logic, not identity verification against an authorised registry; authoritative legal sources, cross-store atomicity and distributed finality are not implemented.
