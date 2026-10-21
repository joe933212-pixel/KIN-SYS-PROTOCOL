> **Authorship & rights:** Joel Kagoro · Copyright © 2026 Joel Kagoro · Licensed under Apache-2.0; see `LICENSE`, `NOTICE`, and `COPYRIGHT.md`.

# KIN-SYS Protocol Invariants

These are proposed acceptance conditions, not proven properties. Each implementation profile must map applicable invariants to tests and identify unsupported cases.

| ID | Invariant | Minimum verification |
|---|---|---|
| INV-001 | No protected transition without valid authority for the operation and purpose. | Test valid, absent, expired, revoked, and insufficient authority. |
| INV-002 | Authentication alone does not confer authorisation. | Valid identity with insufficient permission must be rejected. |
| INV-003 | A repeated operation ID cannot create a second effect. | Repeat sequentially and concurrently, including after restart. |
| INV-004 | Reusing an ID with different content is rejected or quarantined. | Submit conflicting payloads under the same ID. |
| INV-005 | State and required history cannot silently diverge. | Inject faults at every persistence boundary and verify recovery. |
| INV-006 | Acknowledgements distinguish received, accepted, committed, final, deferred, rejected, and unknown. | Simulate timeouts before and after commitment. |
| INV-007 | Rule and schema versions used for a decision remain identifiable. | Upgrade rules and inspect historical provenance. |
| INV-008 | Revoked authority is rejected when the declared freshness policy requires current status. | Test online/offline profiles against revocation timing. |
| INV-009 | Offline acceptance is not presented as final when reconciliation is required. | Disconnect, inspect status, reconnect, and reconcile. |
| INV-010 | Cross-domain requests cannot silently change issuer, audience, purpose, or trust context. | Tamper with each field and test federation boundaries. |
| INV-011 | AI output cannot bypass the authorised transition path. | Attempt privileged action through model output and malformed tool results. |
| INV-012 | Sensitive data is not disclosed to unauthorised recipients. | Test access denial, purpose changes, and minimal disclosure. |
| INV-013 | Economic effects are traceable to authorised rules and source evidence. | Test missing provenance, duplicate rewards, inconsistent accounting, and dispute. |
| INV-014 | Governance changes have a verifiable authorisation trail. | Attempt unsigned, under-approved, and replayed updates. |
| INV-015 | A ledger/proof is not treated as independent proof of an unverified physical-world claim. | Test oracle disputes and conflicting attestations. |
| INV-016 | A node unable to meet declared freshness/security requirements fails closed or enters bounded degraded mode. | Simulate unavailable authority, stale keys, bad clock, and partition. |

For each invariant, record owner, threat addressed, implementation location, test identifier, evidence artifact, limitations, and status. Changes must be documented rather than silently rewriting the requirement history.
