# Research Evidence Register

**Purpose:** Trace protocol decisions to sources and distinguish evidence from design intention. A link to a source is not itself validation of every claim in that source.

## 1. Primary research sources

| Source | Relevance | Evidence status and limits |
|---|---|---|
| [IVM Research Programme](https://github.com/joe933212-pixel/ivm-research) | Executable research on institutional transition semantics, authority/rule checks, state/history consistency, failure injection, recovery, and comparative conformance | The public research record describes prototype-level evidence and explicitly limits novelty, distributed guarantees, and production-readiness claims. Inspect experiment artifacts before relying on a guarantee. |
| [Published IVM research record, Zenodo DOI 10.5281/zenodo.22011725](https://doi.org/10.5281/zenodo.22011725) | Published research lineage and citation anchor | Use for provenance; map claims to the actual version and artifacts supporting them. |
| [KIN-SYS-v2](https://github.com/joe933212-pixel/KIN-SYS-v2) | Earlier concepts and prototype artifacts | Legacy source only. Documents, mock interfaces, generated values, and placeholder logic do not prove operational guarantees. |
| [KIN-SYS Protocol v2.1](https://github.com/joe933212-pixel/KIN-SYS-PROTOCOL-v2.1) | Earlier protocol/application concepts and integration notes | Legacy source only. Application setup instructions and example integrations are not adopted as the new specification without verification. |
| CRS, CISA, IVM, and CEE artifacts in the IVM research programme | Potential foundations and interfaces | Exact source documents, versions, and protocol mappings must be recorded before claiming dependency or conformance. |
| [Kagoro DSL](https://github.com/joe933212-pixel/Kagoro-DSL) | Independent compiler/language research potentially relevant to institutional authoring | Independent project; not a mandatory KIN-SYS dependency. |

## 2. IVM evidence reported by the research archive

The public IVM evidence chain reports progressive experiments from v0.1 to v0.4; a v0.3 fault matrix exposing a tested state/history commitment gap; a v0.4 local transaction record with the lifecycle PREPARED → STATE_COMMITTED → HISTORY_ATTACHED → FINALIZED; an Experiment 004A that terminates a worker and recovers the durable local transaction record in a fresh process, with a historical result of 23 tests run and 23 passed; and Experiment 005, in which a conventional deterministic state machine passes five tests against the current semantic contract.

These are reported results in the source archive and should be verified against the linked experiment files before being used in a deployment argument. They do **not** establish distributed atomicity, power-loss durability, concurrency safety, replication, general corruption recovery, novelty, commercial superiority, or production readiness. The source archive says novelty is not established and comparative research remains active.

## 3. Evidence classification

- **Proposed:** design objective or hypothesis.
- **Specified:** requirement is explicit and reviewable, not necessarily implemented.
- **Implemented:** code exists; this alone does not prove correctness.
- **Tested:** reproducible test demonstrates a defined property under stated conditions.
- **Independently reproduced:** another party repeats the test from published instructions.
- **Operationally validated:** property tested in a realistic deployment with recorded assumptions.
- **Not established:** evidence does not support the claim.
- **Contradicted/falsified:** evidence challenges or disproves the claim under stated conditions.

## 4. Claim-to-evidence rule

Every high-impact claim must identify the exact claim, source and version, environment and assumptions, test method and artifacts, observed result including failures, limitations, and the protocol requirement or decision it informs.

## 5. Open evidence work

- Trace exact CRS, CISA, and CEE source documents and versions.
- Map the agreed literature and prior-art sources to protocol claims.
- Link each adopted IVM property to precise experiment and test artifacts.
- Audit legacy KIN-SYS claims against actual code and test evidence.
- Record external review and independent replication separately from author-run results.

When evidence changes a requirement or invalidates a claim, update this register and the related decision/specification. Preserve earlier results and explain the change.
