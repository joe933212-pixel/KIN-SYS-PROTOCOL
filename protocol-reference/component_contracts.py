# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Versioned semantic response contract for KIN-SYS prototype adapters.

This module validates the shape and routing of local adapter responses. It does
not authenticate a sender, prove that data is true, or provide wire security.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


RESPONSE_CONTRACT_VERSION = "kin-sys-component-response-0.1"
_ALLOWED_STATUSES = {"ACCEPTED", "REJECTED", "DEFERRED", "UNKNOWN", "ERROR"}


class ContractViolation(ValueError):
    """Raised when a component response violates the shared semantic contract."""


def make_component_response(
    *,
    component_id: str,
    operation_id: str,
    component_version: str,
    status: str,
    data: dict[str, Any] | None = None,
    failure_code: str | None = None,
) -> dict[str, Any]:
    """Create a detached response envelope using the shared contract version."""
    fields = {
        "component_id": component_id,
        "operation_id": operation_id,
        "component_version": component_version,
        "status": status,
    }
    if any(not isinstance(value, str) or not value.strip() for value in fields.values()):
        raise ContractViolation("INVALID_COMPONENT_RESPONSE_METADATA")
    if status not in _ALLOWED_STATUSES:
        raise ContractViolation("INVALID_COMPONENT_RESPONSE_STATUS")
    if data is not None and not isinstance(data, dict):
        raise ContractViolation("INVALID_COMPONENT_RESPONSE_DATA")
    if failure_code is not None and (not isinstance(failure_code, str) or not failure_code.strip()):
        raise ContractViolation("INVALID_COMPONENT_FAILURE_CODE")
    if status == "ACCEPTED" and failure_code is not None:
        raise ContractViolation("ACCEPTED_RESPONSE_CANNOT_DECLARE_FAILURE")
    if status != "ACCEPTED" and not failure_code:
        raise ContractViolation("NON_ACCEPTED_RESPONSE_REQUIRES_FAILURE_CODE")
    return {
        "contract_version": RESPONSE_CONTRACT_VERSION,
        **fields,
        "data": deepcopy(data),
        "failure_code": failure_code,
    }


def validate_component_response(
    response: Any,
    *,
    expected_component_id: str,
    expected_operation_id: str,
) -> dict[str, Any]:
    """Validate contract, route and required fields; return a detached envelope."""
    required = {
        "contract_version", "component_id", "operation_id", "component_version",
        "status", "data", "failure_code",
    }
    if not isinstance(response, dict) or not required.issubset(response):
        raise ContractViolation("MALFORMED_COMPONENT_RESPONSE")
    if response["contract_version"] != RESPONSE_CONTRACT_VERSION:
        raise ContractViolation("COMPONENT_CONTRACT_VERSION_MISMATCH")
    if response["component_id"] != expected_component_id:
        raise ContractViolation("COMPONENT_RESPONSE_IDENTITY_MISMATCH")
    if response["operation_id"] != expected_operation_id:
        raise ContractViolation("COMPONENT_RESPONSE_OPERATION_MISMATCH")
    if not isinstance(response["component_version"], str) or not response["component_version"].strip():
        raise ContractViolation("INVALID_COMPONENT_RESPONSE_METADATA")
    if response["status"] not in _ALLOWED_STATUSES:
        raise ContractViolation("INVALID_COMPONENT_RESPONSE_STATUS")
    if response["data"] is not None and not isinstance(response["data"], dict):
        raise ContractViolation("INVALID_COMPONENT_RESPONSE_DATA")
    if response["status"] == "ACCEPTED" and response["failure_code"] is not None:
        raise ContractViolation("ACCEPTED_RESPONSE_CANNOT_DECLARE_FAILURE")
    if response["status"] != "ACCEPTED" and (
        not isinstance(response["failure_code"], str) or not response["failure_code"].strip()
    ):
        raise ContractViolation("NON_ACCEPTED_RESPONSE_REQUIRES_FAILURE_CODE")
    return deepcopy(response)


def accepted_data(response: dict[str, Any]) -> dict[str, Any]:
    """Return data only from an accepted response; never promote failure to success."""
    if response["status"] != "ACCEPTED":
        raise ContractViolation(response["failure_code"] or "COMPONENT_RESPONSE_NOT_ACCEPTED")
    if not isinstance(response["data"], dict):
        raise ContractViolation("ACCEPTED_RESPONSE_MISSING_DATA")
    return deepcopy(response["data"])
