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
import re
from copy import deepcopy
import threading
from typing import Any, Callable

from governed_contracts import (
    assert_instruction_binding,
    validate_decision_envelope,
    verify_instruction_payload_digest,
)
from institutional_runtime_binding import (
    InstitutionalRuntimeBindingError,
)
from institutional_minah_deployment import MinahDeploymentError
from protocol_pipeline import _safe_minah_profile_snapshot


class InstitutionalInstructionExecutionError(ValueError):
    """An institutional instruction cannot safely enter governed execution."""


class InstitutionalInstructionExecutionService:
    """Resolve, pin and execute one instruction under an institution's binding."""

    def __init__(self, *, runtime_binding_resolver: Any,
                 pipeline_factory: Callable[[dict[str, Any]], Any],
                 operation_pipeline_resolver: Callable[..., Any] | None = None,
                 minah_deployment_resolver: Any | None = None):
        if not callable(getattr(runtime_binding_resolver, "resolve", None)):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_RUNTIME_BINDING_RESOLVER_REQUIRED")
        if not callable(pipeline_factory):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_PIPELINE_FACTORY_REQUIRED")
        if operation_pipeline_resolver is not None and not callable(operation_pipeline_resolver):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_OPERATION_PIPELINE_RESOLVER_INVALID")
        if minah_deployment_resolver is None:
            minah_deployment_resolver = getattr(runtime_binding_resolver, "minah", None)
        if (minah_deployment_resolver is not None
                and not callable(getattr(minah_deployment_resolver, "resolve_active", None))):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_MINAH_DEPLOYMENT_RESOLVER_INVALID")
        self.runtime_binding_resolver = runtime_binding_resolver
        self.pipeline_factory = pipeline_factory
        # Recovery must re-check the live deployment lifecycle, not merely the
        # profile cached in the original executor. Production resolvers are
        # normally inherited from InstitutionalRuntimeBindingResolver.minah.
        self.minah_deployment_resolver = minah_deployment_resolver
        # Optional durable catalog hook. It must return the executor configured
        # for the operation's original persisted pins, never the current binding.
        self.operation_pipeline_resolver = operation_pipeline_resolver
        self._pipeline_lock = threading.RLock()
        self._pipelines: dict[tuple[str, ...], Any] = {}

    def close(self) -> None:
        """Close process-cached institution pipelines when the service shuts down."""
        with self._pipeline_lock:
            pipelines = list({id(value): value for value in self._pipelines.values()}.values())
            self._pipelines.clear()
        for pipeline in pipelines:
            close = getattr(pipeline, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _pins(context: dict[str, Any]) -> dict[str, str]:
        fields = (
            "tenant_id", "institution_id", "institution_version", "binding_id",
            "binding_version", "jurisdiction_ref", "jurisdiction_package_id",
            "jurisdiction_package_version", "minah_deployment_id",
            "identity_provider_id", "identity_provider_version",
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
        """Require the selected executor to match the exact institution runtime binding."""
        bound_fields = {
            "identity_provider_id": context["identity_provider_id"],
            "identity_provider_version": context["identity_provider_version"],
            "jurisdiction_package_id": context["jurisdiction_package_id"],
            "jurisdiction_package_version": context["jurisdiction_package_version"],
            "authority_profile_version": context["authority_profile_version"],
        }
        for field, expected in bound_fields.items():
            if getattr(pipeline, field, None) != expected:
                raise InstitutionalInstructionExecutionError(
                    "INSTITUTIONAL_PIPELINE_BINDING_MISMATCH:" + field)
        profile = getattr(pipeline, "minah_profile", None)
        if not isinstance(profile, dict):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_PIPELINE_MINAH_PROFILE_REQUIRED")
        config = context.get("minah_config")
        if not isinstance(config, dict):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_MINAH_CONFIGURATION_MISSING")

        # Compare the executor's effective MINAH profile with the deployment
        # resolved from the institution registry. A pipeline without an
        # inspectable profile is not admissible for institutional execution.
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

        configured_jurisdictions = config.get("jurisdictions")
        profile_jurisdictions = profile.get("jurisdictions")
        if (not isinstance(configured_jurisdictions, list)
                or not configured_jurisdictions
                or any(not isinstance(item, str) or not item.strip()
                       for item in configured_jurisdictions)
                or len(set(configured_jurisdictions)) != len(configured_jurisdictions)
                or not isinstance(profile_jurisdictions, list)
                or profile_jurisdictions != configured_jurisdictions
                or context["jurisdiction_ref"] not in profile_jurisdictions):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_PIPELINE_PROFILE_MISMATCH:jurisdictions")

        # Where the registered deployment carries additional governance
        # bindings, require the effective MINAH profile to preserve them too.
        for field in (
            "mandate_ref", "mandate_version", "interface_version",
            "authority_profile_version",
        ):
            expected = config.get(field)
            if expected is not None and profile.get(field) != expected:
                raise InstitutionalInstructionExecutionError(
                    "INSTITUTIONAL_PIPELINE_PROFILE_MISMATCH:" + field)
        if profile.get("may_authorise") is not False:
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_PIPELINE_PROFILE_MISMATCH:may_authorise")

    def _assert_minah_deployment_execution_eligible(
            self, tenant_id: str, pins: dict[str, str],
            pipeline_profile: dict[str, Any]) -> int:
        """Recheck and pin the exact MINAH deployment epoch before dispatch."""
        resolver = self.minah_deployment_resolver
        resolve_active = getattr(resolver, "resolve_active", None)
        if not callable(resolve_active):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_MINAH_EXECUTION_LIFECYCLE_RESOLVER_REQUIRED")
        try:
            deployment = resolve_active(
                tenant_id=tenant_id, deployment_id=pins["minah_deployment_id"])
        except Exception as exc:
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_MINAH_EXECUTION_DEPLOYMENT_UNAVAILABLE") from exc
        if not isinstance(deployment, dict) or deployment.get("status") != "ACTIVE":
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_MINAH_EXECUTION_DEPLOYMENT_NOT_ACTIVE")
        if (deployment.get("tenant_id") != tenant_id
                or deployment.get("deployment_id") != pins["minah_deployment_id"]):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_MINAH_EXECUTION_DEPLOYMENT_IDENTITY_MISMATCH")
        if deployment.get("version") != pins["minah_runtime_version"]:
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_MINAH_EXECUTION_DEPLOYMENT_VERSION_MISMATCH")
        config = deployment.get("config")
        if not isinstance(config, dict):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_MINAH_EXECUTION_DEPLOYMENT_CONFIGURATION_INVALID")
        config_fields = (
            "deployment_id", "owner_ref", "model_ref", "jurisdictions",
            "policy_pack_version", "source_pack_version", "mandate_ref",
            "mandate_version", "interface_version", "authority_profile_version",
        )
        for field in config_fields:
            if field in pipeline_profile and config.get(field) != pipeline_profile[field]:
                raise InstitutionalInstructionExecutionError(
                    "INSTITUTIONAL_MINAH_EXECUTION_DEPLOYMENT_CONFIGURATION_MISMATCH:" + field)
        epoch = deployment.get("execution_epoch")
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_MINAH_EXECUTION_EPOCH_UNAVAILABLE")
        return epoch

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
        evidence_refs = checked.get("evidence_refs", [])
        if not isinstance(evidence_refs, list) or any(
            not isinstance(ref, str) or not ref.strip() for ref in evidence_refs
        ):
            raise InstitutionalInstructionExecutionError("INVALID_INSTRUCTION_EVIDENCE_REFERENCES")
        rule_parts = checked["rule_pack_version"].rsplit(":", 1)
        if len(rule_parts) != 2 or not all(part.strip() for part in rule_parts):
            raise InstitutionalInstructionExecutionError("INVALID_INSTRUCTION_RULE_PACK_VERSION")
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
        # Cache identity must include every immutable runtime pin. In particular,
        # an institution record can be revised while its binding ID/version remains
        # unchanged; omitting institution_version would silently reuse an executor
        # constructed from the prior institutional identity/configuration.
        cache_key = tuple(pins[field] for field in sorted(pins))
        with self._pipeline_lock:
            pipeline = self._pipelines.get(cache_key)
            if pipeline is None:
                pipeline = self.pipeline_factory(dict(context))
                if not callable(getattr(pipeline, "execute", None)):
                    raise InstitutionalInstructionExecutionError(
                        "INSTITUTIONAL_PIPELINE_EXECUTOR_INVALID")
                if (getattr(pipeline, "SUPPORTS_INSTITUTIONAL_RUNTIME_PINS", False) is not True
                        or tuple(getattr(pipeline, "EXECUTION_ORDER", ())) !=
                        ("MINAH", "IDENTITY", "AUTHORITY", "CEE", "FINAL_DECISION")):
                    raise InstitutionalInstructionExecutionError(
                        "INSTITUTIONAL_PIPELINE_GOVERNANCE_CONTRACT_UNSUPPORTED")
                self._assert_pipeline_profile(pipeline, context)
                self._pipelines[cache_key] = pipeline

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
            # Preserve the exact effective MINAH profile used at admission.
            # Recovery must not silently continue under a substituted profile.
            "_minah_deployment_profile": _safe_minah_profile_snapshot(
                pipeline.minah_profile
            ),
        }
        # Resolve lifecycle state, then atomically acquire a lease for the exact
        # active deployment epoch. The pipeline revalidates this fencing token
        # immediately before its IVM write; lifecycle transitions increment the
        # epoch and invalidate every outstanding lease.
        expected_execution_epoch = self._assert_minah_deployment_execution_eligible(
            tenant_id, pins, internal_request["_minah_deployment_profile"])
        resolver = self.minah_deployment_resolver
        acquire_lease = getattr(resolver, "acquire_execution_lease", None)
        release_lease = getattr(resolver, "release_execution_lease", None)
        execute_under_lease = getattr(resolver, "execute_under_execution_lease", None)
        if not all(callable(method) for method in
                   (acquire_lease, release_lease, execute_under_lease)):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_MINAH_EXECUTION_LEASE_COORDINATOR_REQUIRED")

        def execution_guard(action: Callable[[], Any]) -> Any:
            # Acquire only at the protected commit boundary, after MINAH,
            # identity and authority work, so model latency cannot consume TTL.
            try:
                lease = acquire_lease(
                    tenant_id=tenant_id, deployment_id=pins["minah_deployment_id"])
            except Exception as exc:
                raise InstitutionalInstructionExecutionError(
                    "INSTITUTIONAL_MINAH_EXECUTION_LEASE_UNAVAILABLE") from exc
            if (not isinstance(lease, dict)
                    or lease.get("version") != pins["minah_runtime_version"]
                    or lease.get("execution_epoch") != expected_execution_epoch
                    or isinstance(lease.get("fencing_token"), bool)
                    or not isinstance(lease.get("fencing_token"), int)
                    or lease.get("fencing_token") < 1):
                try:
                    release_lease(lease=lease)
                except Exception:
                    pass
                raise InstitutionalInstructionExecutionError(
                    "INSTITUTIONAL_MINAH_EXECUTION_LEASE_EPOCH_OR_VERSION_MISMATCH")
            try:
                # Carry the durable monotonic fence to the downstream IVM
                # commit adapter; the adapter persists/rejects stale tokens.
                setattr(action, "_kin_sys_execution_fence", {
                    "tenant_id": tenant_id,
                    "deployment_id": pins["minah_deployment_id"],
                    "execution_epoch": expected_execution_epoch,
                    "fencing_token": lease["fencing_token"],
                })
                return execute_under_lease(lease=lease, action=action)
            except MinahDeploymentError as exc:
                raise InstitutionalInstructionExecutionError(
                    "INSTITUTIONAL_MINAH_EXECUTION_LEASE_FENCED") from exc
            finally:
                try:
                    release_lease(lease=lease)
                except Exception:
                    pass

        try:
            result = pipeline.execute(internal_request, execution_guard=execution_guard)
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
        if result.get("operation_id") != checked["operation_id"]:
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_PIPELINE_OPERATION_ID_MISMATCH")
        return self._decision_response(checked, result, pins)


    @staticmethod
    def _decision_response(checked: dict[str, Any], result: dict[str, Any],
                           pins: dict[str, str]) -> dict[str, Any]:
        status = {
            "COMMITTED": "PASSED", "PASSED": "PASSED",
            "REJECTED": "REJECTED", "DEFERRED": "DEFERRED",
            "UNKNOWN": "UNKNOWN",
        }.get(result.get("status"), "UNKNOWN")
        raw_reason = result.get("reason")
        reason_code = (
            raw_reason if isinstance(raw_reason, str)
            and re.fullmatch(r"[A-Z0-9_:-]{1,120}", raw_reason)
            else "INSTITUTIONAL_GOVERNED_EXECUTION"
        )
        decision = validate_decision_envelope({
            "contract_version": checked["contract_version"],
            "request_id": checked["request_id"],
            "operation_id": checked["operation_id"],
            "protocol_decision": status,
            "reason_code": reason_code,
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
        safe_result = {
            key: result[key] for key in (
                "operation_id", "status", "reason", "funds_moved",
                "ivm_decision", "duplicate",
            ) if key in result
        }
        return {
            "decision": decision,
            "result": safe_result,
            "runtime_pins": dict(pins),
            "production_ready": False,
        }

    @staticmethod
    def _validate_stored_pins(operation: dict[str, Any], tenant_id: str,
                              operation_id: str) -> tuple[dict[str, str], dict[str, Any]]:
        request = operation.get("request_json")
        if not isinstance(request, dict) or request.get("operation_id") != operation_id:
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_OPERATION_JOURNAL_INVALID")
        pins = request.get("_institutional_runtime_binding")
        required = (
            "tenant_id", "institution_id", "institution_version", "binding_id",
            "binding_version", "jurisdiction_ref", "jurisdiction_package_id",
            "jurisdiction_package_version", "minah_deployment_id",
            "identity_provider_id", "identity_provider_version",
            "minah_domain_version", "minah_runtime_version",
            "policy_pack_version", "source_pack_version", "authority_profile_version",
        )
        if not isinstance(pins, dict) or any(
            not isinstance(pins.get(field), str) or not pins[field].strip()
            for field in required
        ):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_OPERATION_RUNTIME_PINS_INVALID")
        if pins["tenant_id"] != tenant_id:
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_OPERATION_TENANT_MISMATCH")
        checked = verify_instruction_payload_digest(request.get("_contract_envelope"))
        rule_version = request.get("rule_version")
        rule_set = request.get("rule_set")
        if (checked["operation_id"] != operation_id
                or checked["jurisdiction"] != pins["jurisdiction_ref"]
                or checked["rule_pack_version"] != pins["policy_pack_version"]
                or request.get("jurisdiction") != checked["jurisdiction"]
                or not isinstance(rule_set, str) or not isinstance(rule_version, str)
                or rule_set + ":" + rule_version != checked["rule_pack_version"]
                or request.get("_institutional_runtime_binding") != pins):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_OPERATION_PINNED_REQUEST_MISMATCH")
        return ({field: pins[field] for field in required}, request)

    @staticmethod
    def _assert_original_pipeline(pipeline: Any, request: dict[str, Any],
                                  pins: dict[str, str]) -> None:
        if (getattr(pipeline, "SUPPORTS_INSTITUTIONAL_RUNTIME_PINS", False) is not True
                or tuple(getattr(pipeline, "EXECUTION_ORDER", ())) !=
                ("MINAH", "IDENTITY", "AUTHORITY", "CEE", "FINAL_DECISION")):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_PIPELINE_GOVERNANCE_CONTRACT_UNSUPPORTED")
        fields = {
            "identity_provider_id": pins["identity_provider_id"],
            "identity_provider_version": pins["identity_provider_version"],
            "jurisdiction_package_id": pins["jurisdiction_package_id"],
            "jurisdiction_package_version": pins["jurisdiction_package_version"],
            "authority_profile_version": pins["authority_profile_version"],
        }
        for field, expected in fields.items():
            if getattr(pipeline, field, None) != expected:
                raise InstitutionalInstructionExecutionError(
                    "INSTITUTIONAL_PIPELINE_BINDING_MISMATCH:" + field)
        stored_profile = request.get("_minah_deployment_profile")
        active_profile = getattr(pipeline, "minah_profile", None)
        if not isinstance(stored_profile, dict) or not isinstance(active_profile, dict):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_ORIGINAL_MINAH_PROFILE_UNAVAILABLE")
        try:
            active_snapshot = _safe_minah_profile_snapshot(active_profile)
        except (TypeError, ValueError):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_ORIGINAL_MINAH_PROFILE_UNAVAILABLE") from None
        # Recovery is permitted only when the executor presents the exact safe
        # deployment profile admitted for this operation. Missing snapshots are
        # not treated as legacy-compatible: doing so would permit recovery under
        # an unpinned or substituted MINAH configuration.
        if active_snapshot != stored_profile:
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_ORIGINAL_MINAH_PROFILE_MISMATCH")
        profile_pins = {
            "deployment_id": pins["minah_deployment_id"],
            "owner_ref": pins["tenant_id"],
            "policy_pack_version": pins["policy_pack_version"],
            "source_pack_version": pins["source_pack_version"],
        }
        for field, expected in profile_pins.items():
            if stored_profile.get(field) != expected:
                raise InstitutionalInstructionExecutionError(
                    "INSTITUTIONAL_ORIGINAL_MINAH_PROFILE_MISMATCH:" + field)
        jurisdictions = stored_profile.get("jurisdictions")
        if (not isinstance(jurisdictions, list) or not jurisdictions
                or any(not isinstance(item, str) or not item.strip()
                       for item in jurisdictions)
                or len(set(jurisdictions)) != len(jurisdictions)
                or pins["jurisdiction_ref"] not in jurisdictions):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_ORIGINAL_MINAH_PROFILE_MISMATCH:jurisdictions")
        if stored_profile.get("may_authorise") is not False:
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_ORIGINAL_MINAH_PROFILE_MISMATCH:may_authorise")

    def _find_pinned_pipeline(self, tenant_id: str, operation_id: str,
                              institution_id: str | None = None):
        candidates: list[Any] = []
        with self._pipeline_lock:
            candidates.extend({id(value): value for value in self._pipelines.values()}.values())
        matches: list[tuple[Any, dict[str, Any]]] = []
        for pipeline in candidates:
            journal = getattr(pipeline, "cee", None)
            getter = getattr(journal, "get", None)
            if not callable(getter):
                continue
            try:
                operation = getter(operation_id)
            except Exception:
                continue
            if not isinstance(operation, dict):
                continue
            request = operation.get("request_json")
            pins = request.get("_institutional_runtime_binding") if isinstance(request, dict) else None
            if (isinstance(pins, dict) and pins.get("tenant_id") == tenant_id
                    and (institution_id is None or pins.get("institution_id") == institution_id)):
                matches.append((pipeline, operation))
        if not matches and self.operation_pipeline_resolver is not None:
            try:
                pipeline = self.operation_pipeline_resolver(
                    tenant_id=tenant_id, operation_id=operation_id)
                journal = getattr(pipeline, "cee", None)
                getter = getattr(journal, "get", None)
                operation = getter(operation_id) if callable(getter) else None
                if isinstance(operation, dict):
                    request = operation.get("request_json")
                    pins = request.get("_institutional_runtime_binding") if isinstance(request, dict) else None
                    if (isinstance(pins, dict) and pins.get("tenant_id") == tenant_id
                            and (institution_id is None or pins.get("institution_id") == institution_id)):
                        matches.append((pipeline, operation))
            except Exception as exc:
                raise InstitutionalInstructionExecutionError(
                    "INSTITUTIONAL_ORIGINAL_PINNED_RUNTIME_UNAVAILABLE") from exc
        if len(matches) > 1:
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_OPERATION_ID_AMBIGUOUS")
        if not matches:
            return None
        pipeline, operation = matches[0]
        pins, request = self._validate_stored_pins(operation, tenant_id, operation_id)
        try:
            integrity_check = getattr(pipeline, "_assert_store_integrity", None)
            if callable(integrity_check):
                integrity_check()
            cross_store_check = getattr(pipeline, "_assert_cross_store_consistent", None)
            if callable(cross_store_check):
                cross_store_check(operation)
        except Exception as exc:
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_OPERATION_JOURNAL_INTEGRITY_FAILURE") from exc
        self._assert_original_pipeline(pipeline, request, pins)
        return pipeline, operation, pins, request

    def _assert_minah_deployment_recovery_eligible(
            self, tenant_id: str, pins: dict[str, str],
            stored_profile: dict[str, Any]) -> int:
        resolver = self.minah_deployment_resolver
        resolve_active = getattr(resolver, "resolve_active", None)
        if not callable(resolve_active):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_MINAH_LIFECYCLE_RESOLVER_REQUIRED")
        try:
            deployment = resolve_active(
                tenant_id=tenant_id, deployment_id=pins["minah_deployment_id"])
        except Exception as exc:
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_MINAH_DEPLOYMENT_UNAVAILABLE") from exc
        if not isinstance(deployment, dict) or deployment.get("status") != "ACTIVE":
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_MINAH_DEPLOYMENT_NOT_ACTIVE")
        if deployment.get("tenant_id") != tenant_id or deployment.get(
                "deployment_id") != pins["minah_deployment_id"]:
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_MINAH_DEPLOYMENT_IDENTITY_MISMATCH")
        if deployment.get("version") != pins["minah_runtime_version"]:
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_MINAH_DEPLOYMENT_VERSION_MISMATCH")
        config = deployment.get("config")
        if not isinstance(config, dict):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_MINAH_DEPLOYMENT_CONFIGURATION_INVALID")
        config_fields = (
            "deployment_id", "owner_ref", "model_ref", "jurisdictions",
            "policy_pack_version", "source_pack_version", "mandate_ref",
            "mandate_version", "interface_version", "authority_profile_version",
        )
        for field in config_fields:
            if field in stored_profile and config.get(field) != stored_profile[field]:
                raise InstitutionalInstructionExecutionError(
                    "INSTITUTIONAL_MINAH_DEPLOYMENT_CONFIGURATION_MISMATCH:" + field)
        epoch = deployment.get("execution_epoch")
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_MINAH_EXECUTION_EPOCH_UNAVAILABLE")
        return epoch

    def inspect(self, *, tenant_id: str, operation_id: str,
                institution_id: str | None = None) -> dict[str, Any] | None:
        """Inspect an operation only through the journal carrying its original pins."""
        if not all(isinstance(value, str) and value.strip()
                   for value in (tenant_id, operation_id)):
            raise InstitutionalInstructionExecutionError(
                "VERIFIED_TENANT_AND_OPERATION_REQUIRED")
        found = self._find_pinned_pipeline(tenant_id, operation_id, institution_id)
        if found is None:
            return None
        pipeline, operation, pins, _request = found
        inspector = getattr(pipeline, "inspect", None)
        if not callable(inspector):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_OPERATION_INSPECTOR_UNAVAILABLE")
        snapshot = inspector(operation_id)
        if not isinstance(snapshot, dict):
            return None
        journal = pipeline.cee
        events = journal.events(operation_id)
        observations = journal.observations(operation_id)
        stored_result = operation.get("result_json")
        result_summary = {}
        if isinstance(stored_result, dict):
            result_summary = {
                key: stored_result[key] for key in ("status", "reason")
                if key in stored_result
            }
        safe_observations = []
        for item in observations:
            detail = item.get("detail", {})
            safe_detail = {
                key: detail[key] for key in (
                    "actor_ref", "actor_binding", "request_id", "reason_code",
                ) if isinstance(detail, dict) and key in detail
            }
            safe_observations.append({
                "kind": item.get("kind"), "status": item.get("status"),
                "created_at": item.get("created_at"), "detail": safe_detail,
            })
        return {
            "operation_id": operation_id,
            "phase": operation.get("phase"),
            "status": result_summary.get("status", operation.get("phase")),
            "request_fingerprint": operation.get("request_digest"),
            "runtime_pins": pins,
            "resumable": bool(snapshot.get("resumable")),
            "recovery_required": bool(snapshot.get("resumable")),
            "result": result_summary,
            "cee_event_summary": [
                {"phase": event.get("phase"), "status": event.get("status")}
                for event in events
            ],
            "observations": safe_observations,
            "production_ready": False,
        }

    def recover(self, *, tenant_id: str, operation_id: str,
                institution_id: str | None = None, actor_ref: str | None = None,
                request_id: str | None = None) -> dict[str, Any]:
        """Recover only through the original pipeline and immutable operation pins."""
        if not all(isinstance(value, str) and value.strip()
                   for value in (tenant_id, operation_id)):
            raise InstitutionalInstructionExecutionError(
                "VERIFIED_TENANT_AND_OPERATION_REQUIRED")
        found = self._find_pinned_pipeline(tenant_id, operation_id, institution_id)
        if found is None:
            return {
                "operation_id": operation_id, "status": "UNKNOWN",
                "reason": "ORIGINAL_PINNED_RUNTIME_UNAVAILABLE",
                "recovery_required": True, "production_ready": False,
            }
        pipeline, operation, pins, request = found
        journal = pipeline.cee
        recovery_method = getattr(pipeline, "recover", None)
        record_observation = getattr(journal, "record_observation", None)
        if not callable(recovery_method) or not callable(record_observation):
            raise InstitutionalInstructionExecutionError(
                "INSTITUTIONAL_OPERATION_RECOVERY_UNAVAILABLE")
        detail: dict[str, str] = {}
        if isinstance(actor_ref, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,159}", actor_ref):
            detail["actor_ref"] = actor_ref
        else:
            detail["actor_binding"] = "VERIFIED_INSTITUTIONAL_SESSION"
        if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", request_id):
            detail["request_id"] = request_id
        record_observation(
            operation_id, "INSTITUTIONAL_RECOVERY", "REQUESTED", detail)
        try:
            stored_profile = request.get("_minah_deployment_profile")
            if not isinstance(stored_profile, dict):
                raise InstitutionalInstructionExecutionError(
                    "INSTITUTIONAL_ORIGINAL_MINAH_PROFILE_UNAVAILABLE")
            expected_execution_epoch = self._assert_minah_deployment_recovery_eligible(
                tenant_id, pins, stored_profile)
            resolver = self.minah_deployment_resolver
            acquire_lease = getattr(resolver, "acquire_execution_lease", None)
            release_lease = getattr(resolver, "release_execution_lease", None)
            execute_under_lease = getattr(resolver, "execute_under_execution_lease", None)
            if not all(callable(method) for method in
                       (acquire_lease, release_lease, execute_under_lease)):
                raise InstitutionalInstructionExecutionError(
                    "INSTITUTIONAL_MINAH_EXECUTION_LEASE_COORDINATOR_REQUIRED")
            def recovery_execution_guard(action: Callable[[], Any]) -> Any:
                lease = acquire_lease(
                    tenant_id=tenant_id, deployment_id=pins["minah_deployment_id"])
                if (not isinstance(lease, dict)
                        or lease.get("version") != pins["minah_runtime_version"]
                        or lease.get("execution_epoch") != expected_execution_epoch
                        or isinstance(lease.get("fencing_token"), bool)
                        or not isinstance(lease.get("fencing_token"), int)
                        or lease.get("fencing_token") < 1):
                    try:
                        release_lease(lease=lease)
                    except Exception:
                        pass
                    raise InstitutionalInstructionExecutionError(
                        "INSTITUTIONAL_MINAH_EXECUTION_LEASE_EPOCH_OR_VERSION_MISMATCH")
                try:
                    setattr(action, "_kin_sys_execution_fence", {
                        "tenant_id": tenant_id,
                        "deployment_id": pins["minah_deployment_id"],
                        "execution_epoch": expected_execution_epoch,
                        "fencing_token": lease["fencing_token"],
                    })
                    return execute_under_lease(lease=lease, action=action)
                except MinahDeploymentError as exc:
                    raise InstitutionalInstructionExecutionError(
                        "INSTITUTIONAL_MINAH_EXECUTION_LEASE_FENCED") from exc
                finally:
                    # Lease release is cleanup, not the recovery outcome. A
                    # coordinator outage during release must not mask a
                    # successfully reconciled durable operation.
                    try:
                        release_lease(lease=lease)
                    except Exception:
                        pass
            recovered = recovery_method(
                operation_id, execution_guard=recovery_execution_guard)
            if not isinstance(recovered, dict):
                raise InstitutionalInstructionExecutionError(
                    "INSTITUTIONAL_RECOVERY_RESPONSE_INVALID")
            if recovered.get("operation_id") != operation_id:
                raise InstitutionalInstructionExecutionError(
                    "INSTITUTIONAL_RECOVERY_OPERATION_ID_MISMATCH")
            recovered_status = recovered.get("status", "UNKNOWN")
            reason = recovered.get("reason")
            completed_detail = dict(detail)
            if isinstance(reason, str) and re.fullmatch(r"[A-Z0-9_:-]{1,120}", reason):
                completed_detail["reason_code"] = reason
            record_observation(
                operation_id, "INSTITUTIONAL_RECOVERY", str(recovered_status), completed_detail)
            checked = verify_instruction_payload_digest(request.get("_contract_envelope"))
            return self._decision_response(checked, recovered, pins)
        except Exception as exc:
            blocked_detail = dict(detail)
            reason = str(exc)
            blocked_detail["reason_code"] = (
                reason if re.fullmatch(r"[A-Z0-9_:-]{1,120}", reason)
                else "INSTITUTIONAL_RECOVERY_FAILED"
            )
            try:
                record_observation(
                    operation_id, "INSTITUTIONAL_RECOVERY", "BLOCKED", blocked_detail)
            except Exception:
                pass
            if isinstance(exc, InstitutionalInstructionExecutionError):
                raise
            raise InstitutionalInstructionExecutionError(
                blocked_detail["reason_code"]) from exc
