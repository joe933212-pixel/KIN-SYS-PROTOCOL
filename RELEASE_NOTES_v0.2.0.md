# KIN-SYS Protocol v0.2.0 — Release preparation notes

**Author:** Joel Kagoro  
**Copyright:** © 2026 Joel Kagoro  
**License:** Apache-2.0  
**Release target:** Zenodo record supplied by the author: https://zenodo.org/records/23976295  
**Source snapshot:** `main` at `b9385003509541cc5e014ca4a5a271323eaf3872`  
**Status:** Preparation only. No Git tag, GitHub release, or Zenodo publication has been created.

## Release summary

This milestone brings the institutional deployment and cross-installation federation engineering work onto `main`. It documents a provider-neutral, software-first protocol architecture and a growing set of runnable reference implementations. It is not a claim that KIN-SYS is production-ready.

## Included engineering work

- **Institutional deployment domain model:** institution, jurisdiction package/source, MINAH deployment and deployment-binding records; immutable versions, lifecycle events, ownership constraints and operation pinning.
- **MINAH onboarding boundary:** institution-owned agent/deployment registration and lifecycle contracts, with explicit distinction between registration, connectivity, conformance and authority.
- **Institutional identity:** tenant-scoped OIDC provider registration, versioned configuration lifecycle, provider-neutral adapter selection, HTTPS discovery/JWKS transport controls, ID-token validation and subject binding.
- **Delegated authority:** versioned grants, signed actor assertions, scope and validity checks, revocation and revalidation at protected transitions.
- **Trust and federation:** issuer/receiver trust relationships, Ed25519-signed grants and receipts, destination-bound signed instruction envelopes, receiver-local verification and independent receiver decision rights.
- **Durable federation components:** PostgreSQL-backed replay protection, inbox/outbox, leases, retry/dead-letter handling, signed acknowledgements and recovery-oriented integration tests.
- **Tenant isolation:** PostgreSQL RLS migrations for institutional trust, federation delivery and institutional domain records, with transaction-scoped tenant context.
- **Evidence and demonstrators:** runnable local HTTP prototype, paired comparison harnesses, integrated demonstrator report workflows, migration documentation and security/architecture contracts.

## Verification evidence

GitHub Actions ran on the exact merge commit `b9385003509541cc5e014ca4a5a271323eaf3872` on 2026-11-25.

- `local-transition-contract`: success
- `package`: success
- Test suite: **789 tests run; 4 skipped; 0 failures**
- Skips: live OIDC provider tests, because a live OIDC test profile was not configured.

The test evidence is reproducible within the recorded CI environment. It is not independent validation, penetration testing, operational acceptance or proof of production readiness.

## Known limitations and claims boundary

- The 29 proposed production API routes remain draft contracts and are not routed production endpoints.
- The local HTTP prototype remains a synthetic reference vertical slice. MINAH, identity, authority, jurisdictional sources and some institutional trust material remain synthetic, injected or locally configured.
- The full sender and receiver institutional runtimes have not yet been demonstrated on independent hosts and independent database services. The two-database PostgreSQL federation integration uses two databases on one CI PostgreSQL service, with installation adapters in one process.
- The separate-process mTLS test uses a minimal receiver stub to isolate transport and peer-certificate identity; it is not a complete process-separated institutional deployment.
- Live OIDC integration tests were skipped in this CI run.
- CEE and IVM persistence are not one cross-store atomic transaction. Exactly-once execution across the network and CEE/IVM boundaries is not claimed; delivery and recovery remain based on durable state, idempotency and reconciliation.
- PostgreSQL RLS must be deployed with a least-privilege, non-superuser database role without `BYPASSRLS`. Transaction-scoped custom tenant context is not protection against arbitrary SQL executed under the same database principal.
- No real funds move. No production MINAH service, authoritative legal-source service, production key-custody environment, or complete external institutional deployment is claimed.
- Production adoption requires deployment-specific integration, operational hardening, independent security review and institutional acceptance testing.

## Publication checklist

- [x] Source snapshot merged to `main`.
- [x] CI completed successfully on the exact merge commit.
- [x] Repository authorship, copyright and Apache-2.0 licensing are present.
- [x] Zenodo metadata prepared in `.zenodo.json` for version `0.2.0`.
- [ ] Confirm the supplied Zenodo record is the intended prior version and inspect its metadata/files.
- [ ] Create and verify the Git tag `v0.2.0`.
- [ ] Create the GitHub release from that exact tag.
- [ ] Confirm Zenodo has ingested the GitHub release and that the archived files and metadata match this snapshot.
- [ ] Review the resulting version DOI and concept DOI.

**Do not cite this preparation branch as a published release.** The version becomes a citable Zenodo release only after the Zenodo record is published and its DOI is verified.
