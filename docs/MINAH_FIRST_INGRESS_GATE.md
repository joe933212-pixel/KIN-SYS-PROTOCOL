# MINAH-First Ingress Gate — Reference Implementation

**Status:** Experimental reference module and unit tests. The existing synthetic `/protocol/instructions` and `/cee/cases` paths have been reordered so MINAH review precedes CEE admission/context. The standalone `MinahFirstIngress` adapter gate is still not wired into HTTP.  
**Copyright:** Joel Kagoro © 2026. See repository licensing and notice files.

## Why this slice exists

The current CEE case coordinator records a CEE request/admission/context phase before it calls its MINAH adapter. Although the existing coordinator consults MINAH before institutional decisions, the order does not yet satisfy the stronger architectural requirement that MINAH is the first protocol-facing recipient of every protected instruction.

The standalone module makes the intended ingress order executable and testable at a boundary:

**MINAH → identity verification → action-specific authority verification → CEE → final decision.**

CEE remains responsible for the governed execution path, including institutional rules and IVM execution of CISA-defined semantics inside CEE. The module does not introduce an alternate execution engine; its CEE adapter must be the existing governed coordinator once integration is completed.

## Adapter contracts

- **MINAH:** first adapter to receive the instruction; returns explicit PASS, REJECTED, DEFERRED or UNKNOWN and a disposition reference. A PASS is preliminary and advisory, never institutional authorisation.
- **Identity:** verifies the principal using the institution's chosen identity adapter. Missing or non-true `verified` fails closed.
- **Authority:** verifies permission for this specific action, target, jurisdiction and context. Authentication is not authorisation. Missing or non-true `verified` fails closed.
- **CEE:** receives the instruction plus the recorded MINAH, identity and authority results. It must enforce applicable rule versions, institutional decisions, CISA/IVM semantics and effect controls.

No vendor, cloud, AI model, operating system, GPU or hardware is mandated. Adapters are injected by the deployment. Real national identity and institutional authority integrations are not implemented in this slice.

## Idempotency and uncertain outcomes

The SQLite ingress journal binds an operation ID to a canonical request fingerprint. Reusing an operation ID with different content raises a conflict. A completed replay returns the stored result without calling adapters again.

An operation left non-final after a process interruption returns UNKNOWN and requires reconciliation; it is never automatically sent to CEE a second time. This avoids blind resubmission but does not itself resolve whether a downstream action happened. Production integration must reconcile the ingress journal with durable CEE operation state and provider evidence.

## What tests prove

The unit tests use fake adapters to verify call order, fail-closed MINAH/identity/authority outcomes, conflict detection, replay behaviour, and no automatic CEE resubmission for an interrupted operation.

They do **not** prove:
- that the current HTTP routes use this gate;
- that real identity or authority sources are contacted or trusted;
- that the CEE coordinator is bypass-resistant;
- distributed atomicity, payment finality, legal correctness or production security.

The current HTTP integration is an incremental ordering correction, not a complete implementation of the standalone gate. It does not yet wire real identity/authority adapters or inject their verified results into the CEE coordinator. The legacy `/demo/instructions` route remains a synthetic demonstration and must not be treated as a production protected ingress. Next: add explicit provider-neutral identity and action-specific authority adapter contracts to the real HTTP ingress, ensure every protected route uses one governed path, and test the complete MINAH → identity → authority → CEE → final-decision sequence against the running service.
