# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Institutional deployment domain validators, draft v0.1.

Structural and cross-record checks only; not persistence, authentication,
legal-source verification, cryptographic trust, or production isolation.
"""
from __future__ import annotations
from copy import deepcopy
from typing import Any

DOMAIN_VERSION = "kin-sys-institutional-domain-0.1"
INSTITUTION_STATES = {"PENDING_VERIFICATION", "VERIFIED", "SUSPENDED", "REVOKED"}
JURISDICTION_STATES = {"DRAFT", "SUBMITTED", "VALIDATED", "APPROVED", "ACTIVE", "RETIRED"}
MINAH_STATES = {"REGISTERED", "CONNECTIVITY_VERIFIED", "CONFORMANCE_PASSED", "ACTIVE", "SUSPENDED", "REVOKED"}
ADAPTER_MODES = {"NATIVE", "EXTERNAL_SERVICE", "MANAGED_RUNTIME"}
BINDING_STATES = {"DRAFT", "VALIDATED", "APPROVED", "ACTIVE", "SUSPENDED", "REVOKED"}
SOURCE_TYPES = {"STATUTE", "REGULATION", "DIRECTIVE", "TREATY", "INSTITUTIONAL_POLICY", "PROCEDURE", "AUTHORITY_RULE", "IDENTITY_REQUIREMENT", "OTHER"}
REQUIRED = {
 "institution": {"institution_id","legal_name","jurisdiction_ref","status","verification_ref"},
 "jurisdiction": {"package_id","version","owner_institution_id","jurisdiction_ref","status","sources","approval_ref"},
 "source": {"source_ref","source_type","title","publisher_ref","content_digest","provenance_ref","effective_from","status"},
 "minah": {"deployment_id","owner_institution_id","adapter_mode","adapter_ref","model_ref","interface_version","status"},
 "binding": {"binding_id","owner_institution_id","jurisdiction_package_id","jurisdiction_package_version","minah_deployment_id","policy_pack_version","source_pack_version","authority_profile_version","status","approval_ref"},
}

class DomainModelViolation(ValueError):
    """A record is malformed or violates a KIN-SYS domain invariant."""

def _record(value: Any, kind: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DomainModelViolation(kind.upper() + "_MUST_BE_OBJECT")
    missing = sorted(REQUIRED[kind] - set(value))
    if missing:
        raise DomainModelViolation("MISSING_" + kind.upper() + "_FIELDS:" + ",".join(missing))
    return deepcopy(value)

def _strings(record: dict[str, Any], fields: tuple[str, ...], kind: str) -> None:
    for field in fields:
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise DomainModelViolation("INVALID_" + kind.upper() + "_" + field.upper())

def validate_institution(value: Any) -> dict[str, Any]:
    r = _record(value, "institution")
    _strings(r, ("institution_id","legal_name","jurisdiction_ref"), "institution")
    if r["status"] not in INSTITUTION_STATES:
        raise DomainModelViolation("INVALID_INSTITUTION_STATUS")
    if r["status"] == "VERIFIED" and not r.get("verification_ref"):
        raise DomainModelViolation("VERIFIED_INSTITUTION_REQUIRES_VERIFICATION_EVIDENCE")
    return r

def validate_source(value: Any) -> dict[str, Any]:
    r = _record(value, "source")
    _strings(r, ("source_ref","source_type","title","publisher_ref","content_digest","provenance_ref","effective_from","status"), "source")
    if r["source_type"] not in SOURCE_TYPES:
        raise DomainModelViolation("INVALID_SOURCE_TYPE")
    if not r["content_digest"].startswith("sha256:"):
        raise DomainModelViolation("SOURCE_REQUIRES_SHA256_CONTENT_DIGEST")
    if r["status"] not in {"SUBMITTED","VALIDATED","APPROVED","REJECTED","SUPERSEDED"}:
        raise DomainModelViolation("INVALID_SOURCE_STATUS")
    if r["status"] == "APPROVED" and not r.get("approval_ref"):
        raise DomainModelViolation("APPROVED_SOURCE_REQUIRES_APPROVAL_EVIDENCE")
    return r

def validate_jurisdiction_package(value: Any) -> dict[str, Any]:
    r = _record(value, "jurisdiction")
    _strings(r, ("package_id","version","owner_institution_id","jurisdiction_ref"), "jurisdiction")
    if r["status"] not in JURISDICTION_STATES:
        raise DomainModelViolation("INVALID_JURISDICTION_STATUS")
    if not isinstance(r["sources"], list) or not r["sources"]:
        raise DomainModelViolation("JURISDICTION_REQUIRES_SOURCE_RECORDS")
    sources = [validate_source(s) for s in r["sources"]]
    refs = [s["source_ref"] for s in sources]
    if len(refs) != len(set(refs)):
        raise DomainModelViolation("DUPLICATE_JURISDICTION_SOURCE_REF")
    if r["status"] in {"APPROVED","ACTIVE"}:
        if not r.get("approval_ref"):
            raise DomainModelViolation("APPROVED_JURISDICTION_REQUIRES_APPROVAL_EVIDENCE")
        if any(s["status"] != "APPROVED" for s in sources):
            raise DomainModelViolation("ACTIVE_JURISDICTION_REQUIRES_APPROVED_SOURCES")
    r["sources"] = sources
    return r

def validate_minah_deployment(value: Any) -> dict[str, Any]:
    r = _record(value, "minah")
    _strings(r, ("deployment_id","owner_institution_id","adapter_mode","adapter_ref","model_ref","interface_version"), "minah")
    if r["adapter_mode"] not in ADAPTER_MODES:
        raise DomainModelViolation("UNSUPPORTED_MINAH_ADAPTER_MODE")
    if r["status"] not in MINAH_STATES:
        raise DomainModelViolation("INVALID_MINAH_STATUS")
    if any(k.lower() in {"api_key","secret","password","private_key","model_binary"} for k in r):
        raise DomainModelViolation("SENSITIVE_MATERIAL_MUST_NOT_BE_STORED_IN_DOMAIN_RECORD")
    return r

def validate_binding(value: Any) -> dict[str, Any]:
    r = _record(value, "binding")
    _strings(r, ("binding_id","owner_institution_id","jurisdiction_package_id","jurisdiction_package_version","minah_deployment_id","policy_pack_version","source_pack_version","authority_profile_version"), "binding")
    if r["status"] not in BINDING_STATES:
        raise DomainModelViolation("INVALID_BINDING_STATUS")
    if r["status"] in {"APPROVED","ACTIVE"} and not r.get("approval_ref"):
        raise DomainModelViolation("APPROVED_BINDING_REQUIRES_APPROVAL_EVIDENCE")
    return r

def validate_activation(institution_value: Any, jurisdiction_value: Any, minah_value: Any, binding_value: Any, *, conformance_evidence_ref: str | None) -> dict[str, Any]:
    """Check activation eligibility; evidence references still require trusted verification."""
    institution = validate_institution(institution_value)
    jurisdiction = validate_jurisdiction_package(jurisdiction_value)
    minah = validate_minah_deployment(minah_value)
    binding = validate_binding(binding_value)
    if institution["status"] != "VERIFIED":
        raise DomainModelViolation("INSTITUTION_NOT_VERIFIED")
    if jurisdiction["status"] != "APPROVED":
        raise DomainModelViolation("JURISDICTION_NOT_APPROVED")
    if minah["status"] != "CONFORMANCE_PASSED":
        raise DomainModelViolation("MINAH_CONFORMANCE_NOT_PASSED")
    if binding["status"] != "APPROVED":
        raise DomainModelViolation("BINDING_NOT_APPROVED")
    owner = institution["institution_id"]
    if any(x["owner_institution_id"] != owner for x in (jurisdiction,minah,binding)):
        raise DomainModelViolation("CROSS_TENANT_OWNERSHIP_MISMATCH")
    if jurisdiction["jurisdiction_ref"] != institution["jurisdiction_ref"]:
        raise DomainModelViolation("INSTITUTION_JURISDICTION_MISMATCH")
    if (binding["jurisdiction_package_id"],binding["jurisdiction_package_version"]) != (jurisdiction["package_id"],jurisdiction["version"]):
        raise DomainModelViolation("JURISDICTION_BINDING_VERSION_MISMATCH")
    if binding["minah_deployment_id"] != minah["deployment_id"]:
        raise DomainModelViolation("MINAH_BINDING_MISMATCH")
    if not isinstance(conformance_evidence_ref,str) or not conformance_evidence_ref.strip():
        raise DomainModelViolation("CONFORMANCE_EVIDENCE_REQUIRED")
    return {"domain_version":DOMAIN_VERSION,"activation_eligible":True,"institution_id":owner,
            "jurisdiction_package":jurisdiction["package_id"]+"@"+jurisdiction["version"],
            "minah_deployment_id":minah["deployment_id"],"binding_id":binding["binding_id"],
            "conformance_evidence_ref":conformance_evidence_ref,"status":"ELIGIBLE_FOR_AUTHORISED_ACTIVATION"}
