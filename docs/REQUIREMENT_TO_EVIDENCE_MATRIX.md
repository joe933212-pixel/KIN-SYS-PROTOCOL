> **Authorship & rights:** Joel Kagoro · Copyright © 2026 Joel Kagoro · Licensed under Apache-2.0; see `LICENSE`, `NOTICE`, and `COPYRIGHT.md`.
>
> **Status:** Working audit artifact, draft 0.1. This matrix records the evidence visible in the repository documentation at the time of writing. A documented requirement is not proof of implementation. Rows marked “verify” require direct source/test inspection before any stronger claim.

# KIN-SYS Requirement-to-Evidence Matrix

## 1. Why this matrix exists

KIN-SYS is intended to become a working, deployable system that can be demonstrated to real institutions and partners. This matrix turns that objective into traceable engineering work: every important promise must point to a normative requirement, an implementation, a repeatable test, and observable evidence.

The aim is not to defend the current architecture. It is to discover what is necessary, what works, what remains unproven, and what should be simplified or removed. We should reach deployment by closing evidence gaps, not by relabelling them.

## 2. Evidence vocabulary

Use these labels consistently in this repository:

- **Specified** — a requirement or intended behaviour is written down.
- **Implemented (unverified here)** — source code is believed to exist, but this matrix has not yet traced the exact implementation and test.
- **Prototype evidence** — a bounded experiment/test supports the behaviour only within its documented environment and assumptions.
- **System evidence** — a reproducible end-to-end scenario demonstrates the behaviour across the relevant system boundaries.
- **Independent evidence** — another party reproduces or reviews the result.
- **Open** — the rule, implementation, or evidence is missing or materially incomplete.
- **Not applicable / deliberately excluded** — an explicit design decision says the capability is not required for the chosen use case/profile.

Do not treat “planned”, “designed”, “has a test”, “CI is green”, and “production-ready” as interchangeable. CI results apply only to the exact revision and jobs that actually ran.

## 3. Initial requirement-to-evidence matrix

| ID | Capability / promise | Normative source | Current evidence visible in repository documentation | Evidence status | Required next action / acceptance evidence |
|---|---|---|---|---|---|
| KSR-001 | Define what KIN-SYS is and its boundaries | `SPECIFICATION.md`; `SYSTEM_INTEGRATION_ROADMAP.md`; `KIN-SYS-FOUNDATIONAL-THESIS.md` | Purpose and whole-system logical responsibilities are described. The specification explicitly labels itself Draft 0.1 and says it is not a final wire protocol. | Specified; normative definition incomplete | Approve a versioned protocol scope and glossary. Separate mandatory core, optional profiles, adapters, and out-of-scope claims. Link each SHALL/MUST requirement to an acceptance test. |
| KSR-002 | Versioned, validated request/message envelope | `SPECIFICATION.md` §3 | Candidate fields are listed, but encoding/schema is explicitly undecided. | Open | Define canonical field semantics, encoding, version negotiation, validation, unknown-field handling, audience, expiry, integrity, and rejection codes. Add valid/invalid fixtures and compatibility tests. |
| KSR-003 | Identity, authentication, authority, delegation, revocation | `SPECIFICATION.md`; `THREAT_MODEL.md`; `SYSTEM_INTEGRATION_ROADMAP.md` | Identity/authority are defined as responsibilities; national identity integration and production identity provider are not defined. Threat document lists candidate controls, not proof of implementation. | Specified conceptually; implementation/evidence unverified | Implement a minimal local test identity and scoped authority model first. Test wrong role, expired/revoked authority, delegation scope, stale/unavailable verifier, and separation of identity from permission. Keep real national-ID integrations out of the critical path until governed and legally reviewed. |
| KSR-004 | Deterministic rule evaluation with pinned rule/version context | `SPECIFICATION.md` §2; `SYSTEM_INTEGRATION_ROADMAP.md` | Determinism is a stated principle; rule source/version semantics and end-to-end conformance are not yet frozen. | Specified conceptually; open for conformance | Define evaluator inputs, rule-version pinning, effective dates, conflict/unknown outcomes, and deterministic output contract. Add repeatability, version-mismatch, and unknown-rule tests. |
| KSR-005 | Protected state transition has an explicit lifecycle | `SPECIFICATION.md` §4; CEE conformance trace | A local IVM lifecycle is cited as prototype evidence. The specification explicitly says it does not establish distributed atomicity, power-loss durability, or concurrency safety. | Prototype evidence only for a bounded local slice | Map every normative lifecycle state and transition to source and tests. Test failures at every boundary, including restart, state/history consistency, and idempotent recovery. Do not claim distributed atomicity from local SQLite tests. |
| KSR-006 | Idempotency, replay prevention, and conflicting-ID rejection | `SPECIFICATION.md` §2; `docs/OBSERVABLE_RESULT_VECTOR.md` | The test strategy reports local SQLite scenarios for duplicate submissions, concurrent duplicate submissions, and retry after simulated acknowledgement loss. Exact guarantees are limited to the documented local test environment. | Prototype evidence, bounded | Trace tests and implementation; record exact tested revision and environment. Add conflicting identifier with changed payload, replay across audiences/cases/versions, and multi-node retry cases. |
| KSR-007 | State and history remain consistent through faults/recovery | `SPECIFICATION.md`; `TEST_STRATEGY.md`; `THREAT_MODEL.md` | Local file-backed SQLite tests include termination before local commit and after commit before simulated acknowledgement. Power-loss, storage-device faults, and distributed recovery are explicitly not established. | Prototype evidence, bounded | Define durability contract and storage assumptions. Add process restart, filesystem/storage fault where feasible, journal corruption, interrupted history attachment, backup/restore, and multi-node recovery tests. State clearly what cannot be tested in CI. |
| KSR-008 | Multi-institution coordination preserves independent authority | `SYSTEM_INTEGRATION_ROADMAP.md`; `docs/INTEGRATED_USE_CASE.md`; `docs/INSTITUTIONAL_COORDINATION_TEST_MATRIX.md` | Documentation describes a research scenario and a limited two-institution experiment; full end-to-end cross-domain workflow is not claimed. | Experimental / incomplete | Run a full scenario with separate authorities and rule versions, explicit hand-offs, rejection, uncertainty, correction/dispute path, and recovery. Capture each institution's decision and state independently. |
| KSR-009 | Payment/economic effects cannot be duplicated or falsely declared settled | `SPECIFICATION.md` §6; `docs/OBSERVABLE_RESULT_VECTOR.md` | Economic-effect invariants are stated. Payment reconciliation, disputes/corrections, and settlement integration remain unimplemented according to `TEST_STRATEGY.md`. $KIN semantics are open. | Open for integrated behaviour | Do not issue real funds or represent settlement as proven. Implement a simulated payment adapter with explicit pending/settled/failed/unknown states, idempotency, reconciliation, dispute/correction, and test provider timeout/duplicate callback. Define $KIN only if a demonstrated use case requires it. |
| KSR-010 | External evidence is distinguishable from verified real-world truth | `THREAT_MODEL.md`; `SYSTEM_INTEGRATION_ROADMAP.md`; `SPECIFICATION.md` | Trust limitations are documented: signatures/ledgers do not prove the truth of external facts. A complete evidence-provenance/dispute workflow is not demonstrated. | Specified principle; system evidence open | Define source identity, provenance, freshness, challenge/dispute status, and the authorised decision-maker. Use synthetic fixtures for demos and visibly label them as synthetic. |
| KSR-011 | MINAH acts as jurisdiction-specific jurisprudential oracle and sovereignty gatekeeper while remaining inside independently enforced authority boundaries | `SPECIFICATION.md` §7; `docs/MINAH_JURISPRUDENTIAL_ORACLE.md`; `SYSTEM_INTEGRATION_ROADMAP.md` | Intended mandate and required source/version, API, disposition, uncertainty, escalation, and enforcement boundaries are specified. The current deterministic stand-in is synthetic and is not the full MINAH. Two MINAH boundary tests passed in CI run 39787768780; no production jurisdiction-specific legal integration is claimed. | Contract specified; implementation/evidence open | Implement versioned jurisdiction profiles and governed legal-source/API adapters; create expert-reviewed evaluation sets. Test jurisdiction isolation, source provenance/effective dates, stale/conflicting evidence, reserved decision rights, malformed/adversarial output, failure/defer paths, and that IVM/CEE-informed execution rejects forbidden actions regardless of MINAH's recommendation. | 
| KSR-012 | CEE/IVM-informed execution conforms to its own requirements | `docs/CEE_CONFORMANCE_TRACE.md`; `TEST_STRATEGY.md` | A full-source CEE conformance trace exists, but the test strategy explicitly warns that a local unit-test pass is not full CEE conformance or integrated KIN-SYS evidence. | Partial evidence; exact coverage to audit | Trace every mandatory CEE requirement to implementation lines and tests; classify covered, partial, absent, and not applicable. Add negative tests proving protected operations cannot bypass required checks. |
| KSR-013 | Privacy and proof-backed claims are bounded and correctly described | `SPECIFICATION.md` §2/§5; `THREAT_MODEL.md` | Privacy-by-purpose and proof limitations are documented. No production ZK pipeline is claimed; proof correctness does not establish truth of external inputs. | Open for implementation/evaluation | Do not add ZK just for appearance. First specify the privacy use case and conventional baseline. If justified, define statement, public/private inputs, case/version binding, replay/freshness, verifier behaviour, and resource/metadata leakage tests. |
| KSR-014 | Hardware independence with optional profile-specific security | `SPECIFICATION.md` §9; `DEPLOYMENT_PROFILES.md` | Software-first profiles and optional hardware enhancement are design targets. Physical Nexus Pod implementation and production HSM integration are not claimed. | Specified target; profile conformance unproven | Prove the core workflow on ordinary supported hardware first. Test constrained/offline semantics separately. Only add HSM/edge hardware where a threat model demonstrates need and measurable benefit. |
| KSR-015 | Conventional baseline fairly compared on the same scenarios | `docs/OBSERVABLE_RESULT_VECTOR.md`; `docs/COMPARATOR_ASSESSMENT.md`; `protocol-reference/comparators/conventional_sqlite.py` | A durable SQLite baseline and paired local comparison harness are described. The result vector requires same fixtures, raw outputs, pinned revisions, and unknowns preserved. | Partial comparison evidence | Re-run exact same fixtures against candidate and baseline. Publish source revisions, environment, raw outputs, normalized vector, repeats, limitations, and correctness before performance. Avoid claiming protocol superiority unless results support it. |
| KSR-016 | Governance, upgrades, disputes, and key incidents are actionable | `SPECIFICATION.md` §8; `THREAT_MODEL.md` | Required topics are listed, but complete operational governance and dispute/upgrade implementation is not demonstrated. | Open | Define roles, approval separation, signing, activation, compatibility, emergency limits, key rotation/revocation, appeal/correction, and audit trail. Test unauthorised upgrades and recovery from compromised keys. |
| KSR-017 | Reproducible developer setup and test run | `TEST_STRATEGY.md`; CI workflows | Documentation references a 30-test CI run for a specific historical run and explicitly limits what it proves. Current branch/commit status must be checked before making any current CI claim. | Partial / revision-specific | Record exact setup commands, runtime and dependency versions, deterministic fixtures, expected output, and current CI URL. Verify a fresh checkout can reproduce the suite without secrets or manual undocumented setup. |
| KSR-018 | Observable results, logs, and operational recovery | `docs/OBSERVABLE_RESULT_VECTOR.md`; `THREAT_MODEL.md` | The result-vector contract is documented; a complete production monitoring, alerting, backup/restore, incident response, and operations package is not established. | Contract drafted; operations open | Emit machine-readable decisions and stable reason codes; correlate requests and transitions without leaking secrets. Add health/readiness checks, backup/restore test, structured logs, metrics, alert thresholds, and operator runbook. |
| KSR-019 | A real user-facing demonstrator supports physical presentation | `SYSTEM_INTEGRATION_ROADMAP.md`; `docs/INTEGRATED_USE_CASE.md` | An initial integrated demonstrator is described, but the matrix has not yet confirmed the complete user interface, clean setup, or end-to-end workflow from source. | Must verify directly | Inspect the actual app entry point and run instructions. Produce one end-to-end scenario with a clean demo dataset, visible actors/permissions, accepted and rejected actions, state/history view, and reset/replay controls. Test on laptop and phone-sized viewport. |
| KSR-020 | Deployment is repeatable, observable, recoverable, and appropriately secured | `TEST_STRATEGY.md` §5; `THREAT_MODEL.md` | Production gate explicitly requires independent security review, realistic network/storage tests, operational procedures, key lifecycle, backup/recovery, monitoring, governance, privacy/legal review, and independent reproduction. These are not claimed complete. | Not production-ready on current documentary evidence | After the core slice is stable, deploy a bounded non-production pilot to a supported host. Validate secrets, TLS, access controls, persistence, backups, rollback, monitoring, incident response, and restore. Production use requires separate acceptance against the target institution and jurisdiction. |

## 4. Priority order for closing gaps

### Gate A — A runnable, honest core

1. Trace the actual source and tests for KSR-002 through KSR-007 and KSR-012.
2. Freeze the smallest useful message, authority, rule, transition, idempotency, and history contracts.
3. Make a fresh checkout run the core tests reproducibly.
4. Ensure a single local workflow visibly accepts valid requests and rejects invalid ones with stable reasons.
5. Keep all guarantees narrower than or equal to the evidence.

**Exit evidence:** requirement-to-source/test trace, reproducible command output, and raw test results attached to an exact commit.

### Gate B — A useful end-to-end institutional scenario

1. Select one bounded scenario from `docs/INTEGRATED_USE_CASE.md`; do not attempt every KIN-SYS component at once.
2. Define actors, independent authorities, rules, evidence, state, hand-offs, failure outcomes, and dispute/correction path.
3. Use explicit adapters/simulators for unfinished external systems and label them clearly.
4. Run the same scenario against a fair conventional baseline.
5. Publish the result vector, raw outputs, and limitations.

**Exit evidence:** one complete, repeatable scenario that works from user request through final observable outcome, including at least one denied action and one injected failure/retry.

### Gate C — Demonstration and deployment

1. Package the demonstrator with one-command local setup or a documented, reproducible deployment.
2. Test clean start, reset, restart, persistence, backup/restore, and operator recovery.
3. Provide a stable, shareable demonstration URL when hosting is available; do not make a public URL a substitute for security review.
4. Have an independent person reproduce the demo and test negative cases.
5. Only then choose the next pilot scope with a real institution and appropriate privacy, legal, and security review.

## 5. Source audit tasks still required

This matrix is intentionally conservative. The next code-level pass must replace generic “verify” entries with exact source paths, symbols/functions, test paths, test names, and run identifiers. At minimum inspect:

- repository tree and CI workflow files;
- executable entry points and the real integrated demonstrator;
- transition/authority/rule implementation;
- SQLite persistence and recovery code;
- comparator and paired scenario harness;
- CEE conformance implementation/tests;
- demo adapter boundaries and user interface;
- packaging, environment configuration, and deployment instructions.

If a test is absent, add it or record the gap. If a component is unnecessary for the selected use case, document the reason for excluding it rather than implementing it by default.

## 6. Reporting rule

Every project update should answer four separate questions:

1. **What changed?** Exact branch, commit, files, and links.
2. **What ran?** Exact command/workflow and result, with failures included.
3. **What is now demonstrated?** The narrow behaviour supported by evidence.
4. **What remains unproven?** Known gaps and the next acceptance criterion.

A passing test suite is a useful fact, not a synonym for deployment readiness. A deployment is a milestone, not proof that the protocol is correct or safe for every institution.

## 7. Definition of done for the first tangible milestone

The first milestone is not “all of KIN-SYS is complete.” It is a bounded but credible working demonstration:

- a fresh environment can install and run it using documented steps;
- two distinct institutional roles can participate without sharing authority;
- a permitted request creates exactly one visible state transition and traceable history entry;
- an unauthorised or invalid request is rejected and leaves protected state unchanged;
- duplicate submission/retry does not duplicate the logical effect within the declared test scope;
- restart preserves the documented committed result;
- an injected failure produces an explicit, recoverable outcome rather than a false success;
- the same scenario has a conventional baseline and a transparent comparison record;
- a reviewer can reproduce the demonstration and see the limitations.

This is a stepping stone to a production deployment, not a claim that the whole sovereign distributed system is already production-ready.
