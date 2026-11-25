# KIN-SYS Protocol v0.2.0 — Release preparation notes

**Author:** Joel Kagoro  
**Copyright:** © 2026 Joel Kagoro  
**License:** Apache-2.0  
**Zenodo concept record:** https://doi.org/10.5281/zenodo.23976294  
**Previous version record:** https://doi.org/10.5281/zenodo.23976295 (v0.1.0)  
**Implementation baseline:** `main` merge commit `b9385003509541cc5e014ca4a5a271323eaf3872`  
**Release source snapshot:** To be pinned to the exact commit created by merging this release-preparation PR; the v0.2.0 tag and archive must identify that same snapshot.  
**Status:** Preparation only. No v0.2.0 tag, GitHub release, or Zenodo publication has been created.

## Release summary

This release candidate records the institutional deployment, identity-provider integration, delegated-authority, durable federation, PostgreSQL persistence and portable-sandbox engineering increments merged into the repository after v0.1.0. It remains a research/reference implementation, not a production-ready institutional network.

## Comparison with v0.1.0

The prior Zenodo metadata identifies the v0.1.0 source archive as `KIN-SYS-PROTOCOL-998662f19e0989d05c325a6b846331db997e4668.zip`, corresponding to source commit `998662f19e0989d05c325a6b846331db997e4668`.

A Git tree comparison of that source commit with implementation baseline `b9385003509541cc5e014ca4a5a271323eaf3872` found 101 entries in the v0.1.0 tree and 228 entries in the later tree (entries include directories). The later tree adds 127 entries: six institutional/federation/OIDC documentation files; 42 protocol-reference implementation files; 50 test files; 14 PostgreSQL migration/documentation entries; eight portable-sandbox entries; three JSON schemas; one workflow; and three other entries. The comparison is a source-tree comparison; the v0.1.0 ZIP itself could not be downloaded for independent binary/archive integrity inspection in this audit session.

## Included engineering work

- **Institutional deployment domain model:** tenant-scoped institution, jurisdiction package/source, MINAH deployment and binding records; immutable versions, lifecycle events, ownership constraints and operation pinning.
- **Institution-owned MINAH onboarding:** registration and lifecycle boundaries for institution-selected agents and deployment bindings. Registration, connectivity, conformance and authority remain distinct states.
- **Institutional identity:** tenant-scoped OIDC provider registration and lifecycle, adapter selection, discovery/JWKS transport, ID-token validation and subject binding.
- **Delegated authority and trust:** versioned grants, signed actor assertions, issuer/receiver trust relationships, signed grant provenance, receiver acceptance and revocation/revalidation controls.
- **Cross-installation federation:** destination-bound signed instruction envelopes, receiver-local verification and decision rights, HTTPS/mTLS transport components, durable PostgreSQL inbox/outbox, replay protection, acknowledgements, retries and recovery paths.
- **Persistence and tenant controls:** PostgreSQL reference adapters for institutional registries, trust, CEE journal, IVM transition state and federation delivery; migrations include row-level security policies and transaction-scoped tenant context.
- **Portable demonstration:** a loopback-only local dashboard and packaging workflow for a synthetic MINAH-first governed instruction path.
- **Evidence:** additional unit, integration, PostgreSQL, transport, federation and portable-sandbox tests, plus architecture, migration, onboarding and security-boundary documentation.

## Documentation and implementation cross-check

The audit inspected the v0.1.0 source-tree identity, the v0.2.0 implementation tree, the new domain/onboarding/federation/OIDC documents, the PostgreSQL migration guide, the protocol-reference README, the portable build guide, the production engineering handoff, the prototype status record and the API implementation matrix.

The new domain model, identity-provider onboarding, MINAH onboarding, federation message contract and OIDC transport documents describe their corresponding reference modules and identify injected or synthetic trust boundaries. The PostgreSQL migration guide describes migrations 001–012 and their tenant-context/RLS assumptions. The portable sandbox documentation correctly limits the USB demonstration to a local synthetic path; it does not claim that the sandbox UI runs the complete federation runtime.

The API implementation matrix contains 29 routes, and all 29 are explicitly marked `NOT_IMPLEMENTED_AS_PRODUCTION_HTTP_ROUTE`. The OpenAPI contract and matrix must not be described as a deployed production API.

A stale-status inconsistency was found in `protocol-reference/README.md`: an earlier federation paragraph still said network delivery, inbox/outbox, acknowledgements and receiver orchestration were outstanding, although later commits implement reference versions of those components. That paragraph and its receiver-integration description have been corrected in this preparation branch. Some historical prototype sections still describe their own narrower SQLite-only experiments; those statements must be read as scoped to those experiments, not as a status summary of the entire repository.

## Verification evidence

GitHub Actions on the exact implementation baseline commit `b9385003509541cc5e014ca4a5a271323eaf3872` completed successfully on 2026-11-25:

- `local-transition-contract`: success
- `package`: success
- Test suite: **789 tests run; 4 skipped; 0 failures**
- The four skips are live OIDC provider tests because a live OIDC test profile was not configured.

Fresh GitHub Actions on the exact release-preparation PR head `7f933111076b7351d3864daca7ef98a41f16ec1f` also completed successfully on 2026-11-25: [run 45565505034](https://github.com/joe933212-pixel/KIN-SYS-PROTOCOL/actions/runs/45565505034). The `Protocol conformance slice` workflow's `local-transition-contract` job passed. Transition contract tests, local HTTP smoke test, paired comparison report, integrated demonstrator report and paired integrated workflow report all completed successfully; report verification and artifact upload also succeeded. All workflow steps completed successfully.

This is CI evidence for the current PR head and implementation baseline, not yet the eventual tagged release commit. The final release commit must still be checked after merge/tagging.

## Known limitations and claims boundary

- The 29 production API routes remain planned contracts and are not routed production endpoints.
- The local HTTP and portable sandbox prototypes use synthetic reference adapters. MINAH, identity, authority, jurisdictional sources and some institutional trust material remain synthetic, injected or locally configured.
- The PostgreSQL two-installation integration uses separate databases on one CI PostgreSQL service while both installation adapters execute in one process. Independent hosts and independent database services have not been demonstrated together.
- The separate-process mTLS test uses a minimal receiver stub to isolate transport and peer-certificate identity; it is not the complete institutional runtime in two independent processes.
- Authenticated installation discovery and cross-installation operation-status tracking remain integration work.
- Live OIDC provider tests were skipped in the recorded CI run; deterministic fixture tests are not live-provider validation.
- CEE and IVM persistence are separate boundaries. A cross-store distributed transaction and exactly-once network execution are not claimed.
- PostgreSQL row-level security must run under least-privilege non-superuser roles without `BYPASSRLS`. Transaction-scoped custom tenant context is defense in depth, not protection against arbitrary SQL under the same database principal.
- Federated receiver trust, signing-key custody, external legal/evidence verification, production identity-provider administration and operational revocation still require institution-specific integration and governance.
- No real funds move. No production MINAH service, authoritative legal-source service, production key-custody environment, or complete external institutional deployment is claimed.
- Production adoption requires deployment-specific integration, operational hardening, independent security review and institutional acceptance testing.

## Zenodo publication procedure

1. Keep the existing concept record `10.5281/zenodo.23976294`; create a **new version** from that record. Do not create a new unrelated record.
2. Preserve v0.1.0 as the prior version record `10.5281/zenodo.23976295`.
3. Merge this reviewed release-preparation PR only after fresh CI on its final head is green and the release diff is approved.
4. Create tag `v0.2.0` on the resulting approved main commit and create the GitHub release from that exact tag.
5. Upload or ingest the archive corresponding to that exact tagged commit into the new Zenodo version. Verify the archived filename, source commit, checksum, metadata, version number and DOI before publication.
6. After publication, verify that the new version DOI resolves and that the concept DOI remains `10.5281/zenodo.23976294`.

## Publication checklist

- [x] Identify the prior Zenodo version and parent concept DOI from the supplied record metadata.
- [x] Compare the v0.1.0 source tree with the v0.2.0 implementation baseline.
- [x] Inspect the new institutional, identity, federation, PostgreSQL and sandbox documentation.
- [x] Identify the 29 production API routes as not yet routed.
- [ ] Independently download and inspect the original v0.1.0 ZIP archive (download unavailable during this audit).
- [x] Complete fresh CI on the current PR #9 head `7f933111076b7351d3864daca7ef98a41f16ec1f`.
- [ ] Complete final documentation and security-boundary review on the final release snapshot.
- [ ] Merge PR #9 after approval.
- [ ] Create and verify tag `v0.2.0` and GitHub release from the exact approved commit.
- [ ] Create a new version under concept DOI `10.5281/zenodo.23976294`.
- [ ] Verify the uploaded archive, checksum, metadata and resulting version DOI.

**Do not cite v0.2.0 as published until the Zenodo version is published and its DOI is verified.**
