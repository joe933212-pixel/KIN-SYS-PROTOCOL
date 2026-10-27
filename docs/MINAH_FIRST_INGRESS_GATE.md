# MINAH-First Ingress Gate — Reference Implementation

**Status:** Experimental reference module plus HTTP integration of provider-neutral adapter injection on `/protocol/instructions`. The HTTP route uses the existing pipeline; the standalone `MinahFirstIngress` journal is still not wired into HTTP, and the legacy `/cee/cases` flow is a separate opt-in synthetic demo.  
**Copyright:** Joel Kagoro © 2026. See repository licensing and notice files.

## Why this slice exists

The earlier CEE case coordinator recorded admission/context before its MINAH adapter, which did not satisfy the stronger MINAH-first requirement. The `/protocol/instructions` pipeline has since been reordered, and the HTTP app now accepts injected MINAH, identity and action-authority adapters and passes them into that same pipeline. The legacy `/cee/cases` route remains disabled by default because it is not yet unified with this ingress.

The standalone module makes the intended ingress order executable and testable at a boundary:

**MINAH → identity verification → action-specific authority verification → CEE → final decision.**

CEE remains responsible for the governed execution path, including institutional rules and IVM execution of CISA-defined semantics inside CEE. The module does not introduce an alternate execution engine; its CEE adapter must be the existing governed coordinator once integration is completed.

## Adapter contracts

- **MINAH:** first adapter to receive the instruction. The institution-owned agent may perform its own approved identity and authority API calls through `verify_principal(request)` and `verify_action(request)`. If those methods are not provided, the pipeline uses the separately injected identity and authority adapters. Accepted dispositions are `ADVISORY_ONLY` (continue to the next required gate), `NEEDS_INFORMATION` (defer), `REJECTED` (stop), or `UNKNOWN` (stop and require resolution). These dispositions are not institutional authorisation.
- **Identity:** MINAH may coordinate verification of the principal against the institution's approved identity APIs. The pipeline requires a structured result with `verified: true`; missing, false, or malformed results are rejected. Exceptions/timeouts from the verification adapter produce `UNKNOWN`, stop the operation before the next gate, and require resolution; they are never treated as successful verification. The current default remains a synthetic stub, not a live identity integration.
- **Authority:** MINAH may coordinate action-specific authority checks for the actor, action, target, jurisdiction and context. Authentication is not authorisation. The pipeline requires a structured result with `verified: true`; exceptions/timeouts produce `UNKNOWN` and stop before admission/IVM. The current default remains a synthetic stub, not a live legal or institutional authority integration.
- **CEE:** receives the instruction plus the recorded MINAH, identity and authority results. It must enforce applicable rule versions, institutional decisions, CISA/IVM semantics and effect controls.

No vendor, cloud, AI model, operating system, GPU or hardware is mandated. Adapters are injected by the deployment. Real national identity and institutional authority integrations are not implemented in this slice.

## Idempotency and uncertain outcomes

The SQLite ingress journal binds an operation ID to a canonical request fingerprint. Reusing an operation ID with different content raises a conflict. A completed replay returns the stored result without calling adapters again.

An operation left non-final after a process interruption returns UNKNOWN and requires reconciliation; it is never automatically sent to CEE a second time. This avoids blind resubmission but does not itself resolve whether a downstream action happened. Production integration must reconcile the ingress journal with durable CEE operation state and provider evidence.

## What tests prove

The tests use fake adapters to verify call order, fail-closed MINAH/identity/authority outcomes, including explicit MINAH rejection and unknown dispositions stopping before identity/CEE, conflict detection, replay behaviour, and no automatic CEE resubmission for an interrupted operation.

They do **not** prove:
- that the current HTTP routes use this gate;
- that real identity or authority sources are contacted or trusted;
- that the CEE coordinator is bypass-resistant;
- distributed atomicity, payment finality, legal correctness or production security.

The HTTP application accepts `minah_adapter`, `identity_adapter` and `authority_adapter` through `create_app(...)`; these are passed into the existing `KinSysProtocolPipeline` used by `/protocol/instructions`. An institution-owned MINAH adapter can implement `review`, `verify_principal`, and `verify_action` so it orchestrates its approved API integrations while the pipeline retains distinct fail-closed identity and authority gates. The default adapters remain synthetic. HTTP-level tests exercise injected adapters and verify order and early rejection before CEE admission/IVM. A live loopback HTTP test also sends a request through the local WSGI server and verifies the same adapter order; it uses test adapters, not real identity or legal-authority services. This does not make the standalone `MinahFirstIngress` journal part of HTTP, does not integrate the opt-in legacy `/cee/cases` path, and does not provide production identity or legal-authority verification. The legacy `/demo/instructions` route is also disabled by default. Next: define production adapter trust/error/timeout/revocation contracts, add reconciliation across ingress and CEE state, and decide how to deprecate or unify legacy paths without creating an alternate execution route. The live loopback test covers the current single-threaded reference server; it does not establish multi-worker/thread safety.
