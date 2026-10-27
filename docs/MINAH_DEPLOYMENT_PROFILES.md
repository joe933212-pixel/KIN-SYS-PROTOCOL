# MINAH Deployment Profiles and Institutional Ownership

**Status:** Draft protocol design and reference validation contract; not a deployed service or production security guarantee.  
**Copyright:** Joel Kagoro © 2026. See repository licensing and notice files.

## 1. Core rule

MINAH is a protocol gatekeeper, but KIN-SYS does not require one globally shared MINAH instance. Each country, enterprise, or institution may deploy and govern its own MINAH instance or deployment profile, appropriate to its legal domain, mandate, risk, and operating environment.

The protocol standardises MINAH's gatekeeping role and the evidence needed to establish which deployment handled an instruction. The institution may supply an existing agent trained and configured for its mandate; MINAH is the protocol role and governed interface that agent occupies, not a requirement to use one centrally trained model. That agent may coordinate approved identity and action-specific authority APIs as part of MINAH's duties; the protocol must still record and enforce the outcomes as distinct gates. A deployment may continue the path after a valid preliminary review, reject, defer for missing information, or report an unknown outcome; rejection and unknown must stop progression to later gates. It does not standardise one universal model, prompt, legal corpus, vendor, hosting provider, or hardware platform.

## 2. Deployment is scoped to an owner and use case

Each deployment profile identifies:
- a stable deployment ID and profile version;
- the owner scope: national/sovereign, enterprise, or institution;
- the institution/owner reference responsible for the deployment;
- the intended use case;
- the jurisdictions within which it is configured to operate;
- pinned policy-pack and source-pack versions;
- a model or MINAH implementation reference;
- the deployment mode (self-hosted, on-premises, cloud, edge, or managed); and
- the invariant that MINAH does not grant institutional authority by itself.

A country-level profile might use national legal sources and national identity/authority integrations. An enterprise profile might use the enterprise's policies, delegated authority model, compliance sources and internal systems. A smaller institution may use its own narrower profile. These are examples, not fixed legal categories; each deployment's owner remains responsible for determining what its mandate permits.

## 3. Bind each governed operation to its MINAH deployment

The operation record and evidence trace should pin the exact MINAH deployment ID, profile version, policy/source-pack versions, model/implementation reference where available, and disposition reference used for that operation. Retries must not silently switch to a different profile or policy version. If the configured deployment is unavailable, the protocol should return a defined deferred/unknown outcome rather than silently route to an unrelated MINAH.

Where a deployment router is used, it must select a profile based on authenticated institutional context and an explicit routing policy—not on model preference or an untrusted instruction field. Routing must be auditable. A caller must not be able to select a more permissive jurisdiction or profile merely by editing the request.

## 4. What must remain invariant

Regardless of ownership or deployment technology:
- MINAH receives the instruction first at the protocol's governed ingress, before CEE admission or execution context is established.
- MINAH's disposition is an input to the governed path, not a substitute for identity, action-specific authority, institutional decisions, or CEE enforcement.
- A MINAH profile cannot grant itself or the caller legal/institutional authority.
- Rejected, deferred, invalid, unavailable, or uncertain dispositions must not be treated as approval.
- CEE remains the unified controlled execution environment; IVM executes CISA-defined machine semantics inside CEE.
- No model, provider, adapter, or deployment administrator may create an alternate ungoverned path to protected effects.
- The deployment must expose enough provenance for authorised audit without copying unnecessary sensitive source material into logs.

The protocol may define minimum conformance requirements, but it must not dictate that every deployment use the same AI model or identical legal knowledge. The institution's policy choices remain institution-scoped; the protocol's execution and evidence requirements remain consistent. Cross-institutional and cross-border operations should be coordinated through explicit participant scopes, accepted evidence, and agreed protocol outcomes—not by treating one participant's agent as the authority for all participants.

## 5. Profile approval and revocation reference contract

A profile's `owner_ref` is descriptive metadata, not proof of ownership or approval. Before trusting a profile for a protected deployment, a deployment trust service must verify an approval bound to the exact canonical profile and confirm the approval is current and not revoked.

The reference seam is `protocol-reference/minah_profile_governance.py`. It computes a deterministic SHA-256 digest of canonical profile JSON and validates an approval envelope, profile digest, timezone-aware approval/expiry times, signature-verifier result, and revocation-check result. Missing, malformed, expired, mismatched, revoked, unknown, or unavailable outcomes fail closed.

This is a validation contract, not a cryptographic implementation. Tests use test doubles; they do not prove real signature verification, signer identity, key custody, or a trustworthy revocation registry. The execution pipeline now exposes an explicit governed mode (`require_profile_approval=True`) that verifies the envelope before MINAH is invoked, fails closed if the envelope is missing/invalid/expired/revoked or either trust callback fails, and pins the approval envelope plus verification metadata into the durable operation fingerprint. `create_app(...)` exposes the same dependency-injection seam. The default synthetic demonstration mode does not require approval and must not be mistaken for a governed production deployment. Production use still requires a reviewed cryptographic implementation, trusted key store, authenticated profile-owner/mandate binding, and persistent authoritative revocation state.

## 6. Failure, updates and independence

A profile update is versioned. New operations may use the newly approved profile version; in-flight operations retain the version pinned at admission unless a defined, auditable re-evaluation procedure requires otherwise. Emergency revocation or a compromised profile needs an explicit fail-closed procedure and reconciliation rules.

Separate institutions should not be able to inspect one another's prompts, source packs, cases, or sensitive evidence merely because they use the same protocol. Shared infrastructure, if chosen, must enforce tenant and jurisdiction isolation. Dedicated deployments are allowed but not mandated by the protocol.

## 6. Reference validator

`protocol-reference/minah_deployment_profile.py` validates the shape of a draft profile and enforces the non-authorisation invariant. Its tests show that national and enterprise profiles can differ while satisfying the same structural contract.

This validator does not deploy an AI agent, authenticate an owner, verify the truth or legal validity of sources, or establish that the institution has legitimately delegated the claimed mandate. Those are separate implementation and assurance tasks. The runtime now checks that the request's jurisdiction exactly matches one of the configured profile jurisdictions before invoking the agent; a mismatch is rejected and does not reach identity verification or IVM. This is a scope check, not proof that the profile owner or jurisdiction claim is trustworthy.

## 7. Current implementation boundary

The HTTP pipeline now accepts a validated MINAH deployment profile through `create_app(minah_profile=...)`. The profile is bound into the durable operation fingerprint before MINAH is invoked, recorded with the MINAH disposition, and included in the MINAH-reviewed event. A retry using the same operation ID with a different profile conflicts rather than silently switching deployments. The default profile is explicitly synthetic and local, scoped to `UG-DEMO`. Jurisdiction mismatch is now rejected before the configured agent is invoked. Trusted profile-owner authentication, mandate/delegation verification, a production cryptographic verifier and key store, authoritative persistent revocation, authenticated multi-institution routing, and cross-border policy negotiation are still unimplemented. The profile approval check is available only when explicitly enabled and supplied with trusted verifier callbacks; default local synthetic mode remains ungoverned. The pipeline now supports an institution-owned MINAH adapter implementing `review`, `verify_principal`, and `verify_action`; this is an adapter seam, not a live national identity or authority API integration.
