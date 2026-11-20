# KIN-SYS Windows Institutional Demonstration

**Status:** Implementation plan; not an operational installer or production deployment.
**Owner:** Copyright © 2026 Joel Kagoro.
**Base:** PR #7 head at implementation start: `6a9af31c42259314d7604b3f13ddf700c5ca0648`.

## Purpose

Produce a Windows x64 demonstration package that can be extracted and launched on an ordinary Windows computer without a separately installed Python runtime. The package must demonstrate an institution installing and configuring its own KIN-SYS instance, then exchanging a governed instruction with a second independently running installation.

This work is isolated on `feat/windows-institutional-demo`. It must not modify `main`, retarget, merge, or otherwise change draft PR #7.

## Product boundary

The existing `scripts/package-portable-windows.ps1` creates a self-contained ZIP with bundled CPython. The existing `sandbox/portable_server.py` starts a loopback-only local sandbox backed by SQLite. Its own source and API readiness response explicitly identify the service as synthetic-only; it permits unauthenticated loopback access and constructs the pipeline without the institutional onboarding, identity-provider, MINAH deployment, runtime-binding, or federated receiver services.

Therefore the existing ZIP is a **portable synthetic test sandbox**, not an institutional installer. This project must not relabel it as a deployed institution or production-ready KIN-SYS.

## Demonstration acceptance journey

1. **Install:** unpack and launch the Windows x64 package; show build provenance, data location, service health and stop/reset controls.
2. **Institution identity:** create an institution-local installation profile. The tenant and institution identity must be established by the installation/onboarding service, not accepted from arbitrary browser form fields as authenticated identity.
3. **Identity provider:** configure the institution's own provider policy. A local demonstration fixture may be used only when clearly labelled; it must not be described as a live OIDC provider.
4. **MINAH:** register the institution's own provider-neutral MINAH descriptor, with owner, mandate, jurisdictions, permitted and prohibited operations, and decision boundaries. The invariant prohibiting authority grants and CEE bypass must remain enforced.
5. **Governance binding:** pin exact institution, jurisdiction package, MINAH, identity-provider, policy, source and authority-profile versions. Do not activate incomplete or inconsistent bindings.
6. **Readiness:** distinguish process health from institutional onboarding completeness and from production readiness. A successful health endpoint alone must never mark an institution as deployed.
7. **Second installation:** launch a second process with a separate port, tenant identity, data directory and cryptographic identity. It must not share the first installation's local database or administrator context.
8. **Federated instruction:** submit an instruction from the originating installation. The receiving installation independently authenticates the sender, checks trust and its own pinned authority/governance context, then returns a traceable acknowledgement or rejection.
9. **Evidence:** persist local operation and audit evidence at both ends; show the MINAH → CEE → Final Decision path without implying that the origin's decision substitutes for the receiver's decision.

## Implementation constraints

- Reuse existing domain services, lifecycle administration, identity-provider registry, MINAH deployment administration, runtime-binding resolver, instruction-execution service and federation transport/receiver where their contracts support the workflow.
- Do not create a second, weaker implementation of authority or tenant isolation just to simplify the UI.
- Do not put credentials, private keys, signing secrets or bearer tokens in the package, browser storage, example configuration or logs.
- Bind all listeners to loopback for the local demonstration. Do not expose the demo to a LAN or public network.
- Keep synthetic identity, authority, MINAH, OIDC and federation fixtures visibly labelled as test fixtures. A test signature or deterministic adapter is not production authentication.
- Keep each institution's database, keys, tenant and audit trail independent.
- Make no claim of production readiness, legal verification, external institutional connectivity, or security certification.

## Required engineering increments

### W1 — Windows package foundation
- Keep the self-contained CPython build and checksum verification.
- Add a Windows preflight/launcher that can start and stop two isolated instances without port or data-directory collisions.
- Add package smoke tests for both processes, health/readiness endpoints, process shutdown and persistence separation.

### W2 — Institutional onboarding service composition
- Compose the existing authenticated onboarding, identity-provider, MINAH and lifecycle services behind a coherent installation API.
- Provide an explicit local-demo trust fixture with a clearly labelled test-only authority source.
- Reject requests that attempt to set tenant or actor identity outside the verified session context.
- Test every lifecycle transition, invalid transition, cross-tenant reference and replay boundary.

### W3 — Operator onboarding workflow
- Replace the dashboard-only flow with an operator workflow that calls the real local API for each onboarding step.
- Show current lifecycle state, version pins, audit evidence and unmet prerequisites.
- Do not provide UI-only success states or client-side fabricated records.

### W4 — Two-installation federation
- Compose two separately launched installation processes and their independent stores.
- Use the repository's federation envelope, receiver, trust registry and transport contracts.
- Test accepted, rejected, replayed, revoked-sender, wrong-tenant and receiver-authority cases over an actual local HTTP transport.
- Clearly mark any remaining synthetic cryptographic/identity fixtures.

### W5 — Release candidate
- Run the full existing suite plus Windows-specific package and end-to-end tests.
- Verify the archive checksum and source commit provenance.
- Review the complete diff and security boundaries.
- Publish a downloadable ZIP only from a successful build of the reviewed branch head.

## Current verified starting point

- Existing Windows package script: `scripts/package-portable-windows.ps1`.
- Existing launcher: `sandbox/run-windows.ps1` and `START-KIN-SYS.bat` generated by the package script.
- Existing local API: `protocol-reference/http_service.py`.
- Existing institutional onboarding API routes and services exist in the reference implementation, but the portable sandbox does not currently wire them into its app.
- Existing cross-installation integration test: `protocol-reference/tests/test_cross_installation_runtime_integration.py`. Its own module documentation identifies synthetic identity, authority and MINAH adapters; it is not live network federation.
- Existing readiness response is `READY_FOR_LOCAL_SYNTHETIC_DEMONSTRATION`, with `production_ready: false`.

## Release rule

No ZIP is to be presented as an institutional deployment until W1–W4 acceptance tests pass. No production-readiness claim is permitted. The branch remains separate from PR #7 and `main` until the user explicitly approves any later integration.
