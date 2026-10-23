"""Local HTTP adapter for KIN-SYS synthetic reference slices.

This is an engineering prototype, not a production API:
- /demo/instructions calls the legacy integrated demonstrator with synthetic adapters;
- /protocol/instructions exercises a separate MINAH -> IVM -> CEE vertical slice;
- the new slice uses a local IVM SQLite transition store and a separate CEE journal;
- no production authentication, authoritative legal sources, live model, payment
  provider, distributed commit, or hardware integration is configured.
Run from the repository root:
    PYTHONPATH=protocol-reference python -m http_service
"""
from __future__ import annotations

import json
import os
import re
import uuid
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable
from wsgiref.simple_server import make_server

from integrated_demo import IntegratedDemonstrator
from protocol_pipeline import ExecutionConflict, KinSysProtocolPipeline
from transition import EventIdConflict


MAX_BODY_BYTES = 64 * 1024
_ALLOWED_FIELDS = {
    "consignment_id",
    "origin_decision",
    "destination_decision",
    "declared_value_ugx",
    "payment_outcome",
    "minah_evidence",
    "edge_connected",
    "edge_reconnect",
    "edge_current_evidence_version",
    "edge_current_rule_version",
    "edge_authority_active",
    "exempt",
}
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_PROTOCOL_FIELDS = {
    "operation_id", "event_id", "process_id", "actor_id", "event_type",
    "jurisdiction", "rule_set", "rule_version", "evidence",
}


class HttpServiceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _response(start_response: Callable, status: int, body: dict[str, Any], request_id: str):
    payload = _json_bytes({"request_id": request_id, **body})
    start_response(
        f"{status} {HTTPStatus(status).phrase}",
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(payload))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Request-ID", request_id),
        ],
    )
    return [payload]


class KinSysPrototypeApplication:
    """Small WSGI application exposing only the local synthetic vertical slice."""

    def __init__(self, demonstrator: IntegratedDemonstrator | None = None,
                 pipeline: KinSysProtocolPipeline | None = None):
        self.demonstrator = demonstrator or IntegratedDemonstrator()
        self.pipeline = pipeline or KinSysProtocolPipeline()

    def close(self) -> None:
        self.demonstrator.close()
        self.pipeline.close()

    def __call__(self, environ: dict[str, Any], start_response: Callable):
        request_id = environ.get("HTTP_X_REQUEST_ID", "")
        if not isinstance(request_id, str) or not _ID_RE.fullmatch(request_id):
            request_id = str(uuid.uuid4())
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")

        if path == "/system/health" and method == "GET":
            return _response(start_response, 200, {"status": "ALIVE", "service": "kin-sys-prototype"}, request_id)
        if path == "/system/readiness" and method == "GET":
            return _response(
                start_response, 200,
                {
                    "status": "READY_FOR_LOCAL_SYNTHETIC_DEMONSTRATION",
                    "production_ready": False,
                    "notes": ["No production authentication or external service adapters are configured."],
                },
                request_id,
            )
        if path == "/system/capabilities" and method == "GET":
            return _response(
                start_response, 200,
                {
                    "service": "KIN-SYS local prototype service",
                    "mode": "SYNTHETIC_ONLY",
                    "implemented_routes": [
                        "GET /system/health",
                        "GET /system/readiness",
                        "GET /system/capabilities",
                        "POST /demo/instructions",
                        "POST /protocol/instructions",
                        "GET /protocol/operations/{operation_id}",
                    ],
                    "instruction_flow": [
                        "POST /demo/instructions: legacy integrated synthetic demonstration",
                        "POST /protocol/instructions: MINAH disposition -> IVM transition checks -> CEE lifecycle journal",
                    ],
                    "protocol_pipeline": {
                        "status": "SYNTHETIC_VERTICAL_SLICE_IMPLEMENTED",
                        "ivm": "LOCAL_SQLITE_REFERENCE_TRANSITION_RUNTIME",
                        "cee": "LOCAL_SQLITE_DURABLE_LIFECYCLE_JOURNAL",
                        "cross_store_atomicity": False,
                        "distributed_finality": False,
                        "funds_moved": False,
                    },
                    "not_yet_wired_as_production_components": [
                        "production identity and cryptographic authentication",
                        "authoritative legal sources and live MINAH model service",
                        "live payment providers, distributed consensus, and Nexus Pod hardware",
                    ],
                    "funds_moved": False,
                },
                request_id,
            )
        operation_prefix = "/protocol/operations/"
        if path.startswith(operation_prefix):
            if method != "GET":
                return _response(
                    start_response, 405,
                    {"error": {"code": "METHOD_NOT_ALLOWED", "message": "Use GET to inspect a protocol operation."}},
                    request_id,
                )
            operation_id = path[len(operation_prefix):]
            if not _ID_RE.fullmatch(operation_id):
                return _response(
                    start_response, 400,
                    {"error": {"code": "INVALID_OPERATION_ID", "message": "Operation ID has an invalid format."}},
                    request_id,
                )
            operation = self.pipeline.inspect(operation_id)
            if operation is None:
                return _response(
                    start_response, 404,
                    {"error": {"code": "OPERATION_NOT_FOUND", "message": "No protocol operation matches this ID."}},
                    request_id,
                )
            return _response(
                start_response, 200,
                {"status": operation["phase"], "operation": operation, "production_ready": False},
                request_id,
            )
        if path == "/protocol/instructions":
            if method != "POST":
                return _response(
                    start_response, 405,
                    {"error": {"code": "METHOD_NOT_ALLOWED", "message": "Use POST for this protocol route."}},
                    request_id,
                )
            try:
                payload = self._read_json(environ, allowed_fields=_PROTOCOL_FIELDS)
                result = self.pipeline.execute(payload)
                return _response(
                    start_response, 200,
                    {
                        "status": result["status"],
                        "production_ready": False,
                        "result": result,
                        "protocol_boundary": {
                            "minah": "DETERMINISTIC_MOCK_ADVISORY_ONLY",
                            "ivm": "LOCAL_SQLITE_REFERENCE_RUNTIME",
                            "cee": "LOCAL_SQLITE_LIFECYCLE_JOURNAL",
                            "cross_store_atomicity": False,
                            "distributed_finality": False,
                            "funds_moved": False,
                        },
                    },
                    request_id,
                )
            except HttpServiceError as exc:
                return _response(
                    start_response, exc.status,
                    {"error": {"code": exc.code, "message": exc.message}},
                    request_id,
                )
            except (ExecutionConflict, EventIdConflict) as exc:
                return _response(
                    start_response, 409,
                    {"error": {"code": "IDEMPOTENCY_OR_STATE_CONFLICT", "message": str(exc)[:240]}},
                    request_id,
                )
            except (ValueError, TypeError) as exc:
                return _response(
                    start_response, 422,
                    {"error": {"code": "INVALID_PROTOCOL_INSTRUCTION", "message": str(exc)[:240]}},
                    request_id,
                )
            except Exception:
                return _response(
                    start_response, 500,
                    {"error": {"code": "INTERNAL_ERROR", "message": "The protocol instruction could not be completed."}},
                    request_id,
                )
        if path == "/demo/instructions":
            if method != "POST":
                return _response(
                    start_response, 405,
                    {"error": {"code": "METHOD_NOT_ALLOWED", "message": "Use POST for this demo route."}},
                    request_id,
                )
            try:
                payload = self._read_json(environ)
                result = self._run_instruction(payload)
                return _response(
                    start_response, 200,
                    {
                        "status": "COMPLETED_WITH_SYNTHETIC_ADAPTERS",
                        "production_ready": False,
                        "result": result,
                        "protocol_boundary": {
                            "minah": "DETERMINISTIC_MOCK_ADVISORY_ONLY",
                            "ivm": "NOT_WIRED_AS_SEPARATE_RUNTIME",
                            "cee": "NOT_WIRED_AS_SEPARATE_LIFECYCLE_ENGINE",
                            "institutional_workflow": "LOCAL_SQLITE_REFERENCE_PROTOTYPE",
                            "payment": "SIMULATED_NO_FUNDS_MOVED",
                        },
                    },
                    request_id,
                )
            except HttpServiceError as exc:
                return _response(
                    start_response, exc.status,
                    {"error": {"code": exc.code, "message": exc.message}},
                    request_id,
                )
            except PermissionError as exc:
                return _response(
                    start_response, 403,
                    {"error": {"code": "SYNTHETIC_AUTHORITY_DENIED", "message": str(exc)}},
                    request_id,
                )
            except (ValueError, TypeError) as exc:
                return _response(
                    start_response, 422,
                    {"error": {"code": "INVALID_DEMO_INSTRUCTION", "message": str(exc)[:240]}},
                    request_id,
                )
            except Exception:
                # Never send stack traces, database paths, or adapter internals to clients.
                return _response(
                    start_response, 500,
                    {"error": {"code": "INTERNAL_ERROR", "message": "The demo instruction could not be completed."}},
                    request_id,
                )
        if path.startswith("/system/") or path.startswith("/demo/") or path.startswith("/protocol/"):
            return _response(
                start_response, 404,
                {"error": {"code": "ROUTE_NOT_FOUND", "message": "No implemented prototype route matches this path."}},
                request_id,
            )
        return _response(
            start_response, 404,
            {"error": {"code": "ROUTE_NOT_FOUND", "message": "No route matches this path."}},
            request_id,
        )

    @staticmethod
    def _read_json(environ: dict[str, Any], allowed_fields: set[str] | None = None) -> dict[str, Any]:
        content_type = environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise HttpServiceError(415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json.")
        raw_length = environ.get("CONTENT_LENGTH", "")
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            raise HttpServiceError(400, "INVALID_CONTENT_LENGTH", "A valid Content-Length is required.")
        if length < 0:
            raise HttpServiceError(400, "INVALID_CONTENT_LENGTH", "Content-Length must not be negative.")
        if length > MAX_BODY_BYTES:
            raise HttpServiceError(413, "REQUEST_TOO_LARGE", f"Request body must not exceed {MAX_BODY_BYTES} bytes.")
        stream = environ.get("wsgi.input")
        raw = stream.read(length) if stream is not None else b""
        if len(raw) != length:
            raise HttpServiceError(400, "INCOMPLETE_REQUEST_BODY", "Request body length does not match Content-Length.")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HttpServiceError(400, "INVALID_JSON", "Request body must be valid UTF-8 JSON.")
        if not isinstance(payload, dict):
            raise HttpServiceError(422, "JSON_OBJECT_REQUIRED", "Request body must be a JSON object.")
        unknown = sorted(set(payload) - (allowed_fields if allowed_fields is not None else _ALLOWED_FIELDS))
        if unknown:
            raise HttpServiceError(422, "UNKNOWN_FIELDS", "Unsupported fields: " + ", ".join(unknown))
        return payload

    def _run_instruction(self, payload: dict[str, Any]) -> dict[str, Any]:
        consignment_id = payload.get("consignment_id")
        if not isinstance(consignment_id, str) or not _ID_RE.fullmatch(consignment_id):
            raise HttpServiceError(
                422, "INVALID_CONSIGNMENT_ID",
                "consignment_id is required and must be 1-120 safe identifier characters.",
            )
        for field in ("origin_decision", "destination_decision"):
            if field in payload and payload[field] not in {"APPROVE", "REJECT"}:
                raise HttpServiceError(422, "INVALID_DECISION", f"{field} must be APPROVE or REJECT.")
        if "declared_value_ugx" in payload and (
            type(payload["declared_value_ugx"]) is not int or payload["declared_value_ugx"] < 0
        ):
            raise HttpServiceError(422, "INVALID_DECLARED_VALUE", "declared_value_ugx must be a non-negative integer.")
        for field in ("edge_connected", "edge_reconnect", "edge_authority_active", "exempt"):
            if field in payload and type(payload[field]) is not bool:
                raise HttpServiceError(422, "INVALID_BOOLEAN", f"{field} must be a JSON boolean.")
        if "minah_evidence" in payload and not isinstance(payload["minah_evidence"], dict):
            raise HttpServiceError(422, "INVALID_EVIDENCE", "minah_evidence must be a JSON object.")
        for field in ("edge_current_evidence_version", "edge_current_rule_version"):
            if field in payload and (not isinstance(payload[field], str) or not payload[field]):
                raise HttpServiceError(422, "INVALID_VERSION", f"{field} must be a non-empty string.")
        return self.demonstrator.run_case(**payload)


def create_app(
    demonstrator: IntegratedDemonstrator | None = None,
    pipeline: KinSysProtocolPipeline | None = None,
) -> KinSysPrototypeApplication:
    return KinSysPrototypeApplication(demonstrator, pipeline)


def main() -> None:
    host = os.environ.get("KIN_SYS_HOST", "127.0.0.1")
    port_text = os.environ.get("KIN_SYS_PORT", "8080")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise SystemExit("KIN_SYS_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("KIN_SYS_PORT must be between 1 and 65535")
    data_dir = Path(os.environ.get("KIN_SYS_DATA_DIR", ".kin-sys-prototype"))
    cee_database = os.environ.get("KIN_SYS_CEE_DB", str(data_dir / "cee.sqlite3"))
    ivm_database = os.environ.get("KIN_SYS_IVM_DB", str(data_dir / "ivm.sqlite3"))
    for database in (cee_database, ivm_database):
        if database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    pipeline = KinSysProtocolPipeline(
        cee_database=cee_database,
        ivm_database=ivm_database,
    )
    app = create_app(pipeline=pipeline)
    for database in (cee_database, ivm_database):
        if database != ":memory:" and Path(database).exists():
            try:
                os.chmod(database, 0o600)
            except OSError:
                pass
    print(f"KIN-SYS local synthetic prototype listening on http://{host}:{port}")
    print(f"CEE journal: {cee_database}; IVM store: {ivm_database}")
    print("WARNING: this is not a production API; do not expose it to the public internet.")
    try:
        with make_server(host, port, app) as server:
            server.serve_forever()
    finally:
        app.close()


if __name__ == "__main__":
    main()
