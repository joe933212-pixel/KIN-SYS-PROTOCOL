# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import hashlib
import unittest

from governed_contracts import CONTRACT_VERSION, compute_payload_digest
from institutional_instruction_execution import (
    InstitutionalInstructionExecutionError,
    InstitutionalInstructionExecutionService,
)
from protocol_pipeline import KinSysProtocolPipeline


class RuntimeResolver:
    def __init__(self, context=None):
        self.context = context or {
            "tenant_id": "tenant-ug",
            "institution_id": "BANK-UG-01",
            "institution_version": "2.0",
            "binding_id": "BIND-UG-01",
            "binding_version": "3.0",
            "jurisdiction_ref": "UG",
            "jurisdiction_package_id": "JUR-UG-01",
            "jurisdiction_package_version": "4.0",
            "minah_deployment_id": "MINAH-UG-01",
            "identity_provider_id": "IDP-UG-01",
            "identity_provider_version": "2.0",
            "minah_domain_version": "5.0",
            "minah_runtime_version": "9.0",
            "policy_pack_version": "BANK-RULES:1.0",
            "source_pack_version": "UG-SOURCES:2.0",
            "authority_profile_version": "BANK-AUTHORITY:1.0",
            "runtime_admission": "ELIGIBLE_FOR_GOVERNED_EXECUTION",
            "minah_config": {
                "deployment_id": "MINAH-UG-01",
                "owner_ref": "tenant-ug",
                "model_ref": "model:bank/minah-v1",
                "jurisdictions": ["UG"],
                "policy_pack_version": "BANK-RULES:1.0",
                "source_pack_version": "UG-SOURCES:2.0",
                "endpoint_ref": "secret://private-endpoint",
            },
        }
        self.calls = []
        self.minah = self
        self.minah_status = "ACTIVE"
        self.execution_epoch = 1
        self._leases = {}

    def acquire_execution_lease(self, *, tenant_id, deployment_id):
        if self.minah_status != "ACTIVE":
            raise RuntimeError("MINAH_DEPLOYMENT_NOT_ACTIVE")
        lease = {"lease_id": "test-lease", "lease_token": "test-token",
                 "tenant_id": tenant_id, "deployment_id": deployment_id,
                 "execution_epoch": self.execution_epoch,
                 "version": self.context.get("minah_runtime_version")}
        self._leases[lease["lease_id"]] = lease
        return dict(lease)

    def validate_execution_lease(self, *, lease):
        if (self.minah_status != "ACTIVE"
                or lease.get("execution_epoch") != self.execution_epoch
                or self._leases.get(lease.get("lease_id"), {}).get("lease_token") != lease.get("lease_token")):
            raise RuntimeError("MINAH_EXECUTION_LEASE_FENCED")
        return {"status": "VALID", "execution_epoch": self.execution_epoch}

    def release_execution_lease(self, *, lease):
        self._leases.pop(lease.get("lease_id"), None)

    def resolve_active(self, *, tenant_id, deployment_id):
        if (tenant_id != self.context.get("tenant_id")
                or deployment_id != self.context.get("minah_deployment_id")
                or self.minah_status != "ACTIVE"):
            return None
        return {
            "tenant_id": tenant_id,
            "deployment_id": deployment_id,
            "version": self.context.get("minah_runtime_version"),
            "status": self.minah_status,
            "config": dict(getattr(
                self, "live_minah_config", self.context.get("minah_config", {})
            )),
        }

    def resolve(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.context)


class ActiveMinahResolver:
    def __init__(self, deployment):
        self.deployment = dict(deployment)
        self.execution_epoch = 1
        self._leases = {}

    def acquire_execution_lease(self, *, tenant_id, deployment_id):
        if self.deployment.get("status") != "ACTIVE":
            raise RuntimeError("MINAH_DEPLOYMENT_NOT_ACTIVE")
        lease = {"lease_id": "active-test-lease", "lease_token": "active-test-token",
                 "tenant_id": tenant_id, "deployment_id": deployment_id,
                 "execution_epoch": self.execution_epoch,
                 "version": self.deployment.get("version")}
        self._leases[lease["lease_id"]] = dict(lease)
        return lease

    def validate_execution_lease(self, *, lease):
        if (self.deployment.get("status") != "ACTIVE"
                or lease.get("execution_epoch") != self.execution_epoch
                or lease.get("lease_id") not in self._leases):
            raise RuntimeError("MINAH_EXECUTION_LEASE_FENCED")
        return {"status": "VALID", "execution_epoch": self.execution_epoch}

    def release_execution_lease(self, *, lease):
        self._leases.pop(lease.get("lease_id"), None)

    def resolve_active(self, *, tenant_id, deployment_id):
        current = self.deployment
        if (current.get("tenant_id") != tenant_id
                or current.get("deployment_id") != deployment_id
                or current.get("status") != "ACTIVE"):
            return None
        return dict(current)


class Pipeline:
    EXECUTION_ORDER = ("MINAH", "IDENTITY", "AUTHORITY", "CEE", "FINAL_DECISION")
    SUPPORTS_INSTITUTIONAL_RUNTIME_PINS = True
    identity_provider_id = "IDP-UG-01"
    identity_provider_version = "2.0"
    jurisdiction_package_id = "JUR-UG-01"
    jurisdiction_package_version = "4.0"
    authority_profile_version = "BANK-AUTHORITY:1.0"
    minah_profile = {
        "profile_version": "kin-sys-minah-profile-0.1",
        "deployment_id": "MINAH-UG-01",
        "owner_ref": "tenant-ug",
        "model_ref": "model:bank/minah-v1",
        "jurisdictions": ["UG"],
        "policy_pack_version": "BANK-RULES:1.0",
        "source_pack_version": "UG-SOURCES:2.0",
        "may_authorise": False,
    }

    def __init__(self):
        self.requests = []
        self.before_execution_guard = None
        self.committed = False

    def execute(self, request, *, execution_guard=None):
        self.requests.append(dict(request))
        if callable(self.before_execution_guard):
            self.before_execution_guard()
        if execution_guard is not None:
            execution_guard()
        self.committed = True
        return {
            "operation_id": request["operation_id"],
            "status": "COMMITTED",
            "reason": "SYNTHETIC_GATES_PASSED",
            "funds_moved": False,
            "ivm_decision": {"status": "ACCEPTED"},
            "private_token": "must-not-leak",
        }


def instruction():
    value = {
        "contract_version": CONTRACT_VERSION,
        "request_id": "REQ-1001",
        "operation_id": "OP-1001",
        "idempotency_key": "idempotency-1001",
        "principal_ref": "actor:alice",
        "authority_ref": "authority:bank-ug",
        "action": "ACTIVATE",
        "target_ref": "PROCESS-1001",
        "purpose": "Institutional transaction validation",
        "jurisdiction": "UG",
        "submitted_at": "2026-11-07T10:00:00+00:00",
        "rule_pack_version": "BANK-RULES:1.0",
        "evidence_refs": ["evidence:case-1001"],
        "evidence": {"document_ref": "evidence:case-1001"},
    }
    value["payload_digest"] = compute_payload_digest(value)
    return value


class InstitutionalInstructionExecutionTests(unittest.TestCase):
    def setUp(self):
        self.resolver = RuntimeResolver()
        self.pipeline = Pipeline()
        self.service = InstitutionalInstructionExecutionService(
            runtime_binding_resolver=self.resolver,
            pipeline_factory=lambda context: self.pipeline,
        )

    def test_resolves_binding_pins_exact_versions_and_executes_pipeline(self):
        result = self.service.execute(
            tenant_id="tenant-ug",
            institution_id="BANK-UG-01",
            binding_id="BIND-UG-01",
            instruction=instruction(),
            evidence={"document_ref": "evidence:case-1001"},
        )
        self.assertEqual(self.resolver.calls, [{
            "tenant_id": "tenant-ug",
            "institution_id": "BANK-UG-01",
            "binding_id": "BIND-UG-01",
        }])
        self.assertEqual(result["decision"]["protocol_decision"], "PASSED")
        self.assertNotIn("private_token", result["result"])
        self.assertEqual(result["runtime_pins"]["binding_version"], "3.0")
        self.assertEqual(result["runtime_pins"]["minah_runtime_version"], "9.0")
        pinned = self.pipeline.requests[0]["_institutional_runtime_binding"]
        self.assertEqual(pinned, result["runtime_pins"])
        self.assertEqual(self.pipeline.requests[0]["jurisdiction"], "UG")
        self.assertNotIn("minah_config", pinned)
        self.assertNotIn("endpoint_ref", str(pinned))

    def test_fencing_epoch_change_after_dispatch_blocks_before_commit(self):
        # Simulate a suspension/revocation racing after lease acquisition but
        # before the protected IVM commit boundary.
        self.pipeline.before_execution_guard = lambda: setattr(
            self.resolver, "execution_epoch", self.resolver.execution_epoch + 1)
        with self.assertRaisesRegex(
            InstitutionalInstructionExecutionError,
            "INSTITUTIONAL_MINAH_EXECUTION_LEASE_FENCED",
        ):
            self.service.execute(
                tenant_id="tenant-ug", institution_id="BANK-UG-01",
                binding_id="BIND-UG-01", instruction=instruction(),
                evidence={"document_ref": "evidence:case-1001"},
            )
        self.assertFalse(self.pipeline.committed)
        self.assertEqual(len(self.resolver._leases), 0)

    def test_new_instruction_fails_closed_without_minah_lifecycle_resolver(self):
        class ResolverWithoutLifecycle:
            def __init__(self, delegate):
                self.delegate = delegate

            def resolve(self, **kwargs):
                return self.delegate.resolve(**kwargs)

        pipeline = Pipeline()
        service = InstitutionalInstructionExecutionService(
            runtime_binding_resolver=ResolverWithoutLifecycle(RuntimeResolver()),
            pipeline_factory=lambda context: pipeline,
        )
        with self.assertRaisesRegex(
            InstitutionalInstructionExecutionError,
            "INSTITUTIONAL_MINAH_EXECUTION_LIFECYCLE_RESOLVER_REQUIRED",
        ):
            service.execute(
                tenant_id="tenant-ug", institution_id="BANK-UG-01",
                binding_id="BIND-UG-01", instruction=instruction(),
                evidence=instruction()["evidence"],
            )
        self.assertEqual(pipeline.requests, [])

    def test_rechecks_minah_lifecycle_immediately_before_execution_dispatch(self):
        scenarios = (
            ("SUSPENDED", None, None,
             "INSTITUTIONAL_MINAH_EXECUTION_DEPLOYMENT_NOT_ACTIVE"),
            ("REVOKED", None, None,
             "INSTITUTIONAL_MINAH_EXECUTION_DEPLOYMENT_NOT_ACTIVE"),
            ("ACTIVE", "10.0", None,
             "INSTITUTIONAL_MINAH_EXECUTION_DEPLOYMENT_VERSION_MISMATCH"),
            ("ACTIVE", None, "replacement-model",
             "INSTITUTIONAL_MINAH_EXECUTION_DEPLOYMENT_CONFIGURATION_MISMATCH:model_ref"),
        )
        for status, version, model_ref, expected_error in scenarios:
            with self.subTest(status=status, version=version, model_ref=model_ref):
                resolver = RuntimeResolver()
                pipeline = Pipeline()

                def build_pipeline(_context):
                    resolver.minah_status = status
                    if version is not None:
                        resolver.context["minah_runtime_version"] = version
                    if model_ref is not None:
                        # Keep the admission context and pipeline profile pinned;
                        # mutate only the independently resolved live deployment.
                        resolver.live_minah_config = dict(
                            resolver.context["minah_config"]
                        )
                        resolver.live_minah_config["model_ref"] = model_ref
                    return pipeline

                service = InstitutionalInstructionExecutionService(
                    runtime_binding_resolver=resolver,
                    pipeline_factory=build_pipeline,
                )
                with self.assertRaisesRegex(
                    InstitutionalInstructionExecutionError, expected_error
                ):
                    service.execute(
                        tenant_id="tenant-ug", institution_id="BANK-UG-01",
                        binding_id="BIND-UG-01", instruction=instruction(),
                        evidence=instruction()["evidence"],
                    )
                self.assertEqual(pipeline.requests, [])

    def test_rejects_pipeline_without_inspectable_minah_profile(self):
        class UnprofiledPipeline(Pipeline):
            minah_profile = None

        service = InstitutionalInstructionExecutionService(
            runtime_binding_resolver=self.resolver,
            pipeline_factory=lambda context: UnprofiledPipeline(),
        )
        with self.assertRaisesRegex(
            InstitutionalInstructionExecutionError,
            "INSTITUTIONAL_PIPELINE_MINAH_PROFILE_REQUIRED",
        ):
            service.execute(
                tenant_id="tenant-ug", institution_id="BANK-UG-01",
                binding_id="BIND-UG-01", instruction=instruction(),
                evidence=instruction()["evidence"],
            )

    def test_rejects_minah_profile_from_another_deployment_or_jurisdiction(self):
        for field, value in (
            ("deployment_id", "MINAH-OTHER-01"),
            ("owner_ref", "tenant-other"),
            ("jurisdictions", ["KE"]),
            ("model_ref", "model:other/minah"),
            ("may_authorise", True),
        ):
            with self.subTest(field=field):
                class MismatchedPipeline(Pipeline):
                    pass
                MismatchedPipeline.minah_profile = dict(Pipeline.minah_profile)
                MismatchedPipeline.minah_profile[field] = value
                service = InstitutionalInstructionExecutionService(
                    runtime_binding_resolver=self.resolver,
                    pipeline_factory=lambda context: MismatchedPipeline(),
                )
                with self.assertRaises(InstitutionalInstructionExecutionError):
                    service.execute(
                        tenant_id="tenant-ug", institution_id="BANK-UG-01",
                        binding_id="BIND-UG-01", instruction=instruction(),
                        evidence=instruction()["evidence"],
                    )

    def test_institution_version_change_builds_a_new_pinned_pipeline(self):
        resolver = RuntimeResolver()
        created = []

        def factory(context):
            pipeline = Pipeline()
            created.append((dict(context), pipeline))
            return pipeline

        service = InstitutionalInstructionExecutionService(
            runtime_binding_resolver=resolver,
            pipeline_factory=factory,
        )
        for index in (1, 2):
            request = instruction()
            request["request_id"] = f"REQ-CACHE-{index}"
            request["operation_id"] = f"OP-CACHE-{index}"
            request["idempotency_key"] = f"idempotency-cache-{index}"
            request["payload_digest"] = compute_payload_digest(request)
            outcome = service.execute(
                tenant_id="tenant-ug",
                institution_id="BANK-UG-01",
                binding_id="BIND-UG-01",
                instruction=request,
                evidence=request["evidence"],
            )
            self.assertEqual(outcome["decision"]["protocol_decision"], "PASSED")
            if index == 1:
                resolver.context["institution_version"] = "2.1"

        self.assertEqual(len(created), 2)
        self.assertEqual(created[0][0]["institution_version"], "2.0")
        self.assertEqual(created[1][0]["institution_version"], "2.1")
        self.assertIsNot(created[0][1], created[1][1])

    def test_rejects_instruction_for_different_jurisdiction_before_pipeline(self):
        request = instruction()
        request["jurisdiction"] = "KE"
        request["payload_digest"] = compute_payload_digest(request)
        with self.assertRaisesRegex(
            InstitutionalInstructionExecutionError,
            "INSTRUCTION_JURISDICTION_DOES_NOT_MATCH_ACTIVE_BINDING",
        ):
            self.service.execute(
                tenant_id="tenant-ug", institution_id="BANK-UG-01",
                binding_id="BIND-UG-01", instruction=request, evidence=request["evidence"],
            )
        self.assertEqual(self.pipeline.requests, [])

    def test_rejects_policy_version_drift_before_pipeline(self):
        request = instruction()
        request["rule_pack_version"] = "BANK-RULES:2.0"
        request["payload_digest"] = compute_payload_digest(request)
        with self.assertRaisesRegex(
            InstitutionalInstructionExecutionError,
            "INSTRUCTION_POLICY_VERSION_DOES_NOT_MATCH_ACTIVE_BINDING",
        ):
            self.service.execute(
                tenant_id="tenant-ug", institution_id="BANK-UG-01",
                binding_id="BIND-UG-01", instruction=request, evidence=request["evidence"],
            )
        self.assertEqual(self.pipeline.requests, [])

    def test_rejects_runtime_context_from_another_tenant(self):
        self.resolver.context["tenant_id"] = "tenant-ke"
        request = instruction()
        with self.assertRaisesRegex(
            InstitutionalInstructionExecutionError, "RESOLVED_RUNTIME_IDENTITY_MISMATCH"
        ):
            self.service.execute(
                tenant_id="tenant-ug", institution_id="BANK-UG-01",
                binding_id="BIND-UG-01", instruction=request,
                evidence=request["evidence"],
            )
        self.assertEqual(self.pipeline.requests, [])

    def test_reference_pipeline_persists_institutional_pins_before_minah_review(self):
        context = dict(self.resolver.context)
        context.update({
            "jurisdiction_ref": "UG-DEMO",
            "policy_pack_version": "KIN-SYS-DEMO-RULES:1.0",
            "source_pack_version": "SYNTHETIC-NO-LEGAL-SOURCES",
            "minah_config": {
                "deployment_id": "MINAH-UG-01",
                "owner_ref": "tenant-ug",
                "model_ref": "deterministic-minah-mock",
                "jurisdictions": ["UG-DEMO"],
                "policy_pack_version": "KIN-SYS-DEMO-RULES:1.0",
                "source_pack_version": "SYNTHETIC-NO-LEGAL-SOURCES",
            },
        })
        self.resolver.context = context
        profile = {
            "profile_version": "kin-sys-minah-profile-0.1",
            "deployment_id": "MINAH-UG-01",
            "owner_scope": "INSTITUTION",
            "owner_ref": "tenant-ug",
            "use_case": "INSTITUTIONAL_REFERENCE_TEST",
            "jurisdictions": ["UG-DEMO"],
            "policy_pack_version": "KIN-SYS-DEMO-RULES:1.0",
            "source_pack_version": "SYNTHETIC-NO-LEGAL-SOURCES",
            "model_ref": "deterministic-minah-mock",
            "deployment_mode": "SELF_HOSTED",
            "may_authorise": False,
            "endpoint_ref": "secret://must-not-persist",
        }
        pipeline = KinSysProtocolPipeline(
            minah_profile=profile,
            cee_database=":memory:",
            ivm_database=":memory:",
            identity_provider_id="IDP-UG-01",
            identity_provider_version="2.0",
            jurisdiction_package_id="JUR-UG-01",
            jurisdiction_package_version="4.0",
            authority_profile_version="BANK-AUTHORITY:1.0",
        )
        self.addCleanup(pipeline.close)
        active_minah = ActiveMinahResolver({
            "tenant_id": "tenant-ug",
            "deployment_id": "MINAH-UG-01",
            "version": "9.0",
            "status": "ACTIVE",
            "config": dict(profile),
        })
        service = InstitutionalInstructionExecutionService(
            runtime_binding_resolver=self.resolver,
            pipeline_factory=lambda runtime_context: pipeline,
            minah_deployment_resolver=active_minah,
        )
        evidence = {
            "consignment_id": "PROCESS-REAL-1",
            "commodity": "SYNTHETIC_MAIZE",
            "origin": "UGANDA-DEMO",
            "destination": "BORDER-DEMO",
            "rule_refs": ["SYNTHETIC-RULE-1"],
        }
        request = {
            "contract_version": CONTRACT_VERSION,
            "request_id": "REQ-REAL-1",
            "operation_id": "OP-REAL-1",
            "idempotency_key": "idempotency-real-1",
            "principal_ref": "AUTHORITY-001",
            "authority_ref": "AUTHORITY-001",
            "action": "ACTIVATE",
            "target_ref": "PROCESS-REAL-1",
            "purpose": "Synthetic institutional execution test",
            "jurisdiction": "UG-DEMO",
            "submitted_at": "2026-11-07T10:00:00+00:00",
            "rule_pack_version": "KIN-SYS-DEMO-RULES:1.0",
            "evidence_refs": ["evidence:real-1"],
            "evidence": evidence,
        }
        request["payload_digest"] = compute_payload_digest(request)
        outcome = service.execute(
            tenant_id="tenant-ug", institution_id="BANK-UG-01",
            binding_id="BIND-UG-01", instruction=request, evidence=evidence,
        )
        self.assertEqual(outcome["decision"]["protocol_decision"], "PASSED")
        operation = pipeline.cee.get("OP-REAL-1")
        self.assertIsNotNone(operation)
        self.assertEqual(
            operation["request_json"]["_institutional_runtime_binding"],
            outcome["runtime_pins"],
        )
        stored_profile = operation["request_json"]["_minah_deployment_profile"]
        self.assertEqual(
            stored_profile,
            {key: value for key, value in profile.items() if key != "endpoint_ref"},
        )
        self.assertNotIn("endpoint_ref", stored_profile)
        self.assertNotIn("must-not-persist", str(operation["request_json"]))
        self.assertEqual(
            operation["request_json"]["_institutional_runtime_binding"]["binding_version"],
            "3.0",
        )

        inspected = service.inspect(
            tenant_id="tenant-ug", operation_id="OP-REAL-1")
        self.assertEqual(inspected["phase"], "FINALIZED")
        self.assertEqual(inspected["runtime_pins"], outcome["runtime_pins"])
        self.assertFalse(inspected["recovery_required"])
        recovered = service.recover(
            tenant_id="tenant-ug", operation_id="OP-REAL-1",
            actor_ref="operator:alice", request_id="recovery-real-1")
        self.assertEqual(recovered["decision"]["protocol_decision"], "PASSED")
        observations = pipeline.cee.observations("OP-REAL-1")
        recovery_observations = [
            item for item in observations
            if item["kind"] == "INSTITUTIONAL_RECOVERY"
        ]
        self.assertEqual(
            [item["status"] for item in recovery_observations],
            ["REQUESTED", "COMMITTED"],
        )

        # An executor without a live lifecycle resolver cannot resume an
        # operation merely because its original in-memory pipeline still exists.
        class RuntimeResolverWithoutMinah:
            def __init__(self, delegate):
                self.delegate = delegate

            def resolve(self, **kwargs):
                return self.delegate.resolve(**kwargs)

        unguarded_service = InstitutionalInstructionExecutionService(
            runtime_binding_resolver=RuntimeResolverWithoutMinah(self.resolver),
            pipeline_factory=lambda runtime_context: pipeline,
            # Resolve the already-persisted operation so this assertion reaches
            # the lifecycle gate rather than returning "original runtime missing".
            operation_pipeline_resolver=lambda **kwargs: pipeline,
        )
        with self.assertRaisesRegex(
            InstitutionalInstructionExecutionError,
            "INSTITUTIONAL_MINAH_LIFECYCLE_RESOLVER_REQUIRED",
        ):
            unguarded_service.recover(
                tenant_id="tenant-ug", operation_id="OP-REAL-1",
                actor_ref="operator:alice", request_id="recovery-no-lifecycle")

        # Historical inspection remains available during lifecycle suspension,
        # but recovery is blocked until the exact pinned deployment is active.
        active_minah.deployment["status"] = "SUSPENDED"
        self.assertIsNotNone(service.inspect(
            tenant_id="tenant-ug", operation_id="OP-REAL-1"))
        with self.assertRaisesRegex(
            InstitutionalInstructionExecutionError,
            "INSTITUTIONAL_MINAH_DEPLOYMENT_NOT_ACTIVE",
        ):
            service.recover(
                tenant_id="tenant-ug", operation_id="OP-REAL-1",
                actor_ref="operator:alice", request_id="recovery-suspended")
        active_minah.deployment["status"] = "REVOKED"
        with self.assertRaisesRegex(
            InstitutionalInstructionExecutionError,
            "INSTITUTIONAL_MINAH_DEPLOYMENT_NOT_ACTIVE",
        ):
            service.recover(
                tenant_id="tenant-ug", operation_id="OP-REAL-1",
                actor_ref="operator:alice", request_id="recovery-revoked")
        active_minah.deployment.update(status="ACTIVE", version="10.0")
        with self.assertRaisesRegex(
            InstitutionalInstructionExecutionError,
            "INSTITUTIONAL_MINAH_DEPLOYMENT_VERSION_MISMATCH",
        ):
            service.recover(
                tenant_id="tenant-ug", operation_id="OP-REAL-1",
                actor_ref="operator:alice", request_id="recovery-version-drift")
        active_minah.deployment["version"] = "9.0"
        active_minah.deployment["config"]["model_ref"] = "replacement-model"
        with self.assertRaisesRegex(
            InstitutionalInstructionExecutionError,
            "INSTITUTIONAL_MINAH_DEPLOYMENT_CONFIGURATION_MISMATCH:model_ref",
        ):
            service.recover(
                tenant_id="tenant-ug", operation_id="OP-REAL-1",
                actor_ref="operator:alice", request_id="recovery-config-drift")
        active_minah.deployment["config"]["model_ref"] = profile["model_ref"]
        recovered_again = service.recover(
            tenant_id="tenant-ug", operation_id="OP-REAL-1",
            actor_ref="operator:alice", request_id="recovery-reactivated")
        self.assertEqual(recovered_again["decision"]["protocol_decision"], "PASSED")

        # A previously successful operation must not be inspected or resumed
        # through a MINAH profile whose deployment identity or governance scope
        # has changed since admission.
        original_profile = dict(pipeline.minah_profile)
        drift_cases = (
            ("deployment_id", "MINAH-UG-REPLACEMENT"),
            ("owner_ref", "tenant-other"),
            ("model_ref", "replacement-model"),
            ("policy_pack_version", "KIN-SYS-DEMO-RULES:2.0"),
            ("source_pack_version", "REPLACED-SOURCES:1.0"),
            ("jurisdictions", ["KE"]),
            ("may_authorise", True),
        )
        for field, value in drift_cases:
            with self.subTest(recovery_profile_field=field):
                pipeline.minah_profile[field] = value
                with self.assertRaisesRegex(
                    InstitutionalInstructionExecutionError,
                    "INSTITUTIONAL_ORIGINAL_MINAH_PROFILE_MISMATCH",
                ):
                    service.inspect(
                        tenant_id="tenant-ug", operation_id="OP-REAL-1")
                with self.assertRaisesRegex(
                    InstitutionalInstructionExecutionError,
                    "INSTITUTIONAL_ORIGINAL_MINAH_PROFILE_MISMATCH",
                ):
                    service.recover(
                        tenant_id="tenant-ug", operation_id="OP-REAL-1",
                        actor_ref="operator:alice", request_id="recovery-drift-" + field.replace("_", "-"))
                pipeline.minah_profile.clear()
                pipeline.minah_profile.update(original_profile)


    def test_recovery_never_resolves_a_new_active_binding_when_original_runtime_is_unavailable(self):
        resolver = RuntimeResolver()
        service = InstitutionalInstructionExecutionService(
            runtime_binding_resolver=resolver,
            pipeline_factory=lambda context: self.fail("recovery must not build a replacement pipeline"),
        )
        outcome = service.recover(
            tenant_id="tenant-ug", operation_id="OP-OLD-1",
            actor_ref="operator:alice", request_id="request-recovery-1",
        )
        self.assertEqual(outcome["status"], "UNKNOWN")
        self.assertEqual(outcome["reason"], "ORIGINAL_PINNED_RUNTIME_UNAVAILABLE")
        self.assertEqual(resolver.calls, [])


    def test_minah_rejection_and_deferral_stop_before_identity_authority_and_ivm(self):
        class DispositionMinah:
            def __init__(self, status):
                self.status = status
                self.review_calls = 0

            def review(self, evidence):
                self.review_calls += 1
                return {
                    "status": self.status,
                    "missing_fields": ["additional_evidence"] if self.status == "NEEDS_INFORMATION" else [],
                    "source_refs": ["source:institutional-test"],
                    "uncertainty": "HIGH",
                    "may_authorise": False,
                    "model_version": "institutional-minah-test-v1",
                }

        evidence = {
            "consignment_id": "PROCESS-GATE-TEST",
            "commodity": "SYNTHETIC_MAIZE",
            "origin": "UGANDA-DEMO",
            "destination": "BORDER-DEMO",
            "rule_refs": ["SYNTHETIC-RULE-1"],
        }
        profile = {
            "profile_version": "kin-sys-minah-profile-0.1",
            "deployment_id": "MINAH-UG-01",
            "owner_scope": "INSTITUTION",
            "owner_ref": "tenant-ug",
            "use_case": "INSTITUTIONAL_GATE_TEST",
            "jurisdictions": ["UG-DEMO"],
            "policy_pack_version": "KIN-SYS-DEMO-RULES:1.0",
            "source_pack_version": "SYNTHETIC-NO-LEGAL-SOURCES",
            "model_ref": "institutional-minah-test-v1",
            "deployment_mode": "SELF_HOSTED",
            "may_authorise": False,
        }

        class FailedVerificationAdapter:
            def __init__(self, status):
                self.status = status
                self.calls = 0

            def verify_principal(self, request):
                self.calls += 1
                return {"verified": False, "status": self.status, "mode": "TEST_ADAPTER"}

            def verify_action(self, request):
                self.calls += 1
                return {"verified": False, "status": self.status, "mode": "TEST_ADAPTER"}

        scenarios = (
            ("REJECTED", "REJECTED", None, None),
            ("NEEDS_INFORMATION", "DEFERRED", None, None),
            ("ADVISORY_ONLY", "REJECTED", "IDENTITY", "REJECTED"),
            ("ADVISORY_ONLY", "REJECTED", "AUTHORITY", "REJECTED"),
        )
        for index, (minah_status, expected_decision, failed_gate, failure_status) in enumerate(scenarios):
            with self.subTest(minah_status=minah_status, failed_gate=failed_gate):
                minah = DispositionMinah(minah_status)
                failed_adapter = FailedVerificationAdapter(failure_status) if failed_gate else None
                pipeline = KinSysProtocolPipeline(
                    minah_adapter=minah,
                    identity_adapter=failed_adapter if failed_gate == "IDENTITY" else None,
                    authority_adapter=failed_adapter if failed_gate == "AUTHORITY" else None,
                    minah_profile=profile,
                    cee_database=":memory:",
                    ivm_database=":memory:",
                    identity_provider_id="IDP-UG-01",
                    identity_provider_version="2.0",
                    jurisdiction_package_id="JUR-UG-01",
                    jurisdiction_package_version="4.0",
                    authority_profile_version="BANK-AUTHORITY:1.0",
                )
                self.addCleanup(pipeline.close)
                resolver = RuntimeResolver()
                resolver.context.update({
                    "jurisdiction_ref": "UG-DEMO",
                    "policy_pack_version": "KIN-SYS-DEMO-RULES:1.0",
                    "source_pack_version": "SYNTHETIC-NO-LEGAL-SOURCES",
                    "minah_config": {
                        "deployment_id": "MINAH-UG-01",
                        "owner_ref": "tenant-ug",
                        "model_ref": "institutional-minah-test-v1",
                        "jurisdictions": ["UG-DEMO"],
                        "policy_pack_version": "KIN-SYS-DEMO-RULES:1.0",
                        "source_pack_version": "SYNTHETIC-NO-LEGAL-SOURCES",
                    },
                })
                service = InstitutionalInstructionExecutionService(
                    runtime_binding_resolver=resolver,
                    pipeline_factory=lambda context: pipeline,
                )
                request = instruction()
                request.update({
                    "request_id": "REQ-GATE-" + str(index),
                    "operation_id": "OP-GATE-" + str(index),
                    "idempotency_key": "idempotency-gate-" + str(index),
                    "jurisdiction": "UG-DEMO",
                    "rule_pack_version": "KIN-SYS-DEMO-RULES:1.0",
                    "target_ref": "PROCESS-GATE-" + str(index),
                    "evidence": evidence,
                    "evidence_refs": ["evidence:gate-test"],
                })
                request["payload_digest"] = compute_payload_digest(request)

                outcome = service.execute(
                    tenant_id="tenant-ug",
                    institution_id="BANK-UG-01",
                    binding_id="BIND-UG-01",
                    instruction=request,
                    evidence=evidence,
                )

                self.assertEqual(outcome["decision"]["protocol_decision"], expected_decision)
                self.assertEqual(minah.review_calls, 1)
                if failed_adapter is not None:
                    self.assertEqual(failed_adapter.calls, 1)
                operation = pipeline.cee.get(request["operation_id"])
                self.assertEqual(operation["phase"], "FINALIZED")
                self.assertEqual(operation["outcome"], expected_decision)
                self.assertIsNone(operation["ivm_json"])
                self.assertIsNone(pipeline.ivm.event(
                    "EV-" + hashlib.sha256(
                        request["idempotency_key"].encode("utf-8")
                    ).hexdigest()[:24]
                ))
                self.assertEqual(pipeline.ivm.history(request["target_ref"]), [])
                terminal_event = pipeline.cee.events(request["operation_id"])[-1]
                self.assertTrue(terminal_event["detail"]["ivm_not_invoked"])

if __name__ == "__main__":
    unittest.main()
