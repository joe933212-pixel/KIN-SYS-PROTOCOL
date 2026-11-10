# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Two-installation federation integration through independent governed runtimes.

This is a local reference integration test. It uses the real institutional
execution service and reference MINAH/CEE/IVM pipeline, but synthetic identity,
authority and MINAH adapters. It is not live federation or production proof.
"""
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from governed_contracts import CONTRACT_VERSION, compute_payload_digest
from institutional_federation import sign_cross_installation_instruction
from institutional_federated_receiver import InstitutionalFederatedInstructionReceiver
from institutional_instruction_execution import InstitutionalInstructionExecutionService
from protocol_pipeline import KinSysProtocolPipeline


class IsolatedInstallation:
    """One independently pinned installation with its own lease state and stores."""

    def __init__(self, *, country, tenant, institution, installation,
                 binding, jurisdiction, policy_version, source_version,
                 minah_id, idp_id, jurisdiction_package, authority_version):
        self.country = country
        self.tenant = tenant
        self.institution = institution
        self.installation = installation
        self.binding = binding
        self.jurisdiction = jurisdiction
        self.minah_id = minah_id
        self.idp_id = idp_id
        self.jurisdiction_package = jurisdiction_package
        self.authority_version = authority_version
        self.epoch = 1
        self.leases = {}
        self.next_fence = 0
        self.context = {
            "tenant_id": tenant,
            "institution_id": institution,
            "institution_version": "1",
            "binding_id": binding,
            "binding_version": "1",
            "jurisdiction_ref": jurisdiction,
            "jurisdiction_package_id": jurisdiction_package,
            "jurisdiction_package_version": "1",
            "minah_deployment_id": minah_id,
            "identity_provider_id": idp_id,
            "identity_provider_version": "1",
            "minah_domain_version": "1",
            "minah_runtime_version": "1",
            "policy_pack_version": policy_version,
            "source_pack_version": source_version,
            "authority_profile_version": authority_version,
            "runtime_admission": "ELIGIBLE_FOR_GOVERNED_EXECUTION",
        }
        self.profile = {
            "profile_version": "kin-sys-minah-profile-0.1",
            "deployment_id": minah_id,
            "owner_scope": "INSTITUTION",
            "owner_ref": tenant,
            "use_case": "CROSS_INSTALLATION_REFERENCE_TEST",
            "jurisdictions": [jurisdiction],
            "policy_pack_version": policy_version,
            "source_pack_version": source_version,
            "model_ref": "deterministic-minah-mock-" + country.lower(),
            "deployment_mode": "SELF_HOSTED",
            "may_authorise": False,
            "authority_profile_version": authority_version,
        }
        self.context["minah_config"] = {
            **self.profile,
            "jurisdiction_package_id": jurisdiction_package,
            "jurisdiction_package_version": "1",
            "interface_version": "minah-review-0.1",
        }
        self.context["minah_config"]["deployment_id"] = minah_id
        self.context["minah_config"]["owner_ref"] = tenant
        self.context["minah_config"]["model_ref"] = self.profile["model_ref"]
        self.pipeline = KinSysProtocolPipeline(
            minah_profile=self.profile,
            cee_database=":memory:",
            ivm_database=":memory:",
            identity_provider_id=idp_id,
            identity_provider_version="1",
            jurisdiction_package_id=jurisdiction_package,
            jurisdiction_package_version="1",
            authority_profile_version=authority_version,
        )
        self.resolver = self
        self.service = InstitutionalInstructionExecutionService(
            runtime_binding_resolver=self,
            pipeline_factory=lambda _context: self.pipeline,
        )

    def resolve(self, *, tenant_id, institution_id, binding_id):
        if (tenant_id, institution_id, binding_id) != (
            self.tenant, self.institution, self.binding
        ):
            raise ValueError("INSTALLATION_BINDING_MISMATCH")
        return dict(self.context)

    def resolve_active(self, *, tenant_id, deployment_id):
        if (tenant_id != self.tenant or deployment_id != self.minah_id):
            return None
        return {
            "tenant_id": self.tenant,
            "deployment_id": self.minah_id,
            "version": self.context["minah_runtime_version"],
            "status": "ACTIVE",
            "execution_epoch": self.epoch,
            "config": dict(self.context["minah_config"]),
        }

    def acquire_execution_lease(self, *, tenant_id, deployment_id):
        if tenant_id != self.tenant or deployment_id != self.minah_id:
            raise RuntimeError("LEASE_SCOPE_MISMATCH")
        self.next_fence += 1
        lease = {
            "lease_id": self.installation + "-LEASE-" + str(self.next_fence),
            "lease_token": self.installation + "-TOKEN-" + str(self.next_fence),
            "tenant_id": self.tenant,
            "deployment_id": self.minah_id,
            "execution_epoch": self.epoch,
            "fencing_token": self.next_fence,
            "version": self.context["minah_runtime_version"],
        }
        self.leases[lease["lease_id"]] = dict(lease)
        return lease

    def execute_under_execution_lease(self, *, lease, action):
        current = self.leases.get(lease.get("lease_id"))
        if current != lease or lease.get("execution_epoch") != self.epoch:
            raise RuntimeError("MINAH_EXECUTION_LEASE_FENCED")
        return action()

    def release_execution_lease(self, *, lease):
        self.leases.pop(lease.get("lease_id"), None)

    def execute_local(self, *, operation_id, request_id, jurisdiction,
                      policy_version, target_ref):
        evidence = {
            "case_ref": target_ref,
            "origin": self.country,
            "destination": "CROSS-BORDER-TEST",
            "rule_refs": ["SYNTHETIC-" + self.country + "-RULE"],
        }
        request = {
            "contract_version": CONTRACT_VERSION,
            "request_id": request_id,
            "operation_id": operation_id,
            "idempotency_key": "IDEM-" + operation_id,
            "principal_ref": "AUTHORITY-001",
            "authority_ref": "AUTHORITY-001",
            "action": "ACTIVATE",
            "target_ref": target_ref,
            "purpose": "Independent installation conformance test",
            "jurisdiction": jurisdiction,
            "submitted_at": "2026-11-09T10:00:00+00:00",
            "rule_pack_version": policy_version,
            "evidence_refs": ["evidence:" + operation_id],
            "evidence": evidence,
        }
        request["payload_digest"] = compute_payload_digest(request)
        return self.service.execute(
            tenant_id=self.tenant,
            institution_id=self.institution,
            binding_id=self.binding,
            instruction=request,
            evidence=evidence,
        )

    def close(self):
        self.service.close()
        self.pipeline.close()


class CrossInstallationRuntimeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_030
        self.origin = IsolatedInstallation(
            country="UG", tenant="tenant-ug", institution="BANK-UG-01",
            installation="INSTALL-UG-01", binding="BIND-UG-01",
            jurisdiction="UG", policy_version="UG-RULES:1.0",
            source_version="UG-SOURCES:1.0", minah_id="MINAH-UG-01",
            idp_id="IDP-UG-01", jurisdiction_package="UG-PACK-01",
            authority_version="UG-AUTHORITY:1.0",
        )
        self.receiver_installation = IsolatedInstallation(
            country="RW", tenant="tenant-rw", institution="BANK-RW-01",
            installation="INSTALL-RW-01", binding="BIND-RW-01",
            jurisdiction="RW", policy_version="RW-RULES:3.2",
            source_version="RW-SOURCES:2.0", minah_id="MINAH-RW-01",
            idp_id="IDP-RW-01", jurisdiction_package="RW-PACK-01",
            authority_version="RW-AUTHORITY:2.0",
        )
        self.addCleanup(self.origin.close)
        self.addCleanup(self.receiver_installation.close)
        self.private_key = Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        self.replay_nonces = set()
        self.trusted_key = {
            "tenant_id": "tenant-rw",
            "key_id": "KEY-UG-01",
            "issuer_institution_id": "BANK-UG-01",
            "algorithm": "Ed25519",
            "status": "ACTIVE",
            "public_key_ref": "keyref:ug-bank",
            "valid_from": 1_700_000_000,
            "valid_until": 1_900_000_000,
            "issuer_valid_from": 1_700_000_000,
            "issuer_valid_until": 1_900_000_000,
        }
        self.receiver = InstitutionalFederatedInstructionReceiver(
            receiver_tenant_id="tenant-rw",
            receiver_installation_id="INSTALL-RW-01",
            receiver_institution_id="BANK-RW-01",
            receiver_binding_id="BIND-RW-01",
            execution_service=self.receiver_installation.service,
            trust_key_loader=lambda tenant, key_id: (
                self.trusted_key
                if tenant == "tenant-rw" and key_id == "KEY-UG-01"
                else None
            ),
            public_key_loader=lambda key_ref: (
                self.public_key if key_ref == "keyref:ug-bank" else b""
            ),
            consume_nonce=self._consume_nonce,
            clock=lambda: self.now,
        )

    def _consume_nonce(self, tenant, issuer, nonce, expires_at):
        key = (tenant, issuer, nonce)
        if key in self.replay_nonces:
            return False
        self.replay_nonces.add(key)
        return True

    def _federated_instruction(self, *, jurisdiction="RW",
                               policy_version="RW-RULES:3.2",
                               operation_id="OP-UG-RW-001"):
        evidence = {
            "case_ref": "TX-UG-RW-001",
            "origin": "UG",
            "destination": "RW",
            "rule_refs": ["SYNTHETIC-CROSS-BORDER-RULE"],
        }
        request = {
            "contract_version": CONTRACT_VERSION,
            "request_id": "REQ-UG-RW-001",
            "operation_id": operation_id,
            "idempotency_key": "IDEM-" + operation_id,
            "principal_ref": "AUTHORITY-001",
            "authority_ref": "origin:authority-claim-is-not-inherited",
            "action": "ACTIVATE",
            "target_ref": "TX-UG-RW-001",
            "purpose": "Request independent receiving-institution review",
            "jurisdiction": jurisdiction,
            "submitted_at": "2026-11-09T10:00:00+00:00",
            "rule_pack_version": policy_version,
            "evidence_refs": ["evidence:" + operation_id],
            "evidence": evidence,
        }
        request["payload_digest"] = compute_payload_digest(request)
        return request

    def _envelope(self, request, *, nonce="NONCE-UG-RW-001"):
        return sign_cross_installation_instruction(
            instruction=request,
            message_id="MSG-" + request["operation_id"],
            key_id="KEY-UG-01",
            issuer_installation_id="INSTALL-UG-01",
            issuer_institution_id="BANK-UG-01",
            receiver_installation_id="INSTALL-RW-01",
            receiver_institution_id="BANK-RW-01",
            nonce=nonce,
            issued_at=self.now - 30,
            expires_at=self.now + 90,
            private_key=self.private_key,
        )

    def test_signed_instruction_executes_only_in_receiver_independent_runtime(self):
        origin_result = self.origin.execute_local(
            operation_id="OP-UG-LOCAL-001",
            request_id="REQ-UG-LOCAL-001",
            jurisdiction="UG",
            policy_version="UG-RULES:1.0",
            target_ref="TX-UG-LOCAL-001",
        )
        self.assertEqual(origin_result["decision"]["protocol_decision"], "PASSED")

        request = self._federated_instruction()
        result = self.receiver.receive(self._envelope(request))

        self.assertTrue(result["message_verified"])
        self.assertFalse(result["origin_authority_inherited"])
        self.assertFalse(result["receiver_execution_authorised_by_origin"])
        self.assertEqual(result["receiver_decision"]["protocol_decision"], "PASSED")
        self.assertEqual(result["receiver_runtime_pins"]["tenant_id"], "tenant-rw")
        self.assertEqual(result["receiver_runtime_pins"]["institution_id"], "BANK-RW-01")
        self.assertEqual(result["receiver_runtime_pins"]["jurisdiction_ref"], "RW")
        self.assertEqual(result["receiver_runtime_pins"]["minah_deployment_id"], "MINAH-RW-01")

        self.assertIsNotNone(self.origin.pipeline.cee.get("OP-UG-LOCAL-001"))
        self.assertIsNone(self.origin.pipeline.cee.get("OP-UG-RW-001"))
        self.assertIsNone(self.receiver_installation.pipeline.cee.get("OP-UG-LOCAL-001"))
        receiver_operation = self.receiver_installation.pipeline.cee.get("OP-UG-RW-001")
        self.assertIsNotNone(receiver_operation)
        self.assertEqual(receiver_operation["phase"], "FINALIZED")
        self.assertEqual(receiver_operation["outcome"], "COMMITTED")
        self.assertEqual(
            receiver_operation["request_json"]["_institutional_runtime_binding"]["tenant_id"],
            "tenant-rw",
        )
        self.assertNotEqual(
            self.origin.pipeline.cee, self.receiver_installation.pipeline.cee
        )

    def test_receiver_jurisdiction_rejection_does_not_create_receiver_operation(self):
        request = self._federated_instruction(jurisdiction="UG")
        with self.assertRaisesRegex(
            Exception, "INSTRUCTION_JURISDICTION_DOES_NOT_MATCH_ACTIVE_BINDING"
        ):
            self.receiver.receive(self._envelope(request, nonce="NONCE-WRONG-JUR-001"))
        self.assertIsNone(self.receiver_installation.pipeline.cee.get("OP-UG-RW-001"))
        self.assertIsNotNone(self.origin.pipeline.cee.get("OP-UG-LOCAL-001")
                             if self.origin.pipeline.cee.get("OP-UG-LOCAL-001") else None)

    def test_replay_is_rejected_before_receiver_execution(self):
        request = self._federated_instruction()
        envelope = self._envelope(request, nonce="NONCE-REPLAY-001")
        first = self.receiver.receive(envelope)
        self.assertEqual(first["receiver_decision"]["protocol_decision"], "PASSED")
        with self.assertRaises(Exception):
            self.receiver.receive(envelope)
        self.assertEqual(
            len([event for event in self.receiver_installation.pipeline.cee.events(
                "OP-UG-RW-001"
            ) if event["phase"] == "REQUESTED"]),
            1,
        )


if __name__ == "__main__":
    unittest.main()
