> **Authorship & rights:** Joel Kagoro · Copyright © 2026 Joel Kagoro · Licensed under Apache-2.0; see `LICENSE`, `NOTICE`, and `COPYRIGHT.md`.
>
> **Status:** Normative architectural contract, draft 0.1. This document defines intended responsibilities and acceptance criteria; it does not claim that the complete MINAH implementation exists.

# MINAH — Jurisprudential Oracle and Sovereignty Gatekeeper

## 1. Mandate

MINAH is KIN-SYS's **jurisprudential oracle and gatekeeper of national sovereignty**. She is not merely a general-purpose assistant, chatbot, or optional commentary service. Her system responsibility is to evaluate a case against the law and governing rules applicable to the declared jurisdiction or governing domain, then return a reasoned, traceable jurisprudential disposition that the protocol can use within its mandatory enforcement boundaries.

A deployment may represent a country, company, enterprise, or another explicitly declared governing domain. MINAH must be configured and evaluated for each such domain; legal assumptions from one jurisdiction must never silently transfer to another.

MINAH's conclusions can have consequential downstream effects, but MINAH is not permitted to manufacture authority, replace a legally empowered decision-maker, or bypass KIN-SYS safeguards. **The jurisprudential disposition is an input to controlled execution, not a privileged escape hatch.**

## 2. Jurisdiction-specific knowledge and sources

MINAH must operate against an explicit, versioned jurisdiction profile. The profile should identify, as applicable:

- jurisdiction or governing domain, scope, and applicable institutional mandates;
- authoritative legal and policy sources, source identifiers, provenance, and source hierarchy;
- publication, effective, expiry, amendment, and supersession dates;
- applicable rules, exceptions, exemptions, conflicts-of-law or policy rules, and escalation routes;
- retrieval/index version, model or reasoning-engine version, and the evaluation set used to qualify the deployment.

“Trained jurisdiction by jurisdiction” describes a core design requirement: MINAH's reasoning and evaluation must be specific to the governing domain. Model training alone does not make a legal source authoritative or current. Where feasible, decisions must be grounded in retrievable authoritative source material with citations and version references. The system must not silently treat an unverified model recollection as current law.

## 3. External connectivity and data boundary

MINAH connects to external registries, legal databases, institutional systems, and other data sources **only through governed APIs or declared adapters**. Each integration must state its purpose, permitted operations, identity/authority requirements, data scope, provenance, freshness, timeout behaviour, and failure policy.

API connectivity does not itself establish that returned data is true, authoritative, current, or legally sufficient. The system must preserve source identity and retrieval context and distinguish source assertions from validated evidence. MINAH must not receive unrestricted database credentials or bypass the deployment's access-control, privacy, or audit boundary.

## 4. Inputs and output contract

A jurisprudential evaluation should receive a structured case context containing, as relevant:

- case/operation identifier and declared purpose;
- jurisdiction profile and governing-domain version;
- requesting principal, role, authority claims, and relevant institutional mandate;
- evidence references, provenance, freshness, and challenge status;
- applicable law/policy source versions and relevant prior state;
- the specific question or proposed action to be evaluated.

MINAH must return a structured, versioned disposition. At minimum it should carry:

- case and operation correlation identifiers;
- jurisdiction/profile and rule/source versions considered;
- outcome category: permitted under stated conditions, prohibited, insufficient evidence, conflicting authority/sources, uncertain, or deferred to an authorised decision-maker;
- reasoned explanation and traceable citations/source references;
- assumptions, material evidence gaps, uncertainty and conflicts;
- model/reasoning adapter version, evaluation metadata, and any required human/institutional review;
- explicit conditions, limitations, or expiry/freshness requirements.

An output that is malformed, misrouted, uncorrelated, stale, missing required citations, based on an unknown version, or inconsistent with the contract must not be interpreted as permission. The applicable protected operation must reject or defer according to its declared failure policy.

## 5. Authority boundaries and enforcement

MINAH must not:

- define or rewrite the rules governing her own authority;
- grant herself, a caller, or another component authority that the protocol has not independently established;
- bypass identity, authorisation, revocation, purpose, rule-version, evidence, or transition checks;
- directly commit protected state or represent a requested action as executed;
- impersonate a court, regulator, public officer, institution, or other authority whose decision is legally reserved to that entity;
- conceal unresolved uncertainty, material source conflicts, or missing evidence by presenting a definitive answer.

IVM/CEE-informed execution and KIN-SYS protocol controls must independently enforce mandatory constraints even if MINAH is incorrect, manipulated, compromised, unavailable, or returns a malicious disposition. The system must distinguish MINAH's jurisprudential assessment from an independent institutional decision and from the protocol's deterministic acceptance or rejection of a state transition.

Where law reserves discretion or final determination to an independent institution or official, MINAH must route or defer the case to that authority. The protocol must record that hand-off and must not convert a recommendation into a final decision without the required authority.

## 6. Failure, uncertainty, and dispute handling

The integration must define behaviour for unavailable or stale legal sources, conflicting sources, ambiguous applicability, missing or disputed evidence, API timeout, malformed response, model failure, adversarial prompt or payload, unknown jurisdiction profile, and revoked or superseded rule versions.

Protected actions must fail closed or defer when required authority or jurisprudential preconditions cannot be established. “Fail closed” must not mean silently discarding the case: return a stable reason code, preserve the relevant trace, and provide a lawful route for correction, appeal, or human/institutional review. Re-evaluation after a source or rule update must preserve the version context of the original disposition.

## 7. Required acceptance tests

The MINAH integration is not accepted on persuasive prose or a successful happy-path demo alone. At minimum, tests must demonstrate:

1. **Jurisdiction isolation:** a rule/source from one jurisdiction cannot silently govern a different profile.
2. **Source traceability:** every consequential disposition identifies the source and version basis required by its policy.
3. **Effective-date correctness:** future, expired, superseded, and retroactively amended sources follow explicit rules.
4. **Evidence discipline:** missing, stale, disputed, contradictory, or unproven external facts produce the defined uncertain/defer/reject outcome.
5. **Authority separation:** identity does not imply authority; MINAH cannot grant or expand a principal's permissions.
6. **Non-bypassable enforcement:** an otherwise forbidden action remains rejected even when MINAH recommends or asserts permission.
7. **Adversarial resilience:** malformed, manipulated, prompt-injected, contradictory, and misrouted responses cannot trigger protected effects.
8. **Failure behaviour:** unavailable APIs, unknown versions, timeouts, and model failures yield explicit safe outcomes and trace records.
9. **Reserved decision rights:** cases requiring an independent official or institution are deferred, not finalised by MINAH.
10. **Audit and reproducibility:** a reviewer can reconstruct the sources, profile, model/adapter version, inputs, disposition, uncertainty, and downstream protocol decision, subject to privacy and lawful access controls.
11. **Evaluation quality:** jurisdiction-specific test cases are reviewed by appropriately qualified domain experts; false-permission and false-prohibition risks are measured and reported, not hidden in a single aggregate score.
12. **Change control:** changes to source collections, jurisdiction profile, prompts, model, adapter, or evaluation thresholds are versioned and re-tested before consequential use.

## 8. Evidence status and implementation path

The current synthetic demonstrator's deterministic MINAH stand-in is a test adapter for orchestration and negative-path behaviour. **It is not the full MINAH**, does not establish jurisdiction-specific legal reasoning, and must not be described as an authoritative legal engine.

Implementation should proceed in observable stages: freeze the response schema and authority boundary; build a source/versioned jurisdiction profile; implement governed API adapters and provenance; create expert-reviewed jurisdiction test sets; run adversarial and failure tests; then connect the disposition to independently enforced protocol transitions. Any production claim requires evidence for the actual deployment, jurisdiction, source set, model/adapter, and operational controls.

## 9. Relationship to the wider KIN-SYS system

MINAH performs jurisprudential reasoning and returns a traceable disposition. Identity and authority services establish who may act and under what mandate. IVM/CEE-informed execution enforces mandatory protocol and transition rules. Institutions retain decisions reserved to them by law. These responsibilities cooperate, but none substitutes for the others.

This contract is intended to guide the whole-system specification, requirement-to-evidence matrix, source map, implementation, and conformance tests. It is a design contract, not a claim of current completion.
