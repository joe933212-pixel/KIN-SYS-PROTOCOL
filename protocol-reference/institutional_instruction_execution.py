# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Institution-bound governed instruction execution.

The service resolves an active institutional binding before execution, selects
an institution-configured pipeline through an injected factory, and places a
safe immutable version set inside the pipeline request fingerprint. The factory
is responsible for returning a durable pipeline configured with that
institution's approved MINAH, identity and authority adapters.
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable

from governed_contracts import (
    assert_instruction_binding,
    validate_decision_envelope,
    verify_instruction_payload_digest,
)
from institutional_runtime_binding import (
    InstitutionalRuntimeBindingError,
)


class InstitutionalInstructionExecutionError(ValueError):
    """An institutional instruction cannot safely enter governed execution."""


class InstitutionalInstructionExecutionService:
    """Resolve, pin and execute one instruction under an institution's binding."""

    def __init__(self, *, runtime_binding_resolver: Any,
                 pipeline_factory: Callable[[dict[str, Any]], Any]):
        if not callable(getattr(runtime_binding_resolver, "resolve", None)):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_RUNTIME_BINDING_RESOLVER_REQUIRED")
        if not callable(pipeline_factory):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_PIPELINE_FACTORY_REQUIRED")
        self.runtime_binding_resolver = runtime_binding_resolver
        self.pipeline_factory = pipeline_factory

    @staticmethod
    def _pins(context: dict[str, Any]) -> dict[str, str]:
        fields = (
            "tenant_id", "institution_id", "institution_version", "binding_id",
            "binding_version", "jurisdiction_ref", "jurisdiction_package_id",
            "jurisdiction_package_version", "minah_deployment_id",
            "minah_domain_version", "minah_runtime_version",
            "policy_pack_version", "source_pack_version",
            "authority_profile_version",
        )
        pins: dict[str, str] = {}
        for field in fields:
            value = context.get(field)
            if not isinstance(value, str) or not value.strip():
                raise InstitutionalInstructionExecutionError(
                    "INSTITUTIONAL_RUNTIME_PIN_MISSING:" + field)
            pins[field] = value
        if context.get("runtime_admission") != "ELIGIBLE_FOR_GOVERNED_EXECUTION":
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_RUNTIME_NOT_ADMISSIBLE")
        return pins

    @staticmethod
    def _assert_pipeline_profile(pipeline: Any, context: dict[str, Any]) -> None:
        """When using the reference pipeline, ensure its MINAH profile matches the binding."""
        profile = getattr(pipeline, "minah_profile", None)
        if profile is None:
            return
        config = context.get("minah_config")
        if not isinstance(config, dict):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_MINAH_CONFIGURATION_MISSING")
        checks = {
            "deployment_id": context["minah_deployment_id"],
            "owner_ref": context["tenant_id"],
            "model_ref": config.get("model_ref"),
            "policy_pack_version": context["policy_pack_version"],
            "source_pack_version": context["source_pack_version"],
        }
        for field, expected in checks.items():
            if not isinstance(expected, str) or profile.get(field) != expected:
                raise InstitutionalInstructionExecutionError(
                    "INSTITUTIONAL_PIPELINE_PROFILE_MISMATCH:" + field)
        jurisdictions = profile.get("jurisdictions")
        if not isinstance(jurisdictions, list) or context["jurisdiction_ref"] not in jurisdictions:
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_PIPELINE_PROFILE_MISMATCH:jurisdictions")

    def execute(self, *, tenant_id: str, institution_id: str, binding_id: str,
                instruction: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
        if not all(isinstance(value, str) and value.strip()
                   for value in (tenant_id, institution_id, binding_id)):
            raise InstitutionalInstructionExecutionError(
                "VERIFIED_TENANT_INSTITUTION_AND_BINDING_REQUIRED")
        if not isinstance(evidence, dict):
            raise InstitutionalInstructionExecutionError("EXECUTION_EVIDENCE_MUST_BE_OBJECT")
        checked = verify_instruction_payload_digest(instruction)
        if not isinstance(checked.get("evidence"), dict) or checked["evidence"] != evidence:
            raise InstitutionalInstructionExecutionError(
                "EXECUTION_EVIDENCE_MUST_MATCH_DIGEST_BOUND_INSTRUCTION")
        try:
            context = self.runtime_binding_resolver.resolve(
                tenant_id=tenant_id, institution_id=institution_id, binding_id=binding_id)
        except InstitutionalRuntimeBindingError as exc:
            raise InstitutionalInstructionExecutionError(str(exc)) from exc
        pins = self._pins(context)
        if pins["institution_id"] != institution_id or pins["tenant_id"] != tenant_id:
            raise InstitutionalInstructionExecutionError(
                "RESOLVED_RUNTIME_IDENTITY_MISMATCH")
        if checked["jurisdiction"] != pins["jurisdiction_ref"]:
            raise InstitutionalInstructionExecutionError(
                "INSTRUCTION_JURISDICTION_DOES_NOT_MATCH_ACTIVE_BINDING")
        if checked["rule_pack_version"] != pins["policy_pack_version"]:
            raise InstitutionalInstructionExecutionError(
                "INSTRUCTION_POLICY_VERSION_DOES_NOT_MATCH_ACTIVE_BINDING")

        # The complete configuration (including private adapter references) is
        # supplied only to the trusted server-side factory, never returned or
        # copied into the operation's public/audit pin set.
        pipeline = self.pipeline_factory(dict(context))
        if not callable(getattr(pipeline, "execute", None)):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_PIPELINE_EXECUTOR_INVALID")
        self._assert_pipeline_profile(pipeline, context)

        rule_set, rule_version = checked["rule_pack_version"].rsplit(":", 1)
        internal_request = {
            "operation_id": checked["operation_id"],
            "event_id": "EV-" + hashlib.sha256(
                checked["idempotency_key"].encode("utf-8")
            ).hexdigest()[:24],
            "process_id": checked["target_ref"],
            "actor_id": checked["principal_ref"],
            "event_type": checked["action"],
            "jurisdiction": checked["jurisdiction"],
            "rule_set": rule_set,
            "rule_version": rule_version,
            "evidence": evidence,
            "_contract_envelope": checked,
            "_institutional_runtime_binding": pins,
        }
        try:
            result = pipeline.execute(internal_request)
        except InstitutionalInstructionExecutionError:
            raise
        except Exception as exc:
            # Preserve machine-readable protocol conflicts; never leak adapter data.
            reason = str(exc)
            if reason and len(reason) <= 120 and all(c.isupper() or c.isdigit() or c in "_:-" for c in reason):
                raise InstitutionalInstructionExecutionError(reason) from exc
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_GOVERNED_EXECUTION_FAILED") from exc
        if not isinstance(result, dict):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_PIPELINE_RESPONSE_INVALID")
        status = {
            "COMMITTED": "PASSED", "PASSED": "PASSED",
            "REJECTED": "REJECTED", "DEFERRED": "DEFERRED",
            "UNKNOWN": "UNKNOWN",
        }.get(result.get("status"), "UNKNOWN")
        decision = validate_decision_envelope({
            "contract_version": checked["contract_version"],
            "request_id": checked["request_id"],
            "operation_id": checked["operation_id"],
            "protocol_decision": status,
            "reason_code": str(result.get("reason") or "INSTITUTIONAL_GOVERNED_EXECUTION"),
            "user_message": {
                "PASSED": "The institution's configured protocol gates passed. No external effect is implied.",
                "REJECTED": "A required institutional protocol gate rejected the instruction.",
                "DEFERRED": "The institution requires additional information or review.",
                "UNKNOWN": "The institutional execution outcome cannot yet be established safely.",
            }[status],
            "rule_pack_version": checked["rule_pack_version"],
            "minah_disposition_ref": "minah-disposition:" + checked["operation_id"],
            "cee_execution_ref": "cee-operation:" + checked["operation_id"],
            "external_effect_status": "NOT_ATTEMPTED",
            "evidence_refs": checked.get("evidence_refs", []),
        })
        assert_instruction_binding(checked, decision)
        return {
            "decision": decision,
            "result": result,
            "runtime_pins": pins,
            "production_ready": False,
        }
