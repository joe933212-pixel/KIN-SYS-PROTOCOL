# KIN-SYS Governed Instruction and Decision Contract v0.1

**Status:** Draft implementation contract for review; not a production wire standard.  
**Copyright:** Joel Kagoro © 2026. See LICENSE, NOTICE, and COPYRIGHT.md.

## Purpose

This contract establishes provider-neutral semantic envelopes for an instruction entering KIN-SYS and the decision/outcome returned for it. It is an initial contract slice, not a complete API or security protocol. The Python reference validator checks structure and semantic consistency only. It does not authenticate callers, prove evidence truth, validate legal authority, or supply cryptographic wire security.

## Required sequence

For every protected instruction, the system must enforce this path:

1. **Ingress and correlation:** accept the request into controlled intake; assign or validate stable request and operation IDs, idempotency key, payload digest, purpose, jurisdiction and rule-pack version.
2. **MINAH first:** MINAH performs preliminary jurisprudential, source and evidence checks and returns a versioned disposition. This is advisory and traceable; it is not itself institutional authorisation or permission to commit an effect.
3. **CEE controlled execution:** the instruction and MINAH disposition enter the unified CEE boundary. IVM executes the machine semantics defined by CISA inside CEE. CEE validates the allowed transition, applicable authority and rule versions, and records the outcome.
4. **Institutional decision and effects:** institution-owned decisions and external actions occur only through authorised adapters after required gates pass. A provider adapter cannot override a rejection or deferral.
5. **Final decision and evidence:** return a versioned decision envelope and distinct external-effect status. Preserve evidence references and an ordered trace for authorised review.

This sequence is a target invariant. A schema or trace field does not prove runtime enforcement. A bypass-resistant end-to-end test must demonstrate that protected side effects cannot occur when MINAH is skipped or a required gate rejects or defers.

## Instruction envelope

The v0.1 reference implementation requires the following fields:

| Field | Meaning |
|---|---|
| contract_version | Exact contract identifier; unsupported versions fail closed. |
| request_id, operation_id | Stable correlation and operation identities. |
| idempotency_key | Key used by the operation owner to prevent duplicate effects. |
| principal_ref | Minimal reference to the requesting principal, not a copied identity dossier. |
| authority_ref | Reference to a separately verifiable action-specific authority check. Presence does not mean authority is valid. |
| action, target_ref | Requested operation and target resource. |
| purpose, jurisdiction | Why the request is made and which institutional/legal domain applies. |
| submitted_at | Timestamp including an explicit timezone. |
| payload_digest | Algorithm-labelled SHA-256 digest of the canonical instruction envelope, excluding this field and including all other fields/extensions. The governed HTTP path recomputes and verifies it before execution. |
| rule_pack_version | Exact policy/rule-pack version expected to govern the request. |
| evidence_refs (optional) | References to evidence records, not necessarily raw documents. |
| provider_profile (optional) | Informational deployment profile; it must not alter protocol meaning or be hardcoded to a vendor. |

Unknown versions must not be silently interpreted as current. The reference canonicalisation is UTF-8 JSON with sorted object keys, compact separators, non-ASCII characters preserved, and NaN/Infinity disallowed. A production wire standard still needs schema evolution policy, authenticated transport, replay protection, key management and privacy review.

## Decision envelope

The protocol decision is one of:

- **PASSED:** required protocol gates passed; the defined next step may proceed.
- **REJECTED:** a required condition failed or the action is not permitted.
- **DEFERRED:** an authoritative check, source, review or dependency is missing, stale, conflicting or unavailable.
- **UNKNOWN:** the system cannot safely determine the decision or effect outcome.

External-effect status is separate: NOT_ATTEMPTED, PENDING, CONFIRMED, UNKNOWN, or NOT_APPLICABLE.

**PASSED is not proof that a payment settled or a record changed.** A confirmed effect requires a separate confirmation reference from an appropriately authenticated and trusted source. The reference validator cannot establish source authenticity; provider authentication and reconciliation remain separate requirements.

Every decision carries a stable reason_code, plain-language user_message, rule-pack version, MINAH disposition reference, CEE execution reference and evidence references. Human-facing clients should show what happened, what remains uncertain, the operation reference and the next safe step without exposing restricted detail.

## Provider neutrality

The contract contains no mandatory vendor, model, cloud, GPU, operating system, ledger or hardware field. Provider/profile identifiers may be recorded for observability and conformance evidence, but must not change protocol meaning. NVIDIA-accelerated infrastructure is an optional profile alongside conventional CPU servers, cloud, on-premises and edge/disconnected profiles. The same semantic conformance suite should run across at least two independently implemented profiles before portability is claimed.

## Failure and safety rules

- Missing or unknown versions fail closed.
- Reusing an idempotency key with different request content must be rejected or quarantined.
- Authentication and action-specific authorisation are distinct checks.
- Missing or stale MINAH sources, authority results or mandatory rule packs must not be treated as approval.
- A timeout after an external request may have been sent must produce UNKNOWN/reconciliation, not a blind retry that could duplicate an effect.
- Rejected, deferred or unknown decisions must not claim a confirmed external effect.
- Logs and human-readable errors must minimise personal data and secrets.
- Offline operation must report its limitations and cannot silently extend expired authority or rules.

## Verification status and next gates

The existing `POST /protocol/instructions` route accepts the v0.1 instruction envelope and maps it into the **same** `KinSysProtocolPipeline` used by the earlier internal request shape; it does not create a second execution engine. The complete validated client envelope is retained inside the durable operation fingerprint, so a retry cannot silently change contract fields while reusing an operation ID. The route returns a validated v0.1 decision envelope bound to the client `request_id`, `operation_id`, and `rule_pack_version`, alongside the detailed synthetic pipeline trace.

This is a constrained integration slice, not a general production wire implementation. The current pipeline only accepts the synthetic `ACTIVATE` action and rule pack `KIN-SYS-DEMO-RULES:1.0`. For this reference route, the envelope must include an `evidence` object extension; evidence references alone are not treated as proof or evidence content. The reference validator checks the declared SHA-256 digest's shape but does not recompute it against a canonical payload. The response reports `NOT_ATTEMPTED` for external effects because this route does not move funds or call a real external-effect provider. Default identity and authority adapters remain synthetic unless trusted adapters are injected.

These changes do not prove real identity or authority, legal correctness, production security, distributed finality or external-effect truth. Digest verification proves only that the received envelope matches its declared digest; it does not authenticate the sender or prove that its claims are true.

Next acceptance gates:
1. Extend the internal execution contract beyond the single synthetic action/rule pack, with explicit compatibility and fail-closed handling.
2. Add end-to-end tests proving rejection/deferral prevents every protected side effect and exercise restart/reconciliation behaviour through the HTTP boundary.
3. Complete authenticated identity/authority source contracts, trusted MINAH profile ownership and recovery/reconciliation across CEE and IVM state.
3. Test duplicate and conflicting idempotency keys, restart recovery, timeouts and unknown provider outcomes.
4. Add authenticated identity/authority and institutional-action adapter contracts.
5. Validate machine-readable schemas against the Python validators and run the same suite across provider-neutral deployment profiles.
6. Obtain independent legal, privacy, security, operational and domain review before any production claim.

Until those gates are evidenced, this remains a draft contract and research implementation.
