# Provider-Neutral Execution Architecture and Human Usability

**Status:** Proposed design baseline — not an implementation claim  
**Owner:** KIN-SYS Protocol  
**Applies to:** Protocol specification, runtime, adapters, API, deployment profiles, and user-facing institutional workflows

## 1. Purpose

KIN-SYS must remain a provider-neutral protocol architecture. It must not depend on NVIDIA, any single AI model, GPU vendor, cloud, operating system, blockchain, or deployment form. NVIDIA-based infrastructure may be one supported deployment profile when it offers a useful capability, but it is not the protocol's foundation or a prerequisite.

The protocol must also be usable by people and institutions that are not specialist AI or infrastructure teams. Human usability is a system requirement, not a presentation layer added after the protocol is built.

This document proposes engineering constraints for the next implementation increments. It does not claim that the capabilities below are already implemented.

## 2. Architectural principles

1. **Protocol before provider.** Define the request, authority, policy, execution, decision, evidence, and error contracts independently of a particular vendor.
2. **Adapters at the boundary.** Provider-specific integrations must sit behind documented interfaces. Provider-specific behaviour must not leak into protocol semantics.
3. **One governed execution path.** Every applicable instruction enters through MINAH first, then proceeds through the unified CEE boundary, where IVM executes CISA-defined machine semantics. No model, runtime, plugin, or adapter may create an ungoverned alternative path to a protected institutional side effect.
4. **Human authority remains explicit.** AI output is not identity, legal authority, consent, or approval by itself. Human review and escalation must be available where policy requires them.
5. **Explain decisions in useful language.** A person should be able to understand whether an instruction passed, was rejected, or was deferred, why, what evidence or action is still required, and who can resolve it. Technical traces remain available to authorised engineers and auditors.
6. **Fail safely and recover clearly.** Missing, stale, conflicting, or unavailable evidence must produce a defined outcome. The interface must not imply that a transaction or institutional change succeeded when its outcome is unknown.
7. **Minimise data.** Collect and disclose only the information needed for the authorised purpose. Errors and logs must not expose secrets or unnecessary personal data.
8. **Design for constrained environments.** The protocol must support central, cloud, on-premises, and edge deployments, including carefully defined degraded or disconnected operation. Offline behaviour must not silently extend expired authority or treat stale rules as current.
9. **Make capabilities discoverable.** Deployments must report which interfaces and capabilities are actually enabled, which are simulated, and which are unavailable.
10. **Prove claims through conformance.** A passing demo, diagram, model response, or provider badge is not proof of protocol conformance. Claims require reproducible tests and explicit evidence.

## 3. Institution-owned MINAH deployments

MINAH is a gatekeeper role, not one globally shared service. Each national/sovereign, enterprise, or institutional deployment may use its own implementation, legal/source corpus, policy pack, identity integrations, operational controls, and hosting model. KIN-SYS standardises the gatekeeping position and the provenance required for an operation; it does not require identical MINAH models or configurations.

Each deployment should have a versioned profile identifying the deployment owner and scope, intended use case, jurisdictions, policy/source-pack versions, model or implementation reference, and deployment mode. The operation must pin the profile and version used for its MINAH disposition. A retry must not silently switch profiles. A router, where used, must select a profile from authenticated institutional context and explicit policy; an untrusted request must not be able to choose a more permissive profile.

A deployment may be locally customised, but MINAH must not grant institutional authority by itself. Identity, action-specific authority, institutional decision rules, and CEE enforcement remain distinct controls. The protocol's mandatory sequence and evidence requirements remain consistent across deployment scopes.

The draft profile contract is documented in [MINAH Deployment Profiles](MINAH_DEPLOYMENT_PROFILES.md). The current reference pipeline validates and pins a profile into the operation fingerprint and MINAH evidence trace; the validator does not authenticate profile owners or prove the legal truth of a profile's sources. Production profile routing, trusted ownership, jurisdiction matching, and secure profile updates remain acceptance work.

## 4. Logical architecture

The protocol defines stable logical responsibilities. Implementations may combine or distribute them, provided they preserve the contracts and invariants.

```text
Person / Institution / Machine
             |
             v
Accessible Client or Institutional API
             |
             v
MINAH — instruction intake and preliminary checks
             |
             | integrity-protected, correlated handoff
             v
CEE — unified controlled execution environment
  └── IVM executes CISA-defined machine semantics
             |
             v
Applicable institutional rules, decisions and effects
             |
             v
Final decision + outcome status + evidence references
             |
             v
Human-readable explanation / authorised audit view
```

Provider adapters are supporting infrastructure, not an alternate protocol path:

- **AI model adapter:** connects a selected model or AI service to MINAH or another explicitly authorised role. It must identify model/provider/version where available and must not grant the model execution authority.
- **Runtime/security adapter:** connects to an available agent runtime or isolation/enforcement system. NVIDIA OpenShell/Sentry may be evaluated as one option; other compatible implementations must remain possible.
- **Compute adapter/profile:** describes deployment capabilities and requirements without making a GPU or vendor mandatory.
- **Identity and authority adapters:** connect to authorised registries or institutional identity/permission services. Identity and action-specific authority are separate checks.
- **Institutional action adapter:** invokes the target system only after the governed path authorises the action; it must support idempotency, explicit unknown outcomes, and reconciliation where relevant.
- **Evidence adapter:** records verifiable references and trace data according to retention, privacy, and audit policy.

Adapters must not independently decide to bypass a rejected or deferred protocol decision.

## 5. Provider-neutral contracts

The protocol should specify versioned contracts before adding vendor-specific integrations.

### 4.1 Instruction envelope

At minimum, the governed instruction context should include:

- Stable request and operation identifiers.
- A digest or other integrity binding to the original request.
- Principal and authority references, verification source, result, and time.
- Requested action, target resource, and relevant limits.
- Jurisdiction or institutional domain.
- Pinned rule/legal-source versions and evidence references.
- MINAH disposition and applicable model/policy version.
- Expiry/freshness information and replay/idempotency key where appropriate.

Retain references and minimal necessary data rather than copying sensitive identity records into every trace. The exact schema must be defined in the protocol contracts and reviewed for privacy and security.

### 4.2 Decision envelope

A decision response should distinguish at least:

- `PASSED`: required checks passed and the defined next step is authorised.
- `REJECTED`: a required condition failed or the action is not permitted.
- `DEFERRED`: evidence, authority, policy interpretation, or a dependency is missing, stale, conflicting, or requires authorised review.
- `UNKNOWN`: an external side effect may have occurred, but the system cannot yet confirm its outcome.

The implementation may use more detailed internal states, but must not collapse these meanings. In particular, `PASSED` does not itself mean an external payment settled or a record was changed.

Each response should include a stable reason code, a plain-language explanation safe for the intended audience, references to applicable rules/evidence where disclosure is permitted, the operation ID, and the next safe step. Sensitive internal details should be available only to authorised roles.

### 4.3 Adapter requirements

Each adapter contract must specify:

- Input/output schemas and versions.
- Authentication and authorisation expectations.
- Capability discovery and compatibility.
- Timeouts, retries, idempotency, and cancellation semantics.
- Failure, unknown-result, reconciliation, and recovery behaviour.
- Data minimisation and logging rules.
- Conformance tests and evidence required for each claimed status.

A simulator must identify itself as a simulator in capability responses and operation traces. A simulator must never be presented as an authoritative institution, live identity source, or real payment provider.

## 6. Human usability requirements

The protocol's clients and institutional workflows should:

1. Use clear, plain language by default and offer technical details when needed.
2. Make the current state visible: received, checking, awaiting review, authorised, rejected, executing, completed, or outcome unknown.
3. Explain what the user can do next; avoid opaque errors such as a bare HTTP status or stack trace.
4. Preserve a request/reference ID so support staff can investigate without asking users to repeat sensitive information.
5. Ask for confirmation before consequential actions when required by policy; never use confirmation to replace legal authority or other mandatory checks.
6. Offer accessible layouts, keyboard/screen-reader support, responsive mobile views, and low-bandwidth-friendly interaction where relevant.
7. Support localisation and jurisdiction-specific terminology without changing protocol semantics.
8. Avoid exposing secrets, detailed security controls, or another person's private information in explanations.
9. Distinguish advice from decisions, decisions from execution, and execution from independently confirmed outcomes.
10. Provide a safe route to authorised human review for ambiguity, disputes, and high-impact exceptions.

These requirements apply to any UI built on KIN-SYS; the protocol itself should provide stable reason codes, states, and metadata so different clients can deliver consistent experiences.

## 7. Deployment profiles

The protocol should define and test profiles without binding semantics to a vendor:

- **Development/test:** local execution and explicitly labelled simulators.
- **Conventional server:** CPU-based or mixed compute, with supported operating systems and runtime.
- **Cloud or managed infrastructure:** any compatible provider that meets the deployment's security and governance requirements.
- **On-premises:** institution-controlled compute and services.
- **Accelerated AI:** optional GPU/accelerator profile. NVIDIA is one possible implementation, not the default or exclusive choice.
- **Edge/disconnected:** local operation with explicit limits on which checks can be completed offline, how authority/rule freshness is established, and how queued work is reconciled.

Each profile needs its own capability declaration, security assumptions, resource requirements, acceptance tests, and limitations. A profile is accepted only after its own tests and operational gates pass.

## 8. Conformance and non-bypass tests

The shared test suite should include at least:

- A valid instruction passes through MINAH before CEE admission.
- Unknown identity, expired/revoked authority, wrong scope, or missing legal context prevents downstream side effects.
- A manipulated or misrouted MINAH handoff is rejected.
- An AI model or runtime attempts to call an institutional action adapter directly; the attempt is denied.
- Provider timeout, model unavailability, schema mismatch, stale policy, and missing evidence yield defined safe outcomes.
- Duplicate requests and retries do not duplicate protected effects.
- A payment timeout returns an unknown/reconciliation state rather than falsely reporting success or failure.
- Offline queued work is rechecked against current authority and rules before consequential execution.
- The same protocol scenarios produce equivalent decisions across two different compatible provider/deployment profiles under the same inputs and pinned policies.
- User-facing explanations accurately match the machine-readable decision and do not reveal protected data.

Run the same externally observable scenarios against a well-engineered conventional baseline. Record complexity, failure recovery, integration cost, usability, performance, and security results; do not assume KIN-SYS is superior in advance.

## 9. NVIDIA integration rule

An NVIDIA integration may be added only as an optional adapter/profile after the provider-neutral contracts and baseline conformance tests are established.

The integration should document:

- The exact supported NVIDIA product/software versions.
- Which controls are provided by NVIDIA and which remain KIN-SYS responsibilities.
- How the adapter authenticates and correlates requests.
- How the governed path prevents direct bypass to protected institutional APIs.
- Which security claims have been tested and which remain assumptions.
- How the deployment can replace the model, runtime, or compute provider without changing protocol semantics.

No repository documentation should imply that NVIDIA is required, that KIN-SYS is already integrated with NVIDIA, or that NVIDIA runtime controls automatically establish institutional/legal legitimacy.

## 10. Implementation order

1. Reconcile this proposal with `SPECIFICATION.md`, `ARCHITECTURE.md`, `INVARIANTS.md`, the API contracts, and the CEE integration plan; document conflicts rather than silently overriding them.
2. Define versioned instruction, decision, capability, and adapter contracts.
3. Implement and test the human-readable state/reason mapping using the existing synthetic case coordinator.
4. Add a non-bypass test proving that protected institutional actions cannot be invoked outside the governed execution path.
5. Strengthen durable operation tracking, outbox/dispatch, idempotency, unknown-outcome reconciliation, and crash recovery.
6. Run the same conformance scenarios against at least two distinct provider/deployment profiles.
7. Evaluate NVIDIA as one optional profile only after the shared contracts work.
8. Keep implementation status explicit: proposed, implemented, simulator-backed, sandbox-validated, independently reviewed, or production-accepted.

## 11. Acceptance criterion

This design is not accepted merely because the document exists. The next milestone is reached when versioned contracts and automated tests demonstrate a user-understandable, provider-neutral governed instruction path, including refusal, deferral, failure, and recovery cases. The status must remain a research prototype until deployment-specific security, legal, operational, and integration gates have been independently satisfied.
