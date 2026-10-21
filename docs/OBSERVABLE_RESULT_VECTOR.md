# KIN-SYS Observable Result Vector

**Status:** Research test contract, draft 0.1. This document defines comparison fields; it does not claim that KIN-SYS, IVM, or a conventional comparator already emits this format.

## 1. Purpose

A comparison is only meaningful when implementations receive equivalent inputs and their externally observable outcomes are recorded in a common form. This vector is intended for the KIN-SYS local transition slice, the relevant IVM runtime experiment, and a conventional deterministic state-machine/API baseline.

The goal is not to force different architectures to share internal designs. It is to compare their externally observable decisions, state effects, evidence, and behaviour under the same scenario.

## 2. Comparison rules

1. Pin the source revision, implementation identity, configuration, runtime, and test-fixture version for every run.
2. Give each implementation the same logical initial state, authority/rule fixture, request sequence, and injected fault point. Document unavoidable adapter differences.
3. Separate semantic correctness from performance. A faster incorrect result is not a pass.
4. Preserve raw output alongside the normalized record; do not normalize away errors or uncertainty.
5. Record unsupported or unobservable fields as `null` with an explanation. Do not infer a guarantee from a missing field.
6. Repeat nondeterministic/network experiments and report the repeat count and distribution. Do not use a single run to establish reliability.
7. Evaluate component behaviour, interface behaviour, and whole-scenario usefulness separately.

## 3. Required run metadata

Each run must record:

- `schema_version`: version of this vector format.
- `scenario_id` and `fixture_hash`: stable identifiers for the scenario and exact fixture set.
- `implementation_id`, `source_revision`, and `configuration_hash`.
- `environment`: operating system, runtime/version, storage, relevant hardware, and network conditions.
- `started_at` and elapsed duration, with clock source and units documented.
- `fault_injection`: fault type and exact point, or `none`.
- `repeat_index` and total planned repeats.

Do not include real personal data or secrets in fixtures or result artifacts.

## 4. Required observable outcome

For each submitted request, record the following fields where the implementation exposes them:

| Field | Meaning |
|---|---|
| `request_id` | Stable logical request identifier used across implementations. |
| `decision` | Normalized value: `ACCEPTED`, `REJECTED`, `CONFLICT`, `PENDING`, or `ERROR`. Preserve the implementation's raw status separately. |
| `reason_code` | Stable, documented reason for rejection, conflict, or error; null for no reason. |
| `state_before` / `state_after` | Authoritative logical state observed before and after the request. |
| `version_before` / `version_after` | State/version counter before and after, if defined. |
| `event_recorded` | Whether the request/decision record is durably observable after the operation. |
| `history_effect_count` | Number of new successful transition-history entries attributable to this request. |
| `authority_basis` | Authority reference and verification outcome, without exposing credentials or personal identity data. |
| `rule_basis` | Rule-set/version reference and evaluation outcome. |
| `duplicate_of` | Original request/event reference for an idempotent retry, if applicable. |
| `commit_observation` | What evidence establishes committed, rejected, or uncertain status. |
| `after_restart` | Re-observed state, version, event outcome, and history after a defined restart/recovery procedure. |
| `raw_result_ref` | Path or identifier for unmodified output/log evidence. |

A normalized `decision` is a comparison label, not a claim that all implementations have identical internal states or identical failure models.

## 5. Minimum scenario set

At minimum, run and report these scenarios for each implementation:

- **S01 — authorised transition:** one permitted request commits exactly one state change and one corresponding history effect.
- **S02 — identical retry:** repeating the same request does not create a second logical effect; the prior outcome is returned or its documented equivalent is observed.
- **S03 — conflicting identifier:** reusing an idempotency/event identifier for different request content is rejected as a conflict without a second effect.
- **S04 — wrong or revoked authority:** request is rejected and authoritative state remains unchanged.
- **S05 — rule rejection/version mismatch:** request is rejected with the evaluated rule basis recorded where the implementation supports it.
- **S06 — failure before commitment:** after restart, no partial successful effect is visible if the contract promises atomic rollback.
- **S07 — commit before acknowledgement loss:** client observes uncertainty, retries safely, and determines whether the original effect committed without causing a duplicate.
- **S08 — concurrent competing requests:** final state and history remain consistent under a documented concurrency schedule.
- **S09 — rule/authority change during request:** define the ordering boundary and verify which version/authority governs the decision.
- **S10 — external dependency uncertain:** for the integrated scenario, payment or external approval timeouts must not be silently converted into success or failure without evidence.

The current SQLite prototype has tests for a subset of S01–S06 under limited local conditions. Its orderly close/reopen tests are not a substitute for abrupt process termination, power-loss testing, concurrent-node testing, or network partition experiments.

## 6. Comparison metrics

Report correctness and capability first:

- expected versus observed decision;
- state/history invariant violations;
- duplicate logical effects;
- unaccounted or contradictory outcomes;
- recovery result and evidence completeness;
- authority-boundary violations;
- privacy/data disclosure beyond the scenario's declared scope;
- proportion of scenarios supported without undocumented manual intervention.

Report performance separately, using repeated measurements and distributions:

- end-to-end request latency;
- throughput under a specified load;
- recovery time after each fault type;
- storage/network cost and resource consumption;
- operational steps and human intervention required.

Define start/end points before collecting performance results. Do not compare local in-memory timings with networked multi-party timings as if they measured the same thing.

## 7. Whole-system coordination outcomes

For the cross-border agricultural consignment scenario, add a scenario-level record for each institution:

- institution role and declared mandate;
- action requested, decision authority, and permitted scope;
- evidence submitted and evidence accepted;
- dependency on another institution and resulting status;
- dispute/appeal route and final disposition;
- information disclosed to each participant;
- unresolved state, responsible party, and recovery action.

The development partner's funding or convening role must not confer decision authority. One institution's approval must not be represented as another institution's approval. AI-generated recommendations are advisory unless an explicitly authorised human or institutional rule makes a decision. Token or payment settlement evidence must not be treated as proof of an underlying real-world entitlement without a separate verified basis.

## 8. Pass/fail and falsification

Before running a scenario, publish the expected result and acceptance threshold. A scenario fails if the implementation violates its declared invariant, invents a decision, loses a required record, applies authority outside its scope, or produces a duplicate effect where one is forbidden.

A result may also falsify the case for adding a KIN-SYS component. If a conventional workflow/API achieves the same required outcomes with lower complexity, lower cost, clearer accountability, or better recovery under the same assumptions, that is evidence against claiming the component is necessary for that use case.

## 9. Evidence bundle

Each reproducible comparison should publish:

1. this vector's version;
2. fixture and scenario definitions;
3. exact source revisions and configuration;
4. command(s) and environment;
5. raw outputs and normalized vectors;
6. expected/observed differences and failures;
7. repeat counts, measurement method, and uncertainty;
8. known limitations and the next falsification test.

A passing local vector is not proof of CEE conformance, distributed finality, security, novelty, legal compliance, or production readiness.
