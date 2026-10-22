> **Authorship & rights:** Joel Kagoro · Copyright © 2026 Joel Kagoro · Licensed under Apache-2.0; see `LICENSE`, `NOTICE`, and `COPYRIGHT.md`.

# Integrated Demonstrator Interface Contracts

**Status:** Research contract draft 0.1  
**Scope:** Contracts for the synthetic integrated demonstrator only. These are not production API specifications or legal requirements.

## 1. Shared case envelope

Every adapter invocation should be traceable to a case and operation. The initial contract uses the following conceptual envelope:

```json
{
  "contract_version": "0.1",
  "case_id": "DEMO-CONSIGNMENT-001",
  "operation_id": "DEMO-CONSIGNMENT-001:ORIGIN:1",
  "actor_ref": "ORIGIN-A",
  "action": "DECIDE_ORIGIN",
  "rule_pack_version": "UG-SYNTHETIC-1.0",
  "evidence_refs": ["SYNTHETIC-RULE-PACK-1"],
  "execution_profile": "CENTRAL_DEMO_ONLINE"
}
```

The example is illustrative. Real identifiers and evidence must be opaque references where possible; secrets and full identity documents must not be copied into generic event envelopes.

## 2. Contract responsibilities

| Boundary | Request must identify | Response must expose | Forbidden shortcut |
|---|---|---|---|
| Identity / authority | Principal reference, action, case scope, requested purpose | Allow/deny, reason, credential/policy version, expiry/revocation basis | A valid identity alone must not imply permission for every action |
| Institutional decision | Case, stage, actor, requested decision, event ID, rule version | Accepted/rejected status, reason, recorded decision, history reference | Coordinator or AI cannot impersonate the institution |
| MINAH AI adapter | Evidence references, supplied rule-pack references, requested task | Advisory, cited source references, adapter/model version, uncertainty, missing evidence | AI output cannot grant authority, silently amend law, or directly commit a decision |
| Rule / statutory assessment | Approved inputs, rule-pack ID/version, currency and units, exemption/deferment evidence | Calculation inputs, result, rule version, legal status of rule source | A synthetic rate must never be represented as actual law |
| Payment lifecycle | Obligation ID, stable payment ID, amount, currency, source assessment | Separate instruction, provider acknowledgement, settlement evidence, reconciliation state | Instruction or provider acceptance is not proof of settlement; unknown outcomes must not trigger blind resubmission |
| Edge execution profile | Operation, local policy, connectivity and storage state | Accepted/deferred/rejected result, queued operation ID, sync status | Offline mode cannot invent online authority or fabricate external confirmation |
| Audit/history | Event ID, case ID, actor and authority references, before/after state, evidence references | Append-only event reference and integrity metadata | Correction must not erase the original decision or its history |

## 3. Common result vocabulary

Use explicit results rather than a single boolean:

- `ACCEPTED`: the requested local operation passed its declared checks and was recorded.
- `REJECTED`: the requested operation was denied; the response must include a stable reason code.
- `NOT_ATTEMPTED`: a prerequisite was not met, so this operation was not sent to the downstream component.
- `PENDING`: the workflow is waiting for a required action or evidence.
- `UNKNOWN_PENDING_RECONCILIATION`: an external effect may have occurred, but evidence is insufficient to assert success or failure.
- `COMMITTED`: the declared local commitment boundary was crossed.
- `FINALIZED`: all requirements for the declared workflow's final state are met.
- `SIMULATED_*`: the outcome belongs to a test adapter, not a live institution or provider.

These statuses are not interchangeable. A locally accepted payment instruction is not proof of settlement; a committed stage is not necessarily a finalized case.

## 4. Sequencing and prerequisite policy

For the current consignment scenario:

1. The exporter may submit a case only through an authorised submission capability.
2. Origin authority must accept and approve its stage before the destination decision is attempted.
3. If origin authority is revoked, the actor is unauthorised, or the expected rule version does not match, record the rejection and do not attempt the destination stage.
4. Destination approval remains independent; origin approval does not force it.
5. Statutory assessment is permitted only after both required institutional stages approve.
6. Payment is invoked only for a positive assessed obligation under the active synthetic rule pack. Exemption is represented explicitly.
7. A payment timeout remains unresolved until reconciliation; no downstream status may label it paid merely because an instruction was submitted.
8. AI advisory output is recorded alongside the case but does not itself satisfy an institutional approval, evidence requirement, or payment confirmation.

Any production use must replace this illustrative sequence with a workflow agreed by the competent institutions and grounded in authoritative rules.

## 5. Currency and amount handling

The current demo uses integer UGX amounts and basis-point arithmetic for its fictional rate. The name of an amount field must state its unit/currency. A production contract must additionally define rounding rules, currency precision, conversion sources and timestamps (if conversion is permitted), exemption evidence, refunds, reversals, partial payments, and reconciliation.

No real tax rate, duty, fee, exemption, or remittance recipient is established by this prototype.

## 6. Error and retry semantics

- Reuse the same operation ID only for the same semantic request.
- Reusing an operation ID with different content must be rejected as a conflict.
- Retry after an acknowledgement is lost must return the previously recorded result or trigger explicit reconciliation; it must not duplicate an economic effect.
- Revocation and rule-version checks must occur at the protected operation boundary, not only when a user signs in.
- A network or adapter failure must be distinguishable from a valid institutional refusal.
- Missing, stale, contradictory, or unverifiable evidence must be surfaced rather than silently normalised.
- If a required component is unavailable, return a documented pending/deferred result rather than inventing a successful result.
- The payment lifecycle reference model records stable instruction IDs and separates acknowledgement from settlement evidence. Its current scope does not prove provider-side idempotency or eliminate duplicate effects across a real network; those guarantees require a provider contract and integration tests.

## 7. Versioning and compatibility

Every request/response contract and rule pack should carry a version. Changes to required fields, decision semantics, amount units, status meanings, authority checks, or commitment behaviour require a versioned change and regression tests. An in-progress case must not silently adopt a new rule version; the applicable policy must specify whether it remains pinned, is re-evaluated, or is escalated for an authorised decision.

## 8. Current implementation map

- Implemented in the initial demo: synthetic identity gate, deterministic non-authoritative MINAH adapter, fictional UGX obligation calculation, simulated payment statuses, and online/disconnected profile selection.
- Implemented as a reference model and wired into the synthetic integrated demo: SQLite-backed obligation/instruction records, stable payment IDs, provider acknowledgement states, matching simulated settlement evidence, reconciliation findings, and event-ID conflict detection. A separately implemented conventional payment-lifecycle baseline is tested against the same observable payment contract. The demo labels settlement as simulated; no real funds move.
- Reused from the research workflow: separate origin/destination stages, actor and authority checks, rule-version checks, event IDs, stage history, and correction/dispute research operations.
- A matched synthetic whole-flow comparator now exists in `protocol-reference/comparators/paired_integrated_harness.py`, covering nine scenarios with a limited shared observable result vector. Still incomplete: a formal serialised adapter API, a live evidence store, production credential validation, real AI model evaluation, authoritative tax rules, real payment-provider reconciliation, cryptographic audit proofs, distributed execution, and physical edge-node operation.

## 9. Contract acceptance criteria

A contract is ready for broader implementation only when tests demonstrate:

- accepted and rejected requests with stable reason codes;
- no downstream action after a failed prerequisite;
- independent institutional decisions;
- revocation and rule-version drift handling;
- AI cannot authorise or alter a decision;
- amount units and rule provenance are unambiguous;
- timeout and retry do not claim or create duplicate settlement;
- offline behaviour is explicit and reconciles without fabricating external facts;
- state and history remain consistent across declared failure boundaries;
- conventional baseline receives the same contract and test cases.
