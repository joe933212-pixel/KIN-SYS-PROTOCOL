# Institutional Deployment Domain Model

**Status:** Engineering baseline, draft v0.1. Domain contract only; not production security or legal assurance.  
**Copyright:** Joel Kagoro © 2026. See repository licensing and notice files.

## Purpose

Define the ownership and version relationships required before implementing institutional onboarding APIs and dashboard workflows. This model extends, rather than replaces, the existing MINAH deployment-profile and approval contracts.

## Domain aggregate

An institutional deployment consists of four independently versioned records:

1. **Institution:** stable institutional identity, claimed jurisdiction, verification state and verification-evidence reference.
2. **Jurisdiction package:** institution-scoped package version containing source records, provenance, effective dates, status and approval evidence.
3. **MINAH deployment:** institution-owned adapter registration, model/provider reference, interface version and deployment lifecycle.
4. **Deployment binding:** association of one institution, one exact jurisdiction package version, one MINAH deployment, and pinned policy/source/authority profile versions.

The binding is the unit eligible for activation. A model, source pack, policy pack, jurisdiction package or authority-profile update creates a new version or binding; it must not silently mutate the context already pinned to an admitted operation.

## Ownership and isolation invariants

- Every institution-owned record carries an immutable owner institution reference.
- A MINAH deployment and its binding must belong to the same institution as the institution record.
- A binding identifies an exact jurisdiction package ID and version, never a floating latest alias.
- Cross-tenant references are rejected before activation. Shared infrastructure must enforce tenant isolation in storage, authorization, routing, logs, caches and evidence access; identifiers alone do not provide isolation.
- Institutional identity, legal authority, AI output and administrator permissions are separate claims. An owner reference is not proof of ownership or authority.
- KIN-SYS standardises the protocol interface and execution constraints; it does not own the institution's model, prompts, source corpus or mandate.

## Jurisdiction source lifecycle

A source record includes a stable source reference, source type, title, publisher reference, content digest, provenance reference, effective-from date, status and, when approved, approval evidence. Effective-to dates, amendment/supersession links, retrieval location, language, issuing authority and signature/verification evidence should also be retained where applicable.

The lifecycle is explicit: SUBMITTED → VALIDATED → APPROVED. A source may also be REJECTED or SUPERSEDED. Uploading or hashing a document proves neither authenticity nor legal force. Approval must be performed by a verified actor with authority for the source and jurisdiction, under a separately enforced approval policy. A package may be approved only when its sources and package version have the required approvals and effective-date checks.

## MINAH adapter registration

Initial integration modes:

- NATIVE: institution-operated agent behind the standard adapter contract.
- EXTERNAL_SERVICE: authenticated service endpoint controlled by the institution or selected provider.
- MANAGED_RUNTIME: institution's agent hosted in an approved isolated runtime.

The domain model stores adapter and model references, not API keys, passwords, private keys or model binaries. Credentials belong in a secret manager. Model binaries and runtime artifacts require separate supply-chain, isolation and attestation design. Registration does not imply connectivity, conformance or authority.

The existing reference seam exposes review, verify_principal and verify_action. The serialised wire contract, authentication, timeout semantics, model/version provenance, uncertainty handling and evaluation corpus must be frozen before external integrations are accepted.

## Lifecycle and activation

Institution: PENDING_VERIFICATION → VERIFIED, with SUSPENDED and REVOKED controlled states.

Jurisdiction: DRAFT → SUBMITTED → VALIDATED → APPROVED → ACTIVE; it may be RETIRED.

MINAH: REGISTERED → CONNECTIVITY_VERIFIED → CONFORMANCE_PASSED → ACTIVE; it may be SUSPENDED or REVOKED.

Binding: DRAFT → VALIDATED → APPROVED → ACTIVE; it may be SUSPENDED or REVOKED.

Activation eligibility requires a verified institution; approved jurisdiction package and source records; MINAH conformance evidence; approved binding; exact owner and version matches; and authorised activation approval. The reference validator checks structural and cross-record consistency only. It does not authenticate evidence, validate legal authority, verify signatures, persist state or provide production tenant isolation.

## Operation pinning

Every governed operation must pin at least:

- institution ID and authenticated tenant context;
- binding ID and binding version;
- MINAH deployment ID, profile version and model/provider reference;
- jurisdiction package ID and exact version;
- policy pack, source pack and authority-profile versions;
- approval/evidence references and relevant freshness/expiry data.

Retries and recovery preserve this pinned context. A newly activated binding applies to new operations only unless an explicit, authorised re-evaluation procedure says otherwise. Emergency suspension/revocation prevents new admissions and triggers defined handling for in-flight operations.

## Cross-border coordination

The two-institution demonstrator models each participant as an independent owner and decision domain. Each institution's MINAH evaluates only its own mandate and jurisdictional responsibilities. One participant's approval is not a proxy for the other's. CEE coordinates the instruction through the governed path; IVM enforcement remains inside CEE. A cross-border case identifies which rules apply to which participant, the evidence each participant accepts, and the agreed coordination outcome.

## Implementation sequence

1. Implement and test domain validators and activation eligibility.
2. Add persistence with immutable version records and tenant-scoped queries.
3. Add authenticated institution registration and authority verification.
4. Add jurisdiction source ingestion, provenance and approval workflow.
5. Add MINAH adapter registration, secret references, connectivity and conformance checks.
6. Add binding approval and activation.
7. Expose tenant-scoped APIs and onboarding dashboard.
8. Demonstrate two independent institutions and cross-border coordination.

## Acceptance criteria for this increment

- Malformed records and unknown lifecycle states fail closed.
- An uploaded source cannot be marked approved without approval evidence.
- An approved jurisdiction cannot contain unapproved sources.
- MINAH records cannot contain credentials or model binaries.
- A binding cannot point to another tenant's MINAH or jurisdiction package.
- A binding pins an exact jurisdiction package version.
- Activation eligibility fails unless every required lifecycle gate and evidence reference is present.
- Existing MINAH profile approval and operation-pinning behaviour remains intact.

## Explicit non-claims

This increment is not an onboarding UI, live identity verification, production registry, legal-source authenticity service, model upload/runtime service, cryptographic approval implementation or production tenant isolation. Those require separate implementations and tests.
