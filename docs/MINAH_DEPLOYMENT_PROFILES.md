# MINAH Deployment Profiles and Institutional Ownership

**Status:** Draft protocol design and reference validation contract; not a deployed service or production security guarantee.  
**Copyright:** Joel Kagoro © 2026. See repository licensing and notice files.

## 1. Core rule

MINAH is a protocol gatekeeper, but KIN-SYS does not require one globally shared MINAH instance. Each country, enterprise, or institution may deploy and govern its own MINAH instance or deployment profile, appropriate to its legal domain, mandate, risk, and operating environment.

The protocol standardises MINAH's gatekeeping role and the evidence needed to establish which deployment handled an instruction. A deployment may continue the path after a valid preliminary review, reject, defer for missing information, or report an unknown outcome; rejection and unknown must stop progression to later gates. It does not standardise one universal model, prompt, legal corpus, vendor, hosting provider, or hardware platform.

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

The protocol may define minimum conformance requirements, but it must not dictate that every deployment use the same AI model or identical legal knowledge. The institution's policy choices remain institution-scoped; the protocol's execution and evidence requirements remain consistent.

## 5. Failure, updates and independence

A profile update is versioned. New operations may use the newly approved profile version; in-flight operations retain the version pinned at admission unless a defined, auditable re-evaluation procedure requires otherwise. Emergency revocation or a compromised profile needs an explicit fail-closed procedure and reconciliation rules.

Separate institutions should not be able to inspect one another's prompts, source packs, cases, or sensitive evidence merely because they use the same protocol. Shared infrastructure, if chosen, must enforce tenant and jurisdiction isolation. Dedicated deployments are allowed but not mandated by the protocol.

## 6. Reference validator

`protocol-reference/minah_deployment_profile.py` validates the shape of a draft profile and enforces the non-authorisation invariant. Its tests show that national and enterprise profiles can differ while satisfying the same structural contract.

This validator does not deploy MINAH, authenticate an owner, verify the truth or legal validity of sources, prove jurisdictional correctness, or pin a profile into a live operation. Those are separate implementation and assurance tasks.

## 7. Current implementation boundary

The HTTP pipeline now accepts a validated MINAH deployment profile through `create_app(minah_profile=...)`. The profile is bound into the durable operation fingerprint before MINAH is invoked, recorded with the MINAH disposition, and included in the MINAH-reviewed event. A retry using the same operation ID with a different profile conflicts rather than silently switching deployments. The default profile is explicitly synthetic and local. This does not authenticate profile owners, securely route requests across multiple institutions, or yet enforce that the request jurisdiction is included in the profile's jurisdiction list. Those remain acceptance tasks.
