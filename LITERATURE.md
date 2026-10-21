# Literature and Prior-Art Map

**Purpose:** Preserve the agreed research lineage and prior-art comparison set, and map each source to a question KIN-SYS must answer. Listing a source does not imply endorsement, novelty, or proof of a KIN-SYS property.

## 1. Research lineage

The established research lineage is:

**CRS → CISA → IVM → CEE**

These are related research artifacts, not interchangeable names. KIN-SYS is the wider protocol/ecosystem objective. Its specification must explain which requirements each artifact informs and must not collapse them into one implementation without evidence.

| Artifact | Role in the research lineage | Work required before adopting it as a protocol dependency |
|---|---|---|
| CRS — Coordination / institutional source specification | Foundational model in the research lineage | Identify the authoritative version and extract its normative concepts, invariants, and limitations. |
| CISA — Coordination Instruction Set Architecture | Draft execution model defining deterministic instructions/runtime semantics for coordinated institutional state | Trace each adopted primitive and guarantee to the actual source draft and conformance criteria. |
| IVM — Institutional Virtual Machine | Executable research programme for institutional transition semantics and failure/recovery experiments | Map specific tested properties to exact versions, experiments, and results; do not generalise local prototype results. |
| CEE — Coordination Execution Engine | Runtime/execution layer in the research lineage | Establish the exact source version, boundary with CISA, implementation status, and conformance evidence. |

The public [IVM research repository](https://github.com/joe933212-pixel/ivm-research) contains the available source artifacts and research record. Its prior-art status notes that the full standalone document containing Sections 1–53 of the literature analysis was not located during a repository assembly, with a continuation point at Section 54. Do not reconstruct the missing sections from memory or secondary summaries. The complete agreed source analysis should be reconciled against its original document when available.

The published research record is available at [Zenodo DOI 10.5281/zenodo.22011725](https://doi.org/10.5281/zenodo.22011725).

## 2. Agreed prior-art comparison set

The previously identified comparison set includes:

- **Lingua Franca** — coordination-oriented language/runtime work. Compare coordination semantics, execution model, determinism assumptions, and failure behaviour. Project site: https://www.lingua-franca.dev/
- **L4 DSL** — compare the actual language/runtime scope and semantics in the identified source repository before drawing conclusions. Exact source reference must be reconciled with the original prior-art notes.
- **xDSL** — compiler infrastructure and extensible IR ecosystem: https://github.com/xdslproject/xdsl
- **LLVM** — compiler infrastructure and intermediate representation ecosystem: https://llvm.org/
- **MLIR** — multi-level intermediate representation and compiler infrastructure: https://mlir.llvm.org/
- **TileLang** — domain-specific language/compiler work; compare only where its actual execution and compilation scope is relevant: https://github.com/tile-ai/tilelang
- **Martin Fowler, Domain-Specific Languages** — background on DSL concepts and design trade-offs: https://martinfowler.com/dsl.html
- **Tangled** — included in the previously identified comparison set; canonical source and relevance must be confirmed from the original notes before citation.

These sources primarily inform the evaluation of computational abstractions, language/runtime boundaries, and prior art. They do not automatically establish the correctness, novelty, security, or necessity of KIN-SYS. Comparisons must be claim-specific and fair to existing systems.

## 3. How the literature should be used

For each source, record:
1. exact title, author/maintainer, version/date, and canonical URL;
2. the specific concept or claim being compared;
3. what the source actually demonstrates or specifies;
4. where KIN-SYS/CRS/CISA/IVM/CEE overlaps or differs;
5. whether the comparison is conceptual, specification-level, experimentally tested, or independently reproduced;
6. the conclusion and uncertainty, including evidence that challenges the proposed architecture.

Do not describe an idea as novel merely because terminology differs. If an existing system provides the same property, record that result. If the evidence is insufficient, leave the question open.

## 4. Protocol-level literature still to connect

The prior-art set above is not a substitute for a protocol-specific review. As architecture decisions are made, the register must also connect relevant sources to distributed failure, trust federation, key lifecycle, privacy, edge/offline reconciliation, governance, and economic-accounting requirements. Each addition must answer a concrete design question rather than serve as decoration.

## 5. Separation from Kagoro DSL

Kagoro DSL remains an independent project. Its repository should not be used as a place to document KIN-SYS, CRS, CISA, IVM, or CEE dependencies. Any future interface between Kagoro DSL and KIN-SYS must be optional and specified at a stable boundary.
