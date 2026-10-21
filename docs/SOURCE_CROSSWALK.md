# Source Crosswalk — CRS, CISA, IVM, CEE and KIN-SYS

**Status:** Working research map, 2026-10-21. It is not a conformance declaration.

## 1. Source inventory and version discipline

| Layer | Source currently identified | What it governs | Handling rule |
|---|---|---|---|
| CRS | Public IVM archive: `specifications/crs/CRS_Draft_2026-08-06.docx`; Library also contains a fuller searchable `CRS.docx` draft | Implementation-independent coordination requirements and failure properties | Reconcile the version lineage and normative requirements before treating the copies as identical. |
| CISA | Public IVM archive: `specifications/cisa/CISA_Draft_0.1.docx` and machine-extracted `CISA_Draft_0.1.extracted.md` | Instruction set, execution primitives, deterministic rules, authority and transition semantics | The DOCX is authoritative over extracted Markdown. Do not assume Draft 0.1 is the latest CISA source without version reconciliation. |
| IVM | Library: `Institutional Virtual Machine.docx`, Version 1.0 Draft; public archive currently includes front matter only | Abstract execution architecture that realizes CISA semantics | Review the full Library specification directly. The front matter alone cannot ground a complete implementation contract. |
| CEE | Library: `CEE(2).docx`, CEE 1.0 Draft, 335 pages; the public IVM archive inspected does not contain this full document | Operational environment for execution, state, authority, rules, commitment, history, recovery, verification and external interfaces | Use the full source for the operational lifecycle. Do not infer CEE requirements from the CISA title alone. |
| Research evidence | Public IVM archive: runtime versions v0.1–v0.4, Experiment 004A and Experiment 005 | Prototype-level observations and comparative testing | Preserve the distinction between historical source-reported results, a fresh rerun, and independent reproduction. |
| Prior art | Public IVM archive: `literature/source/PRIOR-ART AND LITERATURE ANALYSIS.docx`; the user's Library contains the complete 75-page analysis | Prior-art boundary and falsifiable research hypothesis | Do not reconstruct missing extracted text from memory. Compare semantics, not names or implementation branding. |

The source lineage is **CRS → CISA → IVM → CEE**. KIN-SYS is the broader protocol architecture/ecosystem under investigation; it does not replace these research artefacts, and this crosswalk does not make them dependencies of Kagoro DSL.

## 2. Relevant normative findings

### CRS — requirements before implementation

The CRS frames coordination as maintaining one shared institutional reality among independent actors, authorities and decisions. Its relevant requirement themes include one authoritative current state per process, explicit authority, deterministic behaviour, immutable history, replay, behavioural equivalence, and preventing partial or unauthorised transitions from becoming authoritative. The exact clause numbers must be taken from the version ultimately designated as the source of record.

### CISA — execution semantics

CISA Draft 0.1 defines runtime primitives including Process, State, Event, Transition, Rule, Authority and Actor. It requires explicit transitions, deterministic side-effect-free rule evaluation, authority verification before transition execution, and an auditable record of executed and rejected events. Its extracted execution sequence is:

`Receive Event → Authenticate Actor → Verify Signature → Verify Authority → Evaluate Rules → Determine Transition → Execute Transition → Persist History → Publish Updated State`

This sequence and the CEE lifecycle should be reconciled rather than mechanically merged: CISA describes the instruction/execution semantics; CEE describes the operational lifecycle around them.

### IVM — abstract machine and historical semantics

The full IVM 1.0 Draft in the Library is the relevant source for the abstract machine's state, context, execution and verification semantics. The public archive's front matter is not enough to settle these details. The v0.4 runtime's local journal phases are experimental implementation detail, not a substitute for the full IVM specification.

### CEE — execution lifecycle, commitment and recovery

The full CEE 1.0 Draft defines the operational lifecycle:

`REQUESTED → ADMITTED → CONTEXT_ESTABLISHED → AUTHORITY_EVALUATED → EXECUTING → PREPARED → COMMITTING → COMMITTED → FINALIZED`

The draft explicitly separates provisional execution from institutional commitment. Admission does not grant authority; authentication alone does not prove authority; rule evaluation alone does not commit a result; and an external acknowledgement does not define the commitment boundary.

CEE Chapter 7 requires observable atomicity between Authoritative State and the corresponding committed History. Chapter 9 treats recovery as restoration of the last valid institutional truth, not blind re-execution: after interruption, the environment must determine whether commitment occurred, preserve the corresponding state/history relationship, and avoid creating a second institutional transition when communication is retried.

The lifecycle above is a source requirement. The local KIN-SYS prototype does not yet implement it as a persisted lifecycle state machine.

## 3. What the current local prototype does—and does not—cover

The files under `protocol-reference/` implement a deliberately narrow local SQLite example:

- one process starts in `PENDING`;
- one configured actor may request `ACTIVATE`;
- a stored rule-version and eligibility flag gate the transition;
- identical event IDs with identical request content are treated as duplicates;
- conflicting reuse of an event ID is rejected;
- a successful transition writes state and history inside one local database transaction;
- structurally valid authority/rule/transition rejections are retained as event outcomes.

This is a prototype of selected local properties only. It currently does **not** implement the complete CISA instruction set, the full IVM execution model, or the CEE lifecycle. It does not implement cryptographic authentication/signatures, durable phase-by-phase recovery, process-death recovery, concurrent multi-process semantics, distributed commitment, cross-institution authority, or an independently verified result vector. The test source is committed, but its exact clean execution has not yet been verified in this session.

## 4. Comparative research constraint

The existing Experiment 005 conventional state-machine comparator passes five tests for its current canonical contract. That result is a substantive challenge, not a reason to dismiss the comparator. The prior-art analysis explicitly permits a negative result: if conventional models preserve the full semantic vector, the appropriate conclusion may be that IVM is a useful synthesis or integration architecture rather than a distinct machine abstraction.

The next comparison must give every implementation the same inputs and the same observable result vector. At minimum:

- `transition_id`, `event_id`;
- `pre_state`, `post_state`;
- authority identity and authority outcome;
- rule-set identity and rule version;
- commitment status and history reference;
- replay result, recovery result and duplicate-event outcome;
- failure/rejection reason and whether authoritative state changed.

Do not give the IVM/KIN-SYS implementation stronger assumptions or more favourable tests than the conventional comparator.

## 5. Required next steps

1. Reconcile the exact CRS, CISA, full IVM and CEE source versions and record source hashes/version identifiers.
2. Trace each contract requirement to its source clause and distinguish mandatory requirements from design interpretation.
3. Extend the local slice to emit the common Experiment 005 result vector without claiming that it is already equivalent to the IVM runtime.
4. Test the CEE lifecycle boundaries, especially failure before commitment, failure after commitment but before acknowledgement, duplicate submission after recovery, authority revocation, rule-version preservation, and state/history consistency.
5. Compare against the conventional state machine using the same vectors. Add event-sourcing, workflow and transaction-oriented comparators only as separate controlled experiments.
6. Report results only after clean reproducible execution; then arrange independent reproduction. No result in this crosswalk establishes novelty or production readiness.
