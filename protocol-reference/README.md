> **Authorship & rights:** Joel Kagoro · Copyright © 2026 Joel Kagoro · Licensed under Apache-2.0; see `LICENSE`, `NOTICE`, and `COPYRIGHT.md`.

# KIN-SYS Protocol Reference Slice

This is a small executable reference for the local institutional transition contract in [`docs/TRANSITION_CONTRACT.md`](../docs/TRANSITION_CONTRACT.md).

It uses Python's standard-library SQLite support to exercise authority checks, rule outcomes, event idempotency, retained decisions, and atomic state/history commitment.

## Run

From the repository root:

```sh
python -m unittest discover -s protocol-reference/tests -v
```

No third-party packages are required.

## Paired comparison report

Run the shared six-scenario comparison from the repository root:

```sh
PYTHONPATH=protocol-reference python -m comparators.paired_harness
```

The command prints a JSON report containing normalized results from the KIN-SYS SQLite slice and the independent conventional SQLite baseline. It compares accepted transitions, identical retries, conflicting identifiers, wrong authority, rule-version mismatch, and pre-commit failure. The report is a local semantic comparison only; crash/restart tests currently run in the test suite and are not yet included in this six-scenario JSON output.

## Scope warning

This is a research conformance slice, not a production runtime. It uses a single local SQLite database, a deliberately small example transition, and caller-supplied actor/rule context. It does not implement cryptographic authentication, networking, distributed consensus, or full CISA/IVM/CEE conformance.

The tests are intended to make the stated local contract executable and falsifiable. Their results should be treated as evidence only for the cases actually run.
