> **Authorship & rights:** Joel Kagoro · Copyright © 2026 Joel Kagoro · Licensed under Apache-2.0; see `LICENSE`, `NOTICE`, and `COPYRIGHT.md`.

# KIN-SYS Protocol Architecture

**Status:** Conceptual architecture draft 0.1. Components below are logical boundaries, not a claim that services or hardware have been implemented.

## 1. Architectural model

KIN-SYS is a coordinated protocol architecture and ecosystem spanning independently operated nodes, institutions, economic actors, and physical infrastructure. It is not synonymous with a frontend, backend service, blockchain, AI model, execution runtime, or edge appliance.

**System-level research rule:** KIN-SYS must be evaluated as an integrated whole. CEE and IVM address the execution environment and execution semantics that can enable the protocol to run; they do not, by themselves, constitute the complete KIN-SYS system. MINAH AI, $KIN, Nexus Pods, identity and authority, governance, privacy mechanisms, institutional actors, and external interfaces are complementary parts whose interactions must be specified and tested together. Component-level experiments remain necessary, but they are not sufficient to establish system-level feasibility or value.

This framing follows the original coordination problem raised in the FITSPA discussion about an African Single Digital Market: government, private sector, and development partners may each have distinct mandates, yet a functioning cross-sector market also needs a way to coordinate actions, authority, rules, resources, evidence, and remedies across those mandates. KIN-SYS is being investigated as a possible system-level response to that coordination problem—not as a claim that the market problem is already solved.

The logical architecture separates:

1. **Actors and institutional authority** — people, organisations, delegated roles, mandates, policies, and revocation.
2. **Identity and key management** — principal/node identity, credential binding, expiry, rotation, and compromise.
3. **Protocol ingress and message validation** — versioning, authentication, schema checks, audience checks, replay protection, and purpose validation.
4. **Policy and transition execution** — deterministic evaluation of authority, applicable rules, preconditions, and proposed state changes.
5. **Commitment and verifiable history** — state persistence, transaction recovery, history linkage, deduplication, and audit proofs.
6. **Inter-domain and edge coordination** — bounded local operation, synchronisation, reconciliation, conflict resolution, and operational health.

Privacy minimisation, observability, governance, incident response, and conformance testing are cross-cutting controls.

## 2. Trust boundaries

- **Device:** an edge node can be stolen, tampered with, offline, or outdated. Physical deployment or a hardware security module reduces selected risks but does not make the whole node trustworthy.
- **Local operator:** the operator may control power, connectivity, maintenance, data access, and software rollout. These powers must be explicit and audited.
- **Institutional authority:** a valid signature does not prove lawful authority for every action. Authority must be checked against policy and current status.
- **Network:** messages may be delayed, duplicated, reordered, intercepted, replayed, or dropped. Transport security alone does not prevent semantic duplication.
- **External data:** a ledger or proof can preserve an assertion without proving the real-world assertion is true. External facts require accountable sources, validation, dispute paths, and, where appropriate, corroboration.
- **AI/service:** models and APIs may be unavailable, changed, manipulated, or wrong. Outputs are untrusted inputs until validated.
- **Governance:** maintainers, administrators, key custodians, and emergency operators can become concentrated points of power. Privileges require limits and review.

## 3. Logical flow

1. A principal or node creates a uniquely identified request.
2. The receiver validates version, schema, integrity, audience, expiry, and replay status.
3. The authority layer checks current permissions for the operation and purpose.
4. The policy evaluator checks preconditions and applicable rule versions.
5. The transition engine derives a proposed state change deterministically.
6. The commitment subsystem records the transition and corresponding history under a specified recovery model.
7. The result distinguishes received, accepted, committed, final, deferred, rejected, and unknown.
8. Cross-domain or offline work follows declared reconciliation and conflict rules.

No stage should report success merely because a request was received or a mock function returned a positive value.

## 4. Edge systems and Nexus Pods

Nexus Pods are a conceptual physical deployment form for edge compute, connectivity, power, storage, and potentially protected key material. This repository does not assume a final bill of materials or claim a deployed Pod exists.

Each profile must specify physical/cyber threat assumptions, boot integrity and software updates, key custody/recovery, data retention/deletion, power/network-loss behaviour, offline operation classes, telemetry, maintenance, decommissioning, and evidence of the running software profile.

## 5. MINAH: jurisprudential oracle and sovereignty gatekeeper

MINAH is intended to be KIN-SYS's jurisprudential oracle and sovereignty gatekeeper, configured and trained for the jurisdiction or governing domain in which a deployment operates (for example, a country, company, or enterprise). Her remit is to reason over the applicable legal and institutional order, identify the rules and evidence relevant to a proposed action, and return a traceable jurisprudential disposition.

MINAH is not a root administrator and cannot unilaterally alter the rules, grant herself authority, or commit a protected state transition. This is not a limitation that depends on MINAH voluntarily complying: it is an invariant the execution architecture must enforce. IVM and CEE research are central to this separation, providing foundations for constrained transitions, lifecycle handling, commitments, and recovery. KIN-SYS must prevent any one component—including MINAH, an operator, or an adapter—from bypassing mandatory protocol checks.

Jurisdiction-specific MINAH deployments require controlled legal-source ingestion, jurisdiction and effective-date tracking, provenance, conflict/uncertainty handling, evaluation against approved cases, and a governed process for updating or replacing legal knowledge. A model's assertion that it knows the law is not proof of completeness or correctness. When a question requires discretion reserved to a court, official, or institution, or when authoritative sources conflict or are incomplete, the disposition must defer to the designated process rather than fabricate certainty. The protocol must distinguish MINAH's jurisprudential determination from deterministic enforcement and from decisions legally reserved to independent institutions.

## 6. Economic and token boundary

$KIN is a proposed economic component whose function and rules remain unresolved in this foundation. Before implementation, define any relationship to fees, incentives, governance, access, or settlement, along with issuance, custody, accounting, duplicate protection, disputes, and failure behaviour. A token does not by itself establish legal title.

## 7. Integrated system roles and research interfaces

These roles describe the intended division of responsibility, not proof that each component is implemented or that the interfaces are settled.

- **KIN-SYS protocol architecture:** coordinates the actors, rules, authority, messages, transitions, evidence, governance and inter-domain interactions that give the wider system its behaviour.
- **CEE and IVM:** execution-enabling research and runtime layers. CEE defines an operational execution environment and lifecycle; IVM defines an abstract execution architecture. They must be mapped to the protocol contract, but neither should be treated as the whole KIN-SYS system.
- **MINAH:** intended jurisprudential oracle and sovereignty gatekeeper, trained/configured for the relevant jurisdiction or governing domain. Her legal dispositions must be traceable to authoritative, versioned sources and evidence. IVM/CEE-informed execution and KIN-SYS safeguards must prevent any component, including MINAH, from bypassing mandatory constraints. Legal matters reserved to independent authorities must be deferred appropriately.
- **$KIN:** proposed economic/accounting component. Its role—if any—in fees, incentives, access, governance or settlement must be evaluated together with institutional rules, accounting, duplicate-effect protection, legal context and dispute handling. Its utility or necessity is not assumed.
- **Nexus Pods:** proposed physical edge deployment form. Research must connect hardware, connectivity, local execution, key custody, data handling, offline limits, synchronisation and operational governance to the protocol's actual requirements. The existence of the concept is not evidence of deployed infrastructure.
- **Identity, authority, governance, privacy, external facts and institutional participation:** cross-cutting capabilities and accountable actors. These determine who can act, what can be disclosed, which rules apply, how real-world claims are established, and how decisions are challenged or reversed.
- **CRS / CISA / CEE / IVM:** distinct research artifacts whose source versions, requirements and interfaces must be traced. Do not collapse their names or treat them as interchangeable.
- **Kagoro DSL:** candidate formal language for expressing jurisdiction-specific legal and institutional rules, authority constraints, exceptions, and permitted transitions. Whether it becomes required in KIN-SYS remains an open design decision to be resolved by semantics, conformance tests, and comparison with alternatives.

## 8. System-level research method
The binding implementation direction is now documented in [SYSTEM_INTEGRATION_ROADMAP.md](SYSTEM_INTEGRATION_ROADMAP.md) and [Decision 0002](DECISIONS/0002-whole-system-first-development.md): reconcile the whole-system contract, build one contract-driven executable model with explicit simulations for unfinished capabilities, then deepen components under cross-component invariants. Existing component tests remain necessary but cannot stand in for integrated system evidence.


Research must preserve both component-level falsifiability and system-level integration. For each important use case, identify the participating institutions and actors; the role of the protocol; the required CEE/IVM execution behaviour; whether and how MINAH AI participates; whether $KIN has a necessary, justified economic role; what the Nexus Pod/edge deployment must provide; the identity, authority, governance and privacy controls; and the observable evidence of success or failure.

Evaluate at least three questions separately: (1) does each component work within its declared scope; (2) do the interfaces and failure semantics work when components interact; and (3) does the integrated system solve a real coordination problem better than simpler institutional or technical alternatives? A component's success must not be presented as proof of the whole system. A component may also prove unnecessary for a particular use case; integrated research must be allowed to discover that.

## 9. Deployment profiles

Define separate profiles for a single-operator laboratory, institution-operated node, intermittently connected edge node, multi-institution coordination, and high-assurance/regulated deployment. Each profile must state assumptions, supported guarantees, and conformance tests.
