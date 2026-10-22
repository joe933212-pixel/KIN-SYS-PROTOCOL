# KIN-SYS Deployment Profiles

**Status:** Design targets, draft 0.1 — not certified deployment products  
**Decision:** KIN-SYS is software-first and hardware-independent. Hardware/software co-design is optional and driven by a deployment's declared threat model and operating constraints.

## The rule that applies to every profile

No institution must buy a Nexus Pod or proprietary computer merely to participate in KIN-SYS. Every conforming deployment must implement the same core protocol obligations for identity and authority, versioned rules, validated transitions, commitment or explicit deferral, replay protection, provenance and freshness, failure/recovery, privacy, and auditability. Different profiles may provide different availability and security assurances; those differences must be declared, not hidden.

A profile is not proof of conformance. Each implementation must pass its profile-specific tests and disclose unsupported requirements.

## Profile A — Standard software deployment

Runs on suitable existing servers, virtual machines, or other supported compute. It is the default target for institutions and enterprises with conventional infrastructure. HSMs, trusted execution environments (TEEs), and zero-knowledge proofs are optional capabilities, not entry requirements.

Acceptance evidence should cover dependency failure, restart and persistence, adapter contracts, key custody/rotation, and all protocol invariants. A standard profile must not silently treat unavailable identity, authority, or rule sources as verified.

## Profile B — Constrained connectivity and power

Targets locations where connectivity is intermittent and power cannot be guaranteed. It does not mandate any specific device or Pod. It requires explicit operation classes: safe under bounded offline authority, deferred until reconnection, or prohibited offline. Local acceptance is not remote finality.

Acceptance evidence should include power-loss/restart tests on the selected storage, stale or revoked identity and authority, rule-version drift, replay-safe reconnection, duplicate suppression, and conflict reconciliation. Where safe execution depends on fresh external authority, the action must defer or reject when that authority cannot be verified.

## Profile C — Optional hardware-enhanced security

Adds selected controls such as an HSM for supported key operations, a TEE for a specifically stated isolation goal, secure boot, or specialised compute for proof generation. It may be combined with Profile A or B; it is not a separate mandatory tier of the protocol.

The deployment must state which threats the hardware is intended to reduce, the exact trust boundary and vendor/platform assumptions, key lifecycle and replacement procedures, failure behaviour, and independent test evidence. An HSM does not automatically generate arbitrary zero-knowledge proofs or make the host application trustworthy. Proof generation and proof verification are separate workloads and must be assessed separately.

## Zero-knowledge and storage

Zero-knowledge proofs may allow a verifier to check a precisely defined claim without receiving all underlying private inputs. They do not establish that the source data is truthful or that the encoded rule is legally correct. A deployment must specify the statement, public inputs, proof-system/circuit and rule versions, source provenance, replay/freshness controls, verifier behaviour, and dispute path.

Proofs may reduce data disclosure, transmission, or central retention for a particular workflow. They do not eliminate the need for all records. Commitments, nullifiers or other replay state, version metadata, revocation data, audit evidence, and legally required records may still be needed. Measure storage, transmitted bytes, generation/verification time, energy and operational complexity against a fair conventional baseline before claiming net savings.

## Conformance and evidence

The machine-readable design targets live in protocol-reference/deployment_profiles.py; tests validate profile structure and hardware independence only. They do not certify runtime behaviour, security, or production readiness.

A deployment profile must not weaken the shared core contract. Where a capability is unavailable, the protocol must follow the declared reject, defer, or recovery path rather than silently lowering authority, freshness, integrity, or accountability requirements.

## Separate workstreams

- **Protocol:** profile-neutral contracts, permitted transitions, failure semantics and conformance.
- **Software implementation:** adapters, persistence, identity connectors, orchestration, tests and operations.
- **Optional hardware engineering:** Pod design, power budget, physical security, HSM selection, proof compute, thermal constraints and field evaluation.

Nexus Pods remain one possible infrastructure design for constrained jurisdictions. They are not the protocol, a mandatory node type, or a deployment constraint.
