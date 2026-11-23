# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Authority-gated application service for institutional trust administration.

The SQLite/PostgreSQL registries are persistence components, not public
administrative APIs. Mutations here require a receiver-local HMAC assertion,
an exact resource scope, an action-specific role, and verified evidence.

The first local authority grant is intentionally not bootstrapped here. It
must be provisioned through a separately controlled institutional ceremony.
There is no self-authorising bootstrap operation in this service.
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Protocol

from institutional_grant_provenance import (
    canonical_grant_bytes, verify_authority_grant,
)


class InstitutionalTrustAdministrationError(ValueError):
    """A trust or delegated-authority administration request was rejected."""


class TrustEvidenceVerifier(Protocol):
    """Resolve and verify durable institutional approval evidence."""

    def verify(self, evidence_ref: str, *, context: dict[str, Any]) -> dict[str, Any]:
        """Return verified=True, the same evidence_ref, a receipt ref and digest."""


ROLE_BY_ACTION = {
    "CREATE_GRANT": "authority_grant_issuer",
    "REVISE_GRANT": "authority_grant_amender",
    "REVOKE_GRANT": "authority_grant_revoker",
    "ESTABLISH_ISSUER_TRUST": "institution_trust_establisher",
    "REVOKE_ISSUER_TRUST": "institution_trust_revoker",
    "REGISTER_TRUST_KEY": "institution_trust_key_registrar",
    "REVOKE_TRUST_KEY": "institution_trust_key_revoker",
    "ACCEPT_FEDERATED_GRANT": "federated_grant_acceptor",
}


class InstitutionalTrustAdministration:
    """Gate registry mutations with receiver-local delegated authority."""

    _DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")

    def __init__(
        self,
        *,
        registry: Any,
        authority_resolver: Any,
        evidence_verifier: TrustEvidenceVerifier,
        issuer_public_key_loader: Any = None,
        receiver_signing_key_id_loader: Any = None,
        receiver_signing_key_loader: Any = None,
        clock: Any = time.time,
    ):
        required_registry_methods = (
            "load_grant", "create_grant_version", "create_grant_revision",
            "revoke_grant", "register_trusted_issuer", "revoke_trusted_issuer",
            "list_trusted_issuer_events", "register_trust_key", "get_trust_key",
            "revoke_trust_key", "accept_signed_grant",
        )
        if any(not callable(getattr(registry, name, None))
               for name in required_registry_methods):
            raise InstitutionalTrustAdministrationError(
                "INSTITUTIONAL_TRUST_REGISTRY_REQUIRED"
            )
        if not callable(getattr(authority_resolver, "resolve", None)):
            raise InstitutionalTrustAdministrationError(
                "VERIFIED_AUTHORITY_RESOLVER_REQUIRED"
            )
        if not callable(getattr(evidence_verifier, "verify", None)):
            raise InstitutionalTrustAdministrationError(
                "TRUST_EVIDENCE_VERIFIER_REQUIRED"
            )
        adapters = (
            issuer_public_key_loader,
            receiver_signing_key_id_loader,
            receiver_signing_key_loader,
        )
        if any(value is not None for value in adapters) and not all(
            callable(value) for value in adapters
        ):
            raise InstitutionalTrustAdministrationError(
                "FEDERATED_ACCEPTANCE_ADAPTERS_INCOMPLETE"
            )
        if not callable(clock):
            raise InstitutionalTrustAdministrationError("TRUST_ADMIN_CLOCK_INVALID")
        self._registry = registry
        self._authority = authority_resolver
        self._evidence = evidence_verifier
        self._issuer_public_key_loader = issuer_public_key_loader
        self._receiver_signing_key_id_loader = receiver_signing_key_id_loader
        self._receiver_signing_key_loader = receiver_signing_key_loader
        self._clock = clock

    @staticmethod
    def _required(value: Any, code: str) -> str:
        if (not isinstance(value, str) or not value.strip()
                or len(value) > 256 or any(ord(ch) < 32 for ch in value)):
            raise InstitutionalTrustAdministrationError(code)
        return value

    @staticmethod
    def _trust_key_resource_id(issuer_institution_id: str, key_id: str) -> str:
        # Length prefixes make the issuer/key pair unambiguous even when an
        # institution identifier itself contains the delimiter character.
        return (
            "trust-key:" + str(len(issuer_institution_id)) + ":"
            + issuer_institution_id + ":" + str(len(key_id)) + ":" + key_id
        )

    @classmethod
    def _subject_digest(cls, subject: dict[str, Any]) -> str:
        try:
            digest = hashlib.sha256(canonical_grant_bytes(subject)).hexdigest()
        except Exception as exc:
            raise InstitutionalTrustAdministrationError(
                "TRUST_ADMIN_SUBJECT_NOT_CANONICALISABLE"
            ) from exc
        return "sha256:" + digest

    def _authorise(
        self,
        assertion: dict[str, Any],
        *,
        tenant_id: str,
        resource_type: str,
        resource_id: str,
        action: str,
    ) -> dict[str, Any]:
        tenant = self._required(tenant_id, "TENANT_CONTEXT_REQUIRED")
        required_role = ROLE_BY_ACTION.get(action)
        if required_role is None:
            raise InstitutionalTrustAdministrationError(
                "TRUST_ADMIN_ACTION_UNSUPPORTED"
            )
        try:
            principal = self._authority.resolve(
                assertion,
                tenant_id=tenant,
                resource_type=resource_type,
                resource_id=resource_id,
            )
        except Exception as exc:
            raise InstitutionalTrustAdministrationError(
                "TRUST_ADMIN_AUTHORITY_REJECTED"
            ) from exc
        if (not isinstance(principal, dict)
                or principal.get("authenticated") is not True
                or principal.get("verified") is not True):
            raise InstitutionalTrustAdministrationError(
                "VERIFIED_TRUST_ADMIN_PRINCIPAL_REQUIRED"
            )
        # HMAC is accepted only for this receiver-local administration boundary.
        # Cross-institution issuer grants and actor assertions use Ed25519.
        if (principal.get("assertion_algorithm") != "HMAC-SHA256"
                or principal.get("assertion_issuer_institution_id") is not None):
            raise InstitutionalTrustAdministrationError(
                "TRUST_ADMIN_REQUIRES_RECEIVER_LOCAL_ASSERTION"
            )
        roles = principal.get("roles")
        if not isinstance(roles, list) or required_role not in roles:
            raise InstitutionalTrustAdministrationError(
                "TRUST_ADMIN_ROLE_INSUFFICIENT"
            )
        for field, code in (
            ("actor_ref", "TRUST_ADMIN_ACTOR_INVALID"),
            ("institution_id", "TRUST_ADMIN_INSTITUTION_INVALID"),
            ("authority_ref", "TRUST_ADMIN_AUTHORITY_REF_INVALID"),
            ("grant_ref", "TRUST_ADMIN_GRANT_REF_INVALID"),
        ):
            self._required(principal.get(field), code)
        return principal

    def _verify_evidence(
        self,
        evidence_ref: str,
        *,
        tenant_id: str,
        principal: dict[str, Any],
        resource_type: str,
        resource_id: str,
        action: str,
        subject: dict[str, Any],
        expected_version: str | None = None,
    ) -> dict[str, str]:
        source_ref = self._required(evidence_ref, "TRUST_ADMIN_EVIDENCE_REQUIRED")
        context = {
            "purpose": "INSTITUTIONAL_TRUST_ADMINISTRATION",
            "tenant_id": tenant_id,
            "institution_id": principal["institution_id"],
            "resource_type": resource_type,
            "resource_id": resource_id,
            "action": action,
            "subject_digest": self._subject_digest(subject),
            "expected_version": expected_version,
        }
        try:
            receipt = self._evidence.verify(source_ref, context=context)
        except Exception as exc:
            raise InstitutionalTrustAdministrationError(
                "TRUST_ADMIN_EVIDENCE_VERIFICATION_FAILED"
            ) from exc
        if (not isinstance(receipt, dict)
                or receipt.get("verified") is not True
                or receipt.get("evidence_ref") != source_ref):
            raise InstitutionalTrustAdministrationError(
                "TRUST_ADMIN_EVIDENCE_NOT_VERIFIED"
            )
        verification_ref = receipt.get("verification_ref")
        digest = receipt.get("evidence_digest")
        if (not isinstance(verification_ref, str) or not verification_ref.strip()
                or not isinstance(digest, str) or not self._DIGEST.fullmatch(digest)):
            raise InstitutionalTrustAdministrationError(
                "TRUST_ADMIN_EVIDENCE_RECEIPT_INVALID"
            )
        return {
            "verification_ref": verification_ref,
            "evidence_digest": digest,
        }

    @staticmethod
    def _local_grant(
        grant: dict[str, Any],
        *,
        tenant_id: str,
        principal: dict[str, Any],
        active_required: bool = True,
    ) -> None:
        if not isinstance(grant, dict) or grant.get("tenant_id") != tenant_id:
            raise InstitutionalTrustAdministrationError("GRANT_TENANT_CONTEXT_MISMATCH")
        institution_id = principal["institution_id"]
        if (grant.get("institution_id") != institution_id
                or grant.get("issuer_institution_id") != institution_id):
            raise InstitutionalTrustAdministrationError(
                "LOCAL_GRANT_INSTITUTION_OR_ISSUER_MISMATCH"
            )
        allowed_statuses = {"ACTIVE"} if active_required else {"ACTIVE", "SUSPENDED"}
        if grant.get("status") not in allowed_statuses:
            raise InstitutionalTrustAdministrationError("LOCAL_GRANT_STATUS_NOT_ALLOWED")
        if grant.get("actor_ref") == principal["actor_ref"]:
            raise InstitutionalTrustAdministrationError(
                "SELF_DELEGATION_REQUIRES_SEPARATE_APPROVAL"
            )

    @staticmethod
    def _result(result: dict[str, Any], principal: dict[str, Any],
                evidence: dict[str, str]) -> dict[str, Any]:
        return dict(
            result,
            actor_ref=principal["actor_ref"],
            authority_grant_ref=principal["grant_ref"],
            evidence_verification_ref=evidence["verification_ref"],
        )

    def create_grant_version(
        self, *, tenant_id: str, grant: dict[str, Any],
        actor_assertion: dict[str, Any], event_id: str, evidence_ref: str,
    ) -> dict[str, Any]:
        tenant = self._required(tenant_id, "TENANT_CONTEXT_REQUIRED")
        if not isinstance(grant, dict):
            raise InstitutionalTrustAdministrationError("AUTHORITY_GRANT_REQUIRED")
        grant_id = self._required(grant.get("grant_id"), "INVALID_GRANT_ID")
        principal = self._authorise(
            actor_assertion, tenant_id=tenant, resource_type="authority_grant",
            resource_id=grant_id, action="CREATE_GRANT",
        )
        if self._registry.load_grant(tenant, grant_id) is not None:
            raise InstitutionalTrustAdministrationError("AUTHORITY_GRANT_ALREADY_EXISTS")
        self._local_grant(grant, tenant_id=tenant, principal=principal)
        evidence = self._verify_evidence(
            evidence_ref, tenant_id=tenant, principal=principal,
            resource_type="authority_grant", resource_id=grant_id,
            action="CREATE_GRANT", subject=grant,
        )
        result = self._registry.create_grant_version(
            tenant_id=tenant, grant=grant, event_id=event_id,
            actor_ref=principal["actor_ref"],
            evidence_ref=evidence["verification_ref"],
        )
        return self._result(result, principal, evidence)

    def create_grant_revision(
        self, *, tenant_id: str, grant_id: str, expected_version: str,
        grant: dict[str, Any], actor_assertion: dict[str, Any],
        event_id: str, evidence_ref: str,
    ) -> dict[str, Any]:
        tenant = self._required(tenant_id, "TENANT_CONTEXT_REQUIRED")
        grant_key = self._required(grant_id, "INVALID_GRANT_ID")
        expected = self._required(expected_version, "INVALID_EXPECTED_GRANT_VERSION")
        principal = self._authorise(
            actor_assertion, tenant_id=tenant, resource_type="authority_grant",
            resource_id=grant_key, action="REVISE_GRANT",
        )
        current = self._registry.load_grant(tenant, grant_key)
        if current is None:
            raise InstitutionalTrustAdministrationError("AUTHORITY_GRANT_NOT_FOUND")
        if current.get("version") != expected:
            raise InstitutionalTrustAdministrationError("STALE_GRANT_VERSION")
        self._local_grant(current, tenant_id=tenant, principal=principal)
        self._local_grant(grant, tenant_id=tenant, principal=principal)
        if (grant.get("grant_id") != grant_key
                or grant.get("version") == expected
                or grant.get("actor_ref") != current.get("actor_ref")
                or grant.get("authority_ref") != current.get("authority_ref")):
            raise InstitutionalTrustAdministrationError(
                "GRANT_REVISION_IDENTITY_OR_VERSION_MISMATCH"
            )
        evidence = self._verify_evidence(
            evidence_ref, tenant_id=tenant, principal=principal,
            resource_type="authority_grant", resource_id=grant_key,
            action="REVISE_GRANT", subject=grant, expected_version=expected,
        )
        result = self._registry.create_grant_revision(
            tenant_id=tenant, grant_id=grant_key, expected_version=expected,
            grant=grant, event_id=event_id, actor_ref=principal["actor_ref"],
            evidence_ref=evidence["verification_ref"],
        )
        return self._result(result, principal, evidence)

    def revoke_grant(
        self, *, tenant_id: str, grant_id: str, expected_version: str,
        new_version: str, actor_assertion: dict[str, Any],
        event_id: str, evidence_ref: str,
    ) -> dict[str, Any]:
        tenant = self._required(tenant_id, "TENANT_CONTEXT_REQUIRED")
        grant_key = self._required(grant_id, "INVALID_GRANT_ID")
        expected = self._required(expected_version, "INVALID_EXPECTED_GRANT_VERSION")
        new = self._required(new_version, "INVALID_NEW_GRANT_VERSION")
        if new == expected:
            raise InstitutionalTrustAdministrationError(
                "GRANT_REVOCATION_VERSION_MUST_ADVANCE"
            )
        principal = self._authorise(
            actor_assertion, tenant_id=tenant, resource_type="authority_grant",
            resource_id=grant_key, action="REVOKE_GRANT",
        )
        current = self._registry.load_grant(tenant, grant_key)
        if current is None:
            raise InstitutionalTrustAdministrationError("AUTHORITY_GRANT_NOT_FOUND")
        if current.get("version") != expected:
            raise InstitutionalTrustAdministrationError("STALE_GRANT_VERSION")
        self._local_grant(
            current, tenant_id=tenant, principal=principal, active_required=False,
        )
        subject = {
            "grant_id": grant_key, "expected_version": expected,
            "new_version": new, "status": "REVOKED",
            "current_grant_digest": self._subject_digest(current),
        }
        evidence = self._verify_evidence(
            evidence_ref, tenant_id=tenant, principal=principal,
            resource_type="authority_grant", resource_id=grant_key,
            action="REVOKE_GRANT", subject=subject, expected_version=expected,
        )
        result = self._registry.revoke_grant(
            tenant_id=tenant, grant_id=grant_key, expected_version=expected,
            new_version=new, event_id=event_id, actor_ref=principal["actor_ref"],
            evidence_ref=evidence["verification_ref"],
        )
        return self._result(result, principal, evidence)

    def establish_issuer_trust(
        self, *, tenant_id: str, issuer_institution_id: str,
        trust_basis_ref: str, valid_from: int, valid_until: int,
        actor_assertion: dict[str, Any], evidence_ref: str,
    ) -> dict[str, Any]:
        tenant = self._required(tenant_id, "TENANT_CONTEXT_REQUIRED")
        issuer = self._required(issuer_institution_id, "INVALID_ISSUER_ID")
        principal = self._authorise(
            actor_assertion, tenant_id=tenant, resource_type="trusted_issuer",
            resource_id=issuer, action="ESTABLISH_ISSUER_TRUST",
        )
        if issuer == principal["institution_id"]:
            raise InstitutionalTrustAdministrationError(
                "SELF_TRUST_ROOT_NOT_REQUIRED"
            )
        if self._registry.list_trusted_issuer_events(
            tenant_id=tenant, issuer_institution_id=issuer,
        ):
            raise InstitutionalTrustAdministrationError(
                "ISSUER_TRUST_ALREADY_CONFIGURED"
            )
        subject = {
            "issuer_institution_id": issuer, "trust_basis_ref": trust_basis_ref,
            "valid_from": valid_from, "valid_until": valid_until,
        }
        evidence = self._verify_evidence(
            evidence_ref, tenant_id=tenant, principal=principal,
            resource_type="trusted_issuer", resource_id=issuer,
            action="ESTABLISH_ISSUER_TRUST", subject=subject,
        )
        result = self._registry.register_trusted_issuer(
            tenant_id=tenant, issuer_institution_id=issuer,
            trust_basis_ref=trust_basis_ref, valid_from=valid_from,
            valid_until=valid_until, actor_ref=principal["actor_ref"],
            evidence_ref=evidence["verification_ref"],
        )
        return self._result(result, principal, evidence)

    def revoke_issuer_trust(
        self, *, tenant_id: str, issuer_institution_id: str,
        actor_assertion: dict[str, Any], event_id: str, evidence_ref: str,
    ) -> dict[str, Any]:
        tenant = self._required(tenant_id, "TENANT_CONTEXT_REQUIRED")
        issuer = self._required(issuer_institution_id, "INVALID_ISSUER_ID")
        principal = self._authorise(
            actor_assertion, tenant_id=tenant, resource_type="trusted_issuer",
            resource_id=issuer, action="REVOKE_ISSUER_TRUST",
        )
        if issuer == principal["institution_id"]:
            raise InstitutionalTrustAdministrationError(
                "LOCAL_INSTITUTION_TRUST_ROOT_CANNOT_BE_REVOKED_HERE"
            )
        events = self._registry.list_trusted_issuer_events(
            tenant_id=tenant, issuer_institution_id=issuer,
        )
        if not events or events[-1].get("event_type") == "ISSUER_TRUST_REVOKED":
            raise InstitutionalTrustAdministrationError(
                "ACTIVE_ISSUER_TRUST_NOT_FOUND"
            )
        subject = {
            "issuer_institution_id": issuer, "status": "REVOKED",
            "prior_event_id": events[-1].get("event_id"),
        }
        evidence = self._verify_evidence(
            evidence_ref, tenant_id=tenant, principal=principal,
            resource_type="trusted_issuer", resource_id=issuer,
            action="REVOKE_ISSUER_TRUST", subject=subject,
        )
        result = self._registry.revoke_trusted_issuer(
            tenant_id=tenant, issuer_institution_id=issuer,
            actor_ref=principal["actor_ref"],
            evidence_ref=evidence["verification_ref"], event_id=event_id,
        )
        return self._result(result, principal, evidence)

    def register_trust_key(
        self, *, tenant_id: str, issuer_institution_id: str, key_id: str,
        algorithm: str, public_key_ref: str, valid_from: int, valid_until: int,
        actor_assertion: dict[str, Any], evidence_ref: str,
    ) -> dict[str, Any]:
        tenant = self._required(tenant_id, "TENANT_CONTEXT_REQUIRED")
        issuer = self._required(issuer_institution_id, "INVALID_ISSUER_ID")
        key = self._required(key_id, "INVALID_KEY_ID")
        if algorithm != "Ed25519":
            raise InstitutionalTrustAdministrationError(
                "FEDERATION_TRUST_KEYS_MUST_USE_ED25519"
            )
        principal = self._authorise(
            actor_assertion, tenant_id=tenant, resource_type="trust_key",
            resource_id=self._trust_key_resource_id(issuer, key),
            action="REGISTER_TRUST_KEY",
        )
        if issuer == principal["institution_id"]:
            raise InstitutionalTrustAdministrationError(
                "LOCAL_HMAC_KEYS_ARE_NOT_FEDERATION_TRUST_KEYS"
            )
        if self._registry.get_trust_key(tenant_id=tenant, key_id=key) is not None:
            raise InstitutionalTrustAdministrationError("TRUST_KEY_ALREADY_CONFIGURED")
        subject = {
            "issuer_institution_id": issuer, "key_id": key,
            "algorithm": algorithm, "public_key_ref": public_key_ref,
            "valid_from": valid_from, "valid_until": valid_until,
        }
        evidence = self._verify_evidence(
            evidence_ref, tenant_id=tenant, principal=principal,
            resource_type="trust_key", resource_id=key,
            action="REGISTER_TRUST_KEY", subject=subject,
        )
        result = self._registry.register_trust_key(
            tenant_id=tenant, issuer_institution_id=issuer, key_id=key,
            algorithm=algorithm, public_key_ref=public_key_ref,
            valid_from=valid_from, valid_until=valid_until,
            actor_ref=principal["actor_ref"],
            evidence_ref=evidence["verification_ref"],
        )
        return self._result(result, principal, evidence)

    def revoke_trust_key(
        self, *, tenant_id: str, issuer_institution_id: str, key_id: str,
        actor_assertion: dict[str, Any], event_id: str, evidence_ref: str,
    ) -> dict[str, Any]:
        tenant = self._required(tenant_id, "TENANT_CONTEXT_REQUIRED")
        issuer = self._required(issuer_institution_id, "INVALID_ISSUER_ID")
        key = self._required(key_id, "INVALID_KEY_ID")
        principal = self._authorise(
            actor_assertion, tenant_id=tenant, resource_type="trust_key",
            resource_id=self._trust_key_resource_id(issuer, key),
            action="REVOKE_TRUST_KEY",
        )
        record = self._registry.get_trust_key(tenant_id=tenant, key_id=key)
        if record is None or record.get("status") != "ACTIVE":
            raise InstitutionalTrustAdministrationError("ACTIVE_TRUST_KEY_NOT_FOUND")
        if record.get("issuer_institution_id") != issuer:
            raise InstitutionalTrustAdministrationError("TRUST_KEY_ISSUER_MISMATCH")
        if record.get("issuer_institution_id") == principal["institution_id"]:
            raise InstitutionalTrustAdministrationError(
                "LOCAL_HMAC_KEYS_ARE_NOT_FEDERATION_TRUST_KEYS"
            )
        subject = {
            "key_id": key, "issuer_institution_id": record["issuer_institution_id"],
            "status": "REVOKED", "public_key_ref": record["public_key_ref"],
        }
        evidence = self._verify_evidence(
            evidence_ref, tenant_id=tenant, principal=principal,
            resource_type="trust_key", resource_id=key,
            action="REVOKE_TRUST_KEY", subject=subject,
        )
        result = self._registry.revoke_trust_key(
            tenant_id=tenant, key_id=key, actor_ref=principal["actor_ref"],
            evidence_ref=evidence["verification_ref"], event_id=event_id,
        )
        return self._result(result, principal, evidence)

    def _federated_acceptance_context(
        self, *, tenant_id: str, envelope: dict[str, Any],
        receiver_institution_id: str, actor_assertion: dict[str, Any],
        evidence_ref: str, action: str, expected_version: str | None = None,
        expected_grant_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, str], str]:
        tenant = self._required(tenant_id, "TENANT_CONTEXT_REQUIRED")
        receiver = self._required(
            receiver_institution_id, "INVALID_RECEIVER_INSTITUTION",
        )
        claims = envelope.get("claims") if isinstance(envelope, dict) else None
        grant = claims.get("grant") if isinstance(claims, dict) else None
        if not isinstance(grant, dict):
            raise InstitutionalTrustAdministrationError("FEDERATED_GRANT_ENVELOPE_INVALID")
        grant_id = self._required(grant.get("grant_id"), "INVALID_GRANT_ID")
        if (grant.get("tenant_id") != tenant or grant.get("institution_id") != receiver
                or grant.get("status") != "PENDING"
                or claims.get("issuer_institution_id") != grant.get("issuer_institution_id")
                or grant.get("issuer_institution_id") == receiver
                or (expected_grant_id is not None and grant_id != expected_grant_id)):
            raise InstitutionalTrustAdministrationError(
                "FEDERATED_GRANT_RECEIVER_OR_ISSUER_MISMATCH"
            )
        principal = self._authorise(
            actor_assertion, tenant_id=tenant, resource_type="authority_grant",
            resource_id=grant_id, action=action,
        )
        if principal["institution_id"] != receiver:
            raise InstitutionalTrustAdministrationError(
                "FEDERATED_GRANT_RECEIVER_ADMINISTRATOR_MISMATCH"
            )
        try:
            verified_grant = verify_authority_grant(
                envelope, tenant_id=tenant,
                trust_key_loader=self._registry.load_trust_key,
                public_key_loader=self._issuer_public_key_loader,
                clock=self._clock,
            )
        except Exception as exc:
            raise InstitutionalTrustAdministrationError(
                "FEDERATED_GRANT_ISSUER_PROVENANCE_INVALID"
            ) from exc
        if verified_grant != grant:
            raise InstitutionalTrustAdministrationError(
                "FEDERATED_GRANT_SIGNED_CLAIMS_MISMATCH"
            )
        digest = hashlib.sha256(canonical_grant_bytes(grant)).hexdigest()
        evidence = self._verify_evidence(
            evidence_ref, tenant_id=tenant, principal=principal,
            resource_type="authority_grant", resource_id=grant_id,
            action=action, subject=grant, expected_version=expected_version,
        )
        approval = {
            "actor_ref": principal["actor_ref"],
            "approval_ref": evidence_ref,
            "authority_ref": principal["authority_ref"],
            "authority_grant_ref": principal["grant_ref"],
            "grant_digest": digest,
            "evidence_verification": evidence,
        }
        return principal, approval, evidence, digest

    def _federation_adapters(self) -> tuple[Any, Any, Any]:
        if (self._issuer_public_key_loader is None
                or self._receiver_signing_key_id_loader is None
                or self._receiver_signing_key_loader is None):
            raise InstitutionalTrustAdministrationError(
                "FEDERATED_ACCEPTANCE_ADAPTERS_REQUIRED"
            )
        return (
            self._issuer_public_key_loader,
            self._receiver_signing_key_id_loader,
            self._receiver_signing_key_loader,
        )

    @staticmethod
    def _receiver_approval_verifier(
        pending_grant: dict[str, Any], approval: dict[str, Any],
        principal: dict[str, Any], tenant_id: str, receiver_institution_id: str,
    ):
        def verify(candidate: dict[str, Any], supplied: dict[str, Any]) -> bool:
            return (
                candidate == pending_grant
                and supplied == approval
                and candidate.get("tenant_id") == tenant_id
                and candidate.get("institution_id") == receiver_institution_id
                and supplied.get("actor_ref") == principal["actor_ref"]
                and supplied.get("authority_grant_ref") == principal["grant_ref"]
            )
        return verify

    def accept_federated_grant(
        self, *, tenant_id: str, envelope: dict[str, Any],
        receiver_institution_id: str, actor_assertion: dict[str, Any],
        event_id: str, evidence_ref: str,
    ) -> dict[str, Any]:
        public_key_loader, key_id_loader, signer_loader = self._federation_adapters()
        principal, approval, evidence, _ = self._federated_acceptance_context(
            tenant_id=tenant_id, envelope=envelope,
            receiver_institution_id=receiver_institution_id,
            actor_assertion=actor_assertion, evidence_ref=evidence_ref,
            action="ACCEPT_FEDERATED_GRANT",
        )
        try:
            signing_key_id = key_id_loader(tenant_id, receiver_institution_id)
        except Exception as exc:
            raise InstitutionalTrustAdministrationError(
                "RECEIVER_ACCEPTANCE_KEY_SELECTION_FAILED"
            ) from exc
        self._required(
            signing_key_id, "RECEIVER_ACCEPTANCE_SIGNING_KEY_REQUIRED",
        )
        pending_grant = envelope["claims"]["grant"]
        verifier = self._receiver_approval_verifier(
            pending_grant, approval, principal, tenant_id, receiver_institution_id,
        )
        try:
            result = self._registry.accept_signed_grant(
                tenant_id=tenant_id, envelope=envelope,
                receiver_institution_id=receiver_institution_id,
                receiver_approval=approval,
                verify_receiver_approval=verifier,
                public_key_loader=public_key_loader,
                event_id=event_id, actor_ref=principal["actor_ref"],
                evidence_ref=evidence_ref,
                receiver_signing_key_id=signing_key_id,
                receiver_signing_key_loader=signer_loader,
                clock=self._clock,
            )
        except Exception as exc:
            raise InstitutionalTrustAdministrationError(
                "FEDERATED_GRANT_ACCEPTANCE_REJECTED"
            ) from exc
        return self._result(result, principal, evidence)

    def accept_federated_grant_revision(
        self, *, tenant_id: str, grant_id: str, expected_version: str,
        envelope: dict[str, Any], receiver_institution_id: str,
        actor_assertion: dict[str, Any], event_id: str, evidence_ref: str,
    ) -> dict[str, Any]:
        revision_method = getattr(
            self._registry, "accept_signed_grant_revision", None,
        )
        if not callable(revision_method):
            raise InstitutionalTrustAdministrationError(
                "FEDERATED_GRANT_REVISION_NOT_SUPPORTED_BY_BACKEND"
            )
        public_key_loader, key_id_loader, signer_loader = self._federation_adapters()
        tenant = self._required(tenant_id, "TENANT_CONTEXT_REQUIRED")
        grant_key = self._required(grant_id, "INVALID_GRANT_ID")
        expected = self._required(expected_version, "INVALID_EXPECTED_GRANT_VERSION")
        receiver = self._required(
            receiver_institution_id, "INVALID_RECEIVER_INSTITUTION",
        )
        principal, approval, evidence, _ = self._federated_acceptance_context(
            tenant_id=tenant, envelope=envelope,
            receiver_institution_id=receiver, actor_assertion=actor_assertion,
            evidence_ref=evidence_ref, action="ACCEPT_FEDERATED_GRANT",
            expected_version=expected, expected_grant_id=grant_key,
        )
        current = self._registry.load_grant(tenant, grant_key)
        if current is None:
            raise InstitutionalTrustAdministrationError("AUTHORITY_GRANT_NOT_FOUND")
        if current.get("version") != expected:
            raise InstitutionalTrustAdministrationError("STALE_GRANT_VERSION")
        if (current.get("status") == "REVOKED"
                or current.get("institution_id") != receiver):
            raise InstitutionalTrustAdministrationError(
                "FEDERATED_GRANT_REVISION_LINEAGE_INVALID"
            )
        pending_grant = envelope["claims"]["grant"]
        if (pending_grant.get("grant_id") != grant_key
                or pending_grant.get("issuer_institution_id")
                != current.get("issuer_institution_id")
                or pending_grant.get("actor_ref") != current.get("actor_ref")
                or pending_grant.get("authority_ref") != current.get("authority_ref")
                or pending_grant.get("version") == expected):
            raise InstitutionalTrustAdministrationError(
                "FEDERATED_GRANT_REVISION_LINEAGE_INVALID"
            )
        try:
            signing_key_id = key_id_loader(tenant, receiver)
        except Exception as exc:
            raise InstitutionalTrustAdministrationError(
                "RECEIVER_ACCEPTANCE_KEY_SELECTION_FAILED"
            ) from exc
        self._required(
            signing_key_id, "RECEIVER_ACCEPTANCE_SIGNING_KEY_REQUIRED",
        )
        verifier = self._receiver_approval_verifier(
            pending_grant, approval, principal, tenant, receiver,
        )
        try:
            result = revision_method(
                tenant_id=tenant, grant_id=grant_key, expected_version=expected,
                envelope=envelope, receiver_institution_id=receiver,
                receiver_approval=approval, verify_receiver_approval=verifier,
                public_key_loader=public_key_loader, event_id=event_id,
                actor_ref=principal["actor_ref"], evidence_ref=evidence_ref,
                receiver_signing_key_id=signing_key_id,
                receiver_signing_key_loader=signer_loader, clock=self._clock,
            )
        except Exception as exc:
            raise InstitutionalTrustAdministrationError(
                "FEDERATED_GRANT_REVISION_ACCEPTANCE_REJECTED"
            ) from exc
        return self._result(result, principal, evidence)
