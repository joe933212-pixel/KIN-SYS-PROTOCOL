# KIN-SYS Protocol Use Cases

These protocol-level scenarios support requirements discovery. They are not claims of deployed functionality. Each must become an executable end-to-end test with positive, negative, and recovery paths.

## UC-01 — Authorised institutional transition

**Actors:** requesting principal, institution policy authority, protocol node, audit/history service.  
**Goal:** execute a state change only when identity, authority, preconditions, and applicable rules are valid.

**Success:** one transition is committed and linked to its rule version.  
**Reject:** missing, expired, revoked, or insufficient authority; invalid precondition; unsupported schema; conflicting operation ID.  
**Failure tests:** terminate before commitment, between state and history commitment, and after commitment but before acknowledgement. Retry must not create a second effect.

## UC-02 — Intermittently connected edge node

**Actors:** local operator, edge node, remote authority, synchronisation service.  
**Goal:** permit only bounded offline actions and reconcile after reconnection.

**Success:** each action is labelled local/provisional or final according to policy.  
**Reject/defer:** action exceeds offline authority, credential freshness expires, or online approval is required.  
**Failure tests:** duplicate sync, reordered events, revocation during disconnection, clock skew, and conflicting transitions.

## UC-03 — Cross-institution request

**Actors:** institution A, institution B, authorised principal, receiving node.  
**Goal:** exchange a request without treating one institution's identity or policy as universal authority.

**Success:** receiver validates sender, audience, purpose, trust relationship, and local authorisation.  
**Reject:** unknown issuer, wrong audience, untrusted trust-root change, incompatible version, or insufficient authority.  
**Open question:** which trust federation and dispute mechanism applies to each deployment?

## UC-04 — Economic effect without duplication

**Actors:** authorised originator, accounting component, recipient, auditor.  
**Goal:** record a fee, reward, entitlement, or settlement instruction exactly once under a declared rule.

**Success:** one unique effect is recorded with authority, rule version, accounting evidence, and reconciliation path.  
**Reject:** reused effect ID with different content, unauthorised issuance, invalid state, or inconsistent totals.  
**Failure tests:** retry after timeout, concurrent duplicates, partial commitment, disputed source evidence, and process-death recovery.

This does not define $KIN economics or imply that a token establishes legal title.

## UC-05 — AI-assisted recommendation with institutional control

**Actors:** MINAH AI or another assistance service, authorised decision-maker, protocol evaluator, auditor.  
**Goal:** use AI to assist without bypassing authority or policy.

**Success:** model output remains a recommendation/input; deterministic policy and authority checks govern consequential transitions.  
**Reject:** attempted direct privileged execution, missing approval, untrusted external instruction, or disallowed model version.  
**Failure tests:** malformed output, hallucinated entity, prompt injection, unavailable service, and model-version change.

## UC-06 — Edge-node compromise or decommissioning

**Actors:** node operator, credential authority, incident responders.  
**Goal:** limit damage after a device is lost, compromised, or retired.

**Success:** credentials are revoked, affected operations identified, retained data handled by policy, and recovery audited.  
**Failure tests:** offline revocation delay, copied key, incomplete inventory, tampered software, and stale-credential rejoin.

## UC-07 — Audit and dispute

**Actors:** affected party, institution, auditor, protocol node.  
**Goal:** explain why a transition occurred without disclosing unnecessary sensitive data.

**Success:** authorised reviewers can retrieve request, authority decision, rule version, transition result, and integrity evidence under privacy-aware access controls.  
**Failure tests:** missing history, inconsistent state/history, unauthorised audit request, retention expiry, and disputed source data.
