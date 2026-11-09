# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Receiver-side handoff into the receiving institution's governed executor.

The receiver selects its tenant, institution and binding from its own trusted
configuration. No origin-supplied identity or authority value is used to select
the receiving runtime. The injected executor must be the institution's
InstitutionalInstructionExecutionService (or a contract-compatible adapter
that preserves its MINAH/identity/authority/CEE/final-decision guarantees).
"""
from __future__ import annotations

from typing import Any, Callable

from institutional_federation import (
    FederationMessageError,
    verify_cross_installation_instruction,
)


class FederatedInstructionReceiverError(ValueError):
    """A verified federation message could not safely enter local execution."""


class InstitutionalFederatedInstructionReceiver:
    def __init__(
        self, *, receiver_tenant_id: str, receiver_installation_id: str,
        receiver_institution_id: str, receiver_binding_id: str,
        execution_service: Any,
        trust_key_loader: Callable,
        public_key_loader: Callable,
        consume_nonce: Callable,
        clock: Callable,
    ):
        for field, value in (
            ("receiver_tenant_id", receiver_tenant_id),
            ("receiver_installation_id", receiver_installation_id),
            ("receiver_institution_id", receiver_institution_id),
            ("receiver_binding_id", receiver_binding_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise FederatedInstructionReceiverError(
                    "RECEIVER_CONFIGURATION_REQUIRED:" + field)
        if not callable(getattr(execution_service, "execute", None)):
            raise FederatedInstructionReceiverError(
                "RECEIVER_GOVERNED_EXECUTION_SERVICE_REQUIRED")
        for name, callback in (
            ("trust_key_loader", trust_key_loader),
            ("public_key_loader", public_key_loader),
            ("consume_nonce", consume_nonce),
            ("clock", clock),
        ):
            if not callable(callback):
                raise FederatedInstructionReceiverError(
                    "RECEIVER_CALLBACK_REQUIRED:" + name)
        self.receiver_tenant_id = receiver_tenant_id
        self.receiver_installation_id = receiver_installation_id
        self.receiver_institution_id = receiver_institution_id
        self.receiver_binding_id = receiver_binding_id
        self.execution_service = execution_service
        self.trust_key_loader = trust_key_loader
        self.public_key_loader = public_key_loader
        self.consume_nonce = consume_nonce
        self.clock = clock

    def receive(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Verify the handoff, then invoke only the receiver's local executor."""
        try:
            verified = verify_cross_installation_instruction(
                envelope,
                receiver_tenant_id=self.receiver_tenant_id,
                receiver_installation_id=self.receiver_installation_id,
                receiver_institution_id=self.receiver_institution_id,
                trust_key_loader=self.trust_key_loader,
                public_key_loader=self.public_key_loader,
                consume_nonce=self.consume_nonce,
                clock=self.clock,
            )
        except FederationMessageError as exc:
            raise FederatedInstructionReceiverError(str(exc)) from exc

        instruction = verified["instruction"]
        evidence = instruction.get("evidence")
        if not isinstance(evidence, dict):
            raise FederatedInstructionReceiverError(
                "FEDERATED_INSTRUCTION_EVIDENCE_REQUIRED")
        try:
            # These identifiers come exclusively from this receiver's
            # installation configuration, never from the signed origin claims.
            local = self.execution_service.execute(
                tenant_id=self.receiver_tenant_id,
                institution_id=self.receiver_institution_id,
                binding_id=self.receiver_binding_id,
                instruction=instruction,
                evidence=evidence,
            )
        except Exception as exc:
            # Do not convert a local rejection into a federation acceptance.
            # Keep machine-readable protocol reasons while hiding adapter data.
            reason = str(exc)
            if reason and len(reason) <= 120 and all(
                c.isupper() or c.isdigit() or c in "_:-" for c in reason
            ):
                raise FederatedInstructionReceiverError(
                    "RECEIVER_LOCAL_EXECUTION:" + reason) from exc
            raise FederatedInstructionReceiverError(
                "RECEIVER_LOCAL_EXECUTION_FAILED") from exc
        if not isinstance(local, dict) or not isinstance(local.get("decision"), dict):
            raise FederatedInstructionReceiverError(
                "RECEIVER_LOCAL_DECISION_INVALID")
        decision = local["decision"]
        if (decision.get("operation_id") != verified["operation_id"]
                or decision.get("request_id") != instruction.get("request_id")):
            raise FederatedInstructionReceiverError(
                "RECEIVER_LOCAL_DECISION_BINDING_MISMATCH")
        return {
            "message_id": verified["message_id"],
            "operation_id": verified["operation_id"],
            "origin_installation_id": verified["issuer_installation_id"],
            "origin_institution_id": verified["issuer_institution_id"],
            "receiver_installation_id": self.receiver_installation_id,
            "receiver_institution_id": self.receiver_institution_id,
            "message_verified": True,
            "origin_authority_inherited": False,
            "receiver_execution_authorised_by_origin": False,
            "receiver_decision": decision,
            "receiver_result": local.get("result", {}),
            "receiver_runtime_pins": local.get("runtime_pins", {}),
            "production_ready": False,
        }
