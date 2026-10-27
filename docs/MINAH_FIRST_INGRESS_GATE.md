# MINAH-First Ingress Gate — Reference Implementation

**Status:** The durable `MinahFirstIngress` journal is wired into `POST /protocol/instructions` for the v0.1 instruction envelope. It delegates actual gate execution to the existing `KinSysProtocolPipeline`; it does not create a second execution engine. The earlier internal request shape remains a compatibility path directly through that same pipeline. The legacy `/cee/cases` flow remains a separate opt-in synthetic demo.  
**Copyright:** Joel Kagoro © 2026. See repository licensing and notice files.

## Why this slice exists

The earlier CEE case coordinator recorded admission/context before its MINAH adapter, which did not satisfy the stronger MINAH-first requirement. The `/protocol/instructions` pipeline enforces MINAH-first ordering, then identity and action-specific authority checks before CEE admission and IVM execution. For v0.1 instruction envelopes, the HTTP app now records the canonical envelope in the ingress journal before delegating to that pipeline. The legacy `/cee/cases` route remains disabled by default because it is not unified with the governed instruction flow.

The module supports two modes. Its standalone adapter mode is a conformance harness for isolated tests. Its HTTP-integrated mode persists the v0.1 instruction at ingress and delegates execution to the canonical pipeline. The actual gate order remains:

**MINAH → identity verification → action-specific authority verification → CEE → final decision.**

CEE remains responsible for the governed execution path, including institutional rules and IVM execution of CISA-defined semantics inside CEE. The HTTP-integrated mode does not introduce an alternate execution engine.

## Adapter contracts

- **MINAH:** first adapter to receive the instruction. The institution-owned agent may perform its own approved identity and authority API calls through `verify_principal(request)` and `verify_action(request)`. If those methods are not provided, the pipeline uses the separately injected identity and authority adapters. Accepted dispositions are `ADVISORY_ONLY` (continue to the next required gate), `NEEDS_INFORMATION` (defer), `REJECTED` (stop), or `UNKNOWN` (stop and require resolution). These dispositions are not institutional authorisation.
- **Identity:** MINAH may coordinate verification of the principal against the institution's approved identity APIs. The pipeline requires a structured result with `verified: true`; missing, false, or malformed results are rejected. Exceptions/timeouts from the verification adapter produce `UNKNOWN`, stop the operation before the next gate, and require resolution; they are never treated as successful verification. The current default remains a synthetic stub, not a live identity integration.
- **Authority:** MINAH may coordinate action-specific authority checks for the actor, action, target, jurisdiction and context. Authentication is not authorisation. The pipeline requires a structured result with `verified: true`; exceptions/timeouts produce `UNKNOWN` and stop before admission/IVM. The current default remains a synthetic stub, not a live legal or institutional authority integration.
- **CEE:** receives the instruction plus the recorded MINAH, identity and authority results. It must enforce applicable rule versions, institutional decisions, CISA/IVM semantics and effect controls.

No vendor, cloud, AI model, operating system, GPU or hardware is mandated. Adapters are injected by the deployment. Real national identity and institutional authority integrations are not implemented in this slice.

## Idempotency and uncertain outcomes

The SQLite ingress journal binds an operation ID to a canonical request fingerprint. Reusing an operation ID with different content raises a conflict. For a completed v0.1 HTTP operation, replay returns the stored decision and pipeline result without invoking the pipeline again. The local server persists this journal at `KIN_SYS_INGRESS_DB`, defaulting to `.kin-sys-prototype/ingress.sqlite3`; tests may use an in-memory or temporary database.

An operation left non-final after a process interruption returns UNKNOWN and is never automatically resubmitted by ordinary request replay or status inspection. `GET /protocol/operations/{operation_id}` reads the ingress journal first and may copy an already-finalized CEE result into it; this is passive reconciliation and does not restart execution. For an existing ingress operation whose CEE journal is non-final, an operator or trusted orchestrator may explicitly call `POST /protocol/operations/{operation_id}/recover`. Recovery accepts no replacement instruction: it uses the request and profile already pinned in CEE, refuses early states without a durable MINAH disposition, and resumes the canonical pipeline from a recoverable lifecycle phase. During `COMMITTING`, the stable IVM event ID lets the local runtime recognize an already committed transition instead of applying it twice. If required pinned evidence is missing or the phase is ambiguous, recovery returns UNKNOWN and leaves the operation for manual reconciliation. These guarantees apply to the local SQLite reference stores; they do not establish distributed atomicity or external payment finality.

## What tests prove

The tests use fake adapters to verify call order, fail-closed MINAH/identity/authority outcomes, explicit rejection and deferral before downstream checks, conflict detection, digest tampering, decision-envelope binding, durable replay after reopening the journal, refusal to recover early ambiguous states, idempotent recovery after an IVM commit/CEE acknowledgement gap, and explicit HTTP recovery. The v0.1 HTTP path uses the durable ingress journal and delegates gate execution and recovery to the same canonical pipeline.

They do **not** prove:
- that real identity or authority sources are contacted or trusted;
- that the CEE coordinator is bypass-resistant under hostile concurrent or multi-worker conditions;
- reconciliation between ingress and CEE/IVM stores after a crash;
- distributed atomicity, payment finality, legal correctness or production security.

The HTTP application accepts `minah_adapter`, `identity_adapter` and `authority_adapter` through `create_app(...)`; these are passed into the canonical `KinSysProtocolPipeline`. For v0.1 instruction envelopes, the HTTP route records the envelope in `MinahFirstIngress` and then invokes that pipeline. The earlier internal request shape still calls the same pipeline directly and is retained only as an explicit compatibility path: it is disabled by default and requires `enable_legacy_demo_routes=True`. Governed v0.1 envelopes use the durable ingress journal. An institution-owned MINAH adapter can implement `review`, `verify_principal`, and `verify_action` so it orchestrates approved API integrations while the pipeline retains distinct fail-closed identity and authority gates. Default adapters remain synthetic. The legacy `/cee/cases` and `/demo/instructions` routes remain opt-in synthetic demonstrations, not alternate production instruction routes. Next: define production adapter trust/error/timeout/revocation contracts, reconcile ingress with CEE/IVM state after a crash, and decide how to deprecate or unify legacy routes. The live loopback test covers the current single-threaded reference server; it does not establish multi-worker/thread safety.
