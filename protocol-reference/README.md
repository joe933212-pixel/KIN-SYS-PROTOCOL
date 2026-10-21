> **Authorship & rights:** Joel Kagoro · Copyright © 2026 Joel Kagoro · Licensed under Apache-2.0; see `LICENSE`, `NOTICE`, and `COPYRIGHT.md`.

# KIN-SYS Protocol Reference Slice

This repository contains small executable research models for two distinct questions: a local transition contract and a two-institution workflow in which origin and destination authorities retain separate decision rights.

The current one-stage slice uses Python's standard-library SQLite support to exercise authority checks, rule outcomes, event idempotency, retained decisions, and atomic state/history commitment. A separate experimental multi-stage model now represents origin and destination decisions, stage-specific authority and rule versions, duplicate/conflicting operation IDs, and durable decision history. It is deliberately separate from the original `ProtocolStore` until its semantics and evidence are reviewed.

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

## Two-institution workflow experiment

The experimental KIN-SYS research model is in `protocol-reference/workflows/institutional_workflow.py`; its independently implemented conventional baseline is `protocol-reference/comparators/conventional_workflow.py`. Matched contract tests are in `protocol-reference/tests/test_institutional_workflow.py`.

The model currently covers two independent decision stages, stage-specific actor and rule checks, retained outcomes, duplicate/conflict handling, and file-backed reopen. Correction handling is a bounded sequence: a configured reviewer proposes a linked correction; the institution may apply or refuse it; the reviewer may dispute a refusal; and an optional, separately configured appeal authority may record `UPHOLD_REFUSAL` or `REMAND_FOR_RECONSIDERATION`. Appeal authority is subject to explicit identity, active-status, and rule-version checks. Neither appeal disposition directly overwrites institutional state, and remand does not automatically reopen or reverse the decision. Payment reconciliation, inter-institution message delivery, notification retries, full legal appeals/joint review, and process-death fault injection during correction handling remain out of scope. Tests compare a limited observable result vector and do not demonstrate a KIN-SYS advantage; the conventional baseline is expected to meet the same contract.

## Scope warning

These are research prototypes, not production runtimes. They use local SQLite databases and caller-supplied actor/rule context. They do not implement cryptographic authentication, networking, distributed consensus, or full CISA/IVM/CEE conformance. Process termination tests exercise local process failure, not power-loss durability. A passing test suite is evidence only for the cases actually run; unsupported workflow cases are not passes.
