# Implementation Status

**Last updated:** 2026-10-21  
**Baseline:** Protocol foundation 0.1-draft  
**Overall status:** Specification foundation in progress; not production-ready.

## Status vocabulary

- **Proposed:** concept under consideration.
- **Specified:** requirement documented, not necessarily implemented.
- **Prototype:** implementation exists but does not establish production guarantees.
- **Tested:** a defined property has a reproducible test artifact.
- **Independently validated:** evidence produced or repeated by an independent reviewer.
- **Production-ready:** deployment-specific acceptance and operational gates have been satisfied.

## Current status

| Area | Status | Next evidence required |
|---|---|---|
| Protocol purpose and scope | Specified in draft | External review and scope decision |
| Trust boundaries | Specified in draft | Threat-model review against target deployments |
| Identity and authority interfaces | Requirements identified | Credential, policy, revocation, and key-lifecycle specification |
| Deterministic transition semantics | Narrow local contract and SQLite prototype committed; not yet cleanly verified | Compare against full IVM/CEE source semantics and Experiment 005 result vector |
| State/history commitment and recovery | Prototype evidence reported in IVM archive | Re-verify artifacts; test concurrency, storage failures, and multi-node cases |
| Offline edge behaviour | Requirements identified | Offline authority model and reconciliation tests |
| Cross-institution trust | Open design | Trust federation, governance, and dispute model |
| Economic accounting / $KIN | Open design | Purpose, accounting rules, abuse model, and legal/economic review |
| MINAH AI boundary | Conceptual boundary documented | Permission model, evaluation, threat tests, and audit policy |
| Nexus Pods | Conceptual deployment form | Hardware profile, power/network assumptions, security, maintenance, and field testing |
| Privacy-preserving proofs | Not established here | Define the claim and threat model before selecting a mechanism |
| Production deployment | Not established | Complete profile-specific security and operational gates |
| Independent validation | Not yet recorded here | Independent review/reproduction report |

## Evidence discipline

A legacy codebase or document describing a feature does not mark it implemented or tested in this repository. Every status change must link to a commit, test artifact, experiment, review, or deployment record.

## Immediate next steps

1. Trace source versions for CRS, CISA, IVM, and CEE.
2. Complete the literature-to-requirement crosswalk using the agreed literature record and prior-art analysis.
3. Freeze the first narrow transition contract and invariants.
4. Build the smallest end-to-end conformance slice.
5. Run tests and publish reproducible artifacts before expanding scope.
