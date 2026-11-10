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
    postgresql_inbox_acceptor,
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
        consume_nonce: Callable | None = None,
        clock: Callable,
        accept_verified_message: Callable | None = None,
        inbox: Any | None = None,
        inbox_worker_id: str | None = None,
        inbox_lease_seconds: int = 60,
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
            ("clock", clock),
        ):
            if not callable(callback):
                raise FederatedInstructionReceiverError(
                    "RECEIVER_CALLBACK_REQUIRED:" + name)
        if inbox is not None:
            required_inbox_methods = ("accept_authenticated", "claim", "complete", "fail", "get")
            if any(not callable(getattr(inbox, name, None)) for name in required_inbox_methods):
                raise FederatedInstructionReceiverError("RECEIVER_DURABLE_INBOX_INVALID")
            if not isinstance(inbox_worker_id, str) or not inbox_worker_id.strip():
                raise FederatedInstructionReceiverError("RECEIVER_INBOX_WORKER_ID_REQUIRED")
            if type(inbox_lease_seconds) is not int or not 1 <= inbox_lease_seconds <= 3600:
                raise FederatedInstructionReceiverError("RECEIVER_INBOX_LEASE_DURATION_INVALID")
            if accept_verified_message is not None:
                raise FederatedInstructionReceiverError(
                    "RECEIVER_INBOX_ACCEPTOR_MUST_BE_DERIVED_FROM_INBOX")
            accept_verified_message = postgresql_inbox_acceptor(inbox)
        if callable(consume_nonce) == callable(accept_verified_message):
            raise FederatedInstructionReceiverError(
                "RECEIVER_EXACTLY_ONE_REPLAY_ACCEPTOR_REQUIRED")
        if accept_verified_message is not None and inbox is None:
            raise FederatedInstructionReceiverError(
                "RECEIVER_DURABLE_INBOX_REQUIRED_FOR_ATOMIC_ACCEPTANCE")
        self.receiver_tenant_id = receiver_tenant_id
        self.receiver_installation_id = receiver_installation_id
        self.receiver_institution_id = receiver_institution_id
        self.receiver_binding_id = receiver_binding_id
        self.execution_service = execution_service
        self.trust_key_loader = trust_key_loader
        self.public_key_loader = public_key_loader
        self.consume_nonce = consume_nonce
        self.accept_verified_message = accept_verified_message
        self.inbox = inbox
        self.inbox_worker_id = inbox_worker_id
        self.inbox_lease_seconds = inbox_lease_seconds
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
                accept_verified_message=self.accept_verified_message,
                clock=self.clock,
            )
        except FederationMessageError as exc:
            raise FederatedInstructionReceiverError(str(exc)) from exc

        acceptance = verified.get("inbox_acceptance")
        inbox_claim = None
        if isinstance(acceptance, dict) and acceptance.get("inserted") is not True:
            if self.inbox is None:
                raise FederatedInstructionReceiverError(
                    "FEDERATED_MESSAGE_ALREADY_DURABLY_ACCEPTED")
            existing = self.inbox.get(
                receiver_tenant_id=self.receiver_tenant_id,
                issuer_installation_id=verified["issuer_installation_id"],
                message_id=verified["message_id"],
            )
            if existing is None:
                raise FederatedInstructionReceiverError("FEDERATION_INBOX_RECORD_MISSING")
            if existing.get("status") == "COMPLETED" and isinstance(existing.get("response"), dict):
                response = dict(existing["response"])
                response["duplicate_delivery"] = True
                response["delivery_status"] = "COMPLETED"
                return response
            # Retryable failures and expired PROCESSING leases are reclaimable.
            # An active lease remains owned by the other worker.
            inbox_claim = self.inbox.claim(
                receiver_tenant_id=self.receiver_tenant_id,
                issuer_installation_id=verified["issuer_installation_id"],
                message_id=verified["message_id"],
                worker_id=self.inbox_worker_id,
                lease_seconds=self.inbox_lease_seconds,
            )
            if inbox_claim is None:
                return {
                    "message_id": verified["message_id"],
                    "operation_id": verified["operation_id"],
                    "receiver_installation_id": self.receiver_installation_id,
                    "receiver_institution_id": self.receiver_institution_id,
                    "message_verified": True,
                    "duplicate_delivery": True,
                    "delivery_status": existing.get("status", "UNKNOWN"),
                    "acknowledgement_status": "PENDING",
                    "production_ready": False,
                }

        if self.inbox is not None and inbox_claim is None:
            inbox_claim = self.inbox.claim(
                receiver_tenant_id=self.receiver_tenant_id,
                issuer_installation_id=verified["issuer_installation_id"],
                message_id=verified["message_id"],
                worker_id=self.inbox_worker_id,
                lease_seconds=self.inbox_lease_seconds,
            )
            if inbox_claim is None:
                existing = self.inbox.get(
                    receiver_tenant_id=self.receiver_tenant_id,
                    issuer_installation_id=verified["issuer_installation_id"],
                    message_id=verified["message_id"],
                )
                return {
                    "message_id": verified["message_id"],
                    "operation_id": verified["operation_id"],
                    "receiver_installation_id": self.receiver_installation_id,
                    "receiver_institution_id": self.receiver_institution_id,
                    "message_verified": True,
                    "duplicate_delivery": bool(not acceptance or not acceptance.get("inserted")),
                    "delivery_status": (existing or {}).get("status", "UNKNOWN"),
                    "acknowledgement_status": "PENDING",
                    "production_ready": False,
                }

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
            # Leave a retryable durable state. If the process died after the
            # CEE/IVM commit, a later lease owner reconciles through the
            # execution service's pinned operation journal and IVM idempotency.
            if self.inbox is not None:
                try:
                    self.inbox.fail(
                        receiver_tenant_id=self.receiver_tenant_id,
                        issuer_installation_id=verified["issuer_installation_id"],
                        message_id=verified["message_id"],
                        worker_id=self.inbox_worker_id,
                        error_code="RECEIVER_EXECUTION_REQUIRES_RETRY",
                        retryable=True,
                    )
                except Exception:
                    pass
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
            if self.inbox is not None:
                self.inbox.fail(
                    receiver_tenant_id=self.receiver_tenant_id,
                    issuer_installation_id=verified["issuer_installation_id"],
                    message_id=verified["message_id"],
                    worker_id=self.inbox_worker_id,
                    error_code="RECEIVER_LOCAL_DECISION_INVALID",
                    retryable=False,
                )
            raise FederatedInstructionReceiverError(
                "RECEIVER_LOCAL_DECISION_INVALID")
        decision = local["decision"]
        if (decision.get("operation_id") != verified["operation_id"]
                or decision.get("request_id") != instruction.get("request_id")):
            raise FederatedInstructionReceiverError(
                "RECEIVER_LOCAL_DECISION_BINDING_MISMATCH")
        response = {
            "message_id": verified["message_id"],
            "operation_id": verified["operation_id"],
            "origin_installation_id": verified["issuer_installation_id"],
            "origin_institution_id": verified["issuer_institution_id"],
            "receiver_installation_id": self.receiver_installation_id,
            "receiver_institution_id": self.receiver_institution_id,
            "message_verified": True,
            "durable_inbox_acceptance": verified.get("inbox_acceptance"),
            "origin_authority_inherited": False,
            "receiver_execution_authorised_by_origin": False,
            "receiver_decision": decision,
            "receiver_result": local.get("result", {}),
            "delivery_status": "COMPLETED",
            "duplicate_delivery": False,
            "production_ready": False,
        }
        if self.inbox is not None:
            decision_ref = decision.get("cee_execution_ref")
            if not isinstance(decision_ref, str) or not decision_ref.strip():
                self.inbox.fail(
                    receiver_tenant_id=self.receiver_tenant_id,
                    issuer_installation_id=verified["issuer_installation_id"],
                    message_id=verified["message_id"],
                    worker_id=self.inbox_worker_id,
                    error_code="RECEIVER_DECISION_REFERENCE_MISSING",
                    retryable=False,
                )
                raise FederatedInstructionReceiverError(
                    "RECEIVER_DECISION_REFERENCE_MISSING")
            completed = self.inbox.complete(
                receiver_tenant_id=self.receiver_tenant_id,
                issuer_installation_id=verified["issuer_installation_id"],
                message_id=verified["message_id"],
                worker_id=self.inbox_worker_id,
                decision_ref=decision_ref,
                response=response,
            )
            if completed is not True:
                raise FederatedInstructionReceiverError(
                    "RECEIVER_INBOX_COMPLETION_NOT_CONFIRMED")
        return response
