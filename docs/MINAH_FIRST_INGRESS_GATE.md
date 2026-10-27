# MINAH-First Ingress Gate — Reference Implementation

**Status:** Experimental reference module and unit tests. Not yet wired into the HTTP service or the existing CEE case coordinator.  
**Copyright:** Joel Kagoro © 2026. See repository licensing and notice files.

## Why this slice exists

The current CEE case coordinator records a CEE request/admission/context phase before it calls its MINAH adapter. Although the existing coordinator consults MINAH before institutional decisions, the order does not yet satisfy the stronger architectural requirement that MINAH is the first protocol-facing recipient of every protected instruction.

This module makes that intended ingress order executable and testable at a boundary:

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

The next step is to wire this boundary into the actual protected HTTP ingress, remove or constrain any bypass routes, and test the complete MINAH → CEE → final-decision path against the running service.
