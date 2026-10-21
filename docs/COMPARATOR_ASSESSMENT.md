# Comparator Assessment: IVM Experiment 005 and the KIN-SYS Transition Slice

**Status:** Research assessment, draft 0.1  
**Purpose:** Identify what can and cannot be concluded from existing comparison artifacts before designing a fair cross-implementation experiment.

## 1. Summary

The IVM research repository contains a conventional deterministic state-machine comparator in Experiment 005 and a separate KIN-SYS local SQLite transition prototype is maintained in this repository. Both address a narrow transition contract, but the currently inspected implementations do not have equivalent persistence or failure models.

The existing Experiment 005 result is useful evidence that a conventional deterministic state machine can match the canonical happy-path transition and selected semantic checks. It is **not** a fully matched benchmark against the current SQLite prototype, and it does not establish parity for durable commitment, abrupt process termination, concurrency, or recovery after lost acknowledgement.

Conversely, the KIN-SYS SQLite tests do not establish that KIN-SYS is novel or better than conventional alternatives. The fair comparison remains to be built.

## 2. Inspected source artifacts

The assessment reviewed the public `joe933212-pixel/ivm-research` main-branch artifacts:

- `experiments/005/canonical-test-vector.md`
- `experiments/005/conformance-harness/conformance/protocol.py`
- `experiments/005/conformance-harness/conformance/canonical.py`
- `experiments/005/conformance-harness/conformance/harness.py`
- `experiments/005/conformance-harness/comparators/state_machine.py`
- `experiments/005/conformance-harness/tests/test_experiment_005.py`
- `experiments/005/README.md`
- `runtime/reference/current/PROCESS_DEATH_EXPERIMENT.md`
- `docs/RESEARCH_EVIDENCE_CHAIN.md`

Repository links:
- [Experiment 005 overview](https://github.com/joe933212-pixel/ivm-research/tree/main/experiments/005)
- [Canonical test vector](https://github.com/joe933212-pixel/ivm-research/blob/main/experiments/005/canonical-test-vector.md)
- [Conventional comparator source](https://github.com/joe933212-pixel/ivm-research/blob/main/experiments/005/conformance-harness/comparators/state_machine.py)
- [Experiment 005 tests](https://github.com/joe933212-pixel/ivm-research/blob/main/experiments/005/conformance-harness/tests/test_experiment_005.py)
- [IVM v0.4 process-death experiment](https://github.com/joe933212-pixel/ivm-research/blob/main/runtime/reference/current/PROCESS_DEATH_EXPERIMENT.md)

This is a source review, not a fresh execution of the IVM harness in the current environment. Its repository records five passing tests; that recorded result should not be represented as a new independent reproduction.

## 3. What Experiment 005 currently covers

The conventional comparator uses in-memory Python structures for its state, transition records, history, event set, revoked-authority set, and rule-version map. Its five tests cover:

1. canonical `PENDING → ACTIVE` execution;
2. duplicate-event handling without a second transition in the in-memory model;
3. preservation of the recorded rule version when a later version is added;
4. rejection of a new transition after the authority is revoked;
5. detection of a deliberately introduced mismatch in a result field.

This is a meaningful baseline for the tested semantic slice. However, the comparator's `recover` and `replay` methods evaluate its current in-memory records; they do not themselves implement a durable journal, fresh-process recovery, or storage transaction. The duplicate check is based on event-ID membership and does not test conflicting reuse of an ID with different request content. The visible rule-version model stores eligibility, but the inspected execution path checks whether a version exists rather than evaluating the stored eligibility value.

These limitations do not invalidate Experiment 005's stated narrow purpose. They define what its evidence can support.

## 4. What the KIN-SYS local prototype currently adds

The KIN-SYS reference slice uses a file-backed SQLite database in its persistence and fault tests. The observed test suite now covers:

- accepted local state and history commitment;
- same-content duplicate retries and conflicting event-ID reuse;
- wrong/revoked authority and rule-version mismatch;
- rejected-event records and undefined transitions;
- rule-version provenance in history;
- pre-commit rollback;
- state/history/outcome after orderly close and reopen;
- concurrent identical requests through separate SQLite connections;
- child-process termination while a transaction has uncommitted state/event changes;
- child-process termination after local commit but before the test simulates acknowledgement delivery.

The latest GitHub Actions run reports 15 passing tests: [test run](https://github.com/joe933212-pixel/KIN-SYS-PROTOCOL/actions/runs/39593633670).

This is a larger local persistence/failure test surface than the five tests in the Experiment 005 conventional comparator, but **test count is not a measure of architectural superiority**. The two implementations differ in storage, failure model, event-content checks, and tested scenarios. Results must not be compared as though they were already equivalent implementations.

The local crash tests do not establish sudden power-loss durability, filesystem or device failure guarantees, network behaviour, multi-host correctness, distributed finality, Byzantine fault tolerance, or whole-system institutional coordination.

## 5. IVM v0.4 recovery is a separate comparison target

The IVM repository's Experiment 004A describes a separate file-based transaction journal with phases:

`PREPARED → STATE_COMMITTED → HISTORY_ATTACHED → FINALIZED`

Its documented test terminates a worker at defined phases and uses a fresh process to inspect/recover the journal. That is a distinct implementation and evidence claim from the in-memory Experiment 005 comparator.

A fair follow-on experiment should include the exact IVM v0.4 source revision and executable recovery harness, if those artifacts are available and reproducible. The current KIN-SYS SQLite transaction model should not be silently described as implementing the IVM journal, nor should the two be merged into one comparison target.

## 6. Fair comparison design and current implementation status

Three comparison targets are now identified:

- **A — KIN-SYS SQLite slice:** current reference implementation in `protocol-reference/transition.py`.
- **B — conventional durable baseline:** initial independent implementation in `protocol-reference/comparators/conventional_sqlite.py`, using ordinary SQLite transactions and a separate schema. Its tests are in `protocol-reference/tests/test_conventional_baseline.py`.
- **C — IVM v0.4 journal experiment:** exact source revision and its documented journal/recovery contract, to be included after its setup and test command are independently reproducible.

The existing in-memory Experiment 005 comparator remains useful as a fourth, semantic-only baseline, but it must be labelled as in-memory and must not be treated as equivalent to A or B for persistence/fault claims. The current GitHub Actions run executes 27 tests and passes. It includes matched pre-commit and post-commit process-termination cases for both local SQLite implementations, plus a shared six-scenario harness that reports equivalent normalized outcomes for the tested cases. The workflow also validates and uploads the JSON report as the [paired-comparison-results artifact](https://github.com/joe933212-pixel/KIN-SYS-PROTOCOL/actions/runs/39594558948/artifacts/12343126465). This is still an initial paired comparison: the shared harness does not yet include abrupt termination, concurrent requests, or full post-commit retry scenarios, which remain covered by separate implementation-specific tests.

All applicable implementations must receive the same logical initial state, event fields, rule fixtures, authority state, duplicate/conflicting requests, and injected fault points. Where one implementation cannot express a case, record it as unsupported and explain why; do not silently omit it from aggregate scores.

### Minimum paired scenarios

| Scenario | Required observation |
|---|---|
| Canonical accepted transition | State, version, event outcome and history effect match expected vector. |
| Identical retry | No second logical state/history effect. |
| Same ID, different content | Explicit conflict; no second effect. |
| Wrong/revoked authority | Rejected; state unchanged. |
| Unknown or mismatched rule version | Rejected according to the declared contract. |
| Process death before local commitment | Fresh process observes no partial successful transition. |
| Process death after commitment, before acknowledgement | Fresh process finds the committed outcome; retry does not duplicate the effect. |
| Concurrent identical submissions | Exactly one logical transition is committed. |
| Recovery evidence | Each implementation's raw journal/database and normalized result are preserved. |

Only compare latency or throughput after semantic equivalence is established. Pin environment and configuration, repeat measurements, and publish distributions and resource costs. A single SQLite result must not be generalized to a distributed network.

## 7. Falsification criteria

The hypothesis that a KIN-SYS-specific transition mechanism is needed for this use case weakens if a conventional durable baseline satisfies the same authority, history, retry, recovery, audit, and privacy requirements with equal or lower operational complexity and cost.

A KIN-SYS-specific contribution would need to be stated narrowly and supported by an observable difference—for example, a requirement met under specified failure assumptions that the matched baseline does not meet, or a materially better whole-system outcome without shifting cost or risk to institutions or operators.

If no such difference is observed, record that result. A fair experiment is valuable even when it shows that a conventional implementation is sufficient.

## 8. Next implementation gate

1. Pin the source revision for each implementation.
2. Define a shared fixture file and adapter contract from [Observable Result Vector](OBSERVABLE_RESULT_VECTOR.md).
3. **Initial conventional durable baseline added:** the independent SQLite implementation and its eight tests now pass alongside the 15 tests for the KIN-SYS slice.
4. **Shared harness and CI artifact implemented:** `protocol-reference/comparators/paired_harness.py` runs six common scenarios, emits JSON normalized outcomes, and the CI workflow validates and uploads that report.
5. Extend the shared harness to abrupt termination, concurrent requests, and post-commit retry under identical fixture definitions; preserve raw artifacts and source revisions.
6. Add performance measurements only after semantic and fault coverage is matched.

Until these steps are completed, the evidence supports a tested local KIN-SYS slice and an existing IVM comparator experiment—not a claim of superiority, novelty, or end-to-end coordination capability.
