# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Provider-neutral contract for institutional execution coordination.

This is an interface and semantic boundary, not a networked coordinator.
Implementations must make the lease authority durable and shared by every
worker that claims to participate in the same tenant/deployment scope.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, TypedDict, runtime_checkable


@dataclass(frozen=True)
class ExecutionScope:
    """The isolation key for one institution-owned MINAH deployment."""

    tenant_id: str
    deployment_id: str

    def __post_init__(self) -> None:
        for field, value in (
            ("tenant_id", self.tenant_id),
            ("deployment_id", self.deployment_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("EXECUTION_SCOPE_" + field.upper() + "_REQUIRED")


class ExecutionLease(TypedDict):
    """Opaque lease capability plus public epoch/fencing claims."""

    lease_id: str
    lease_token: str
    tenant_id: str
    deployment_id: str
    execution_epoch: int
    fencing_token: int
    version: str
    expires_at: str


class ExecutionCoordinatorError(RuntimeError):
    """A coordinator cannot safely grant, validate, or use an execution lease."""


@runtime_checkable
class InstitutionalExecutionCoordinator(Protocol):
    """Durable lease authority required by institutional execution workers.

    Required implementation semantics:
    - Scope every lease and monotonic fencing sequence by (tenant_id,
      deployment_id); never share counters across those boundaries.
    - Acquire the lease and advance its fencing sequence atomically. Tokens
      must be strictly increasing, durable across process restart, and never
      reused after a committed acquisition.
    - Grant leases only for the current ACTIVE deployment epoch. Every
      lifecycle mutation that invalidates old work must atomically advance the
      epoch with the lifecycle version change.
    - Validate capability, scope, expiry, release state, current lifecycle
      status, epoch and fencing token against authoritative persisted state.
      Caller-supplied claims alone are never authoritative.
    - Release is idempotent. A released, expired, revoked, suspended or
      superseded lease must not regain validity.
    - Serialize a guarded action against lifecycle changes at the coordinator
      authority. This protects coordinator-owned state only: it does not make
      writes to a separate IVM/CEE database atomic with the coordinator.
    - For a multi-worker deployment, every worker must use the same durable
      coordinator authority. Independent local databases do not satisfy this
      requirement, even if they implement this Python interface.
    - Keep the lease capability secret out of logs, events, traces and durable
      business-instruction payloads. Persist only a verifier/digest as needed.

    The current SQLite registry structurally implements this interface for
    local reference testing. Structural conformance does not certify shared,
    networked, multi-process or production coordination.
    """

    def acquire_execution_lease(
        self, *, tenant_id: str, deployment_id: str, ttl_seconds: int = 30
    ) -> ExecutionLease: ...

    def validate_execution_lease(
        self, *, lease: ExecutionLease
    ) -> dict[str, Any]: ...

    def release_execution_lease(self, *, lease: ExecutionLease) -> None: ...

    def execute_under_execution_lease(
        self, *, lease: ExecutionLease, action: Callable[[], Any]
    ) -> Any: ...


def execution_scope(lease: dict[str, Any]) -> ExecutionScope:
    """Extract and validate the tenant/deployment isolation key from a lease."""
    if not isinstance(lease, dict):
        raise ValueError("EXECUTION_LEASE_MUST_BE_OBJECT")
    return ExecutionScope(
        tenant_id=lease.get("tenant_id"),
        deployment_id=lease.get("deployment_id"),
    )
