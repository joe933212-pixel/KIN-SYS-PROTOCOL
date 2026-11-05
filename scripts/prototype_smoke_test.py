#!/usr/bin/env python3
"""Exercise the runnable local KIN-SYS HTTP prototype as a separate process.

This is a synthetic smoke test. It uses only Python's standard library and
never contacts an external identity, authority, AI, banking, or payment service.
Run from the repository root: python scripts/prototype_smoke_test.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_REFERENCE = ROOT / "protocol-reference"
sys.path.insert(0, str(PROTOCOL_REFERENCE))

from governed_contracts import compute_payload_digest  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request(url: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def main() -> int:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    operation_id = f"SMOKE-{uuid.uuid4().hex[:16]}"
    target_ref = f"{operation_id}-TARGET"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROTOCOL_REFERENCE) + os.pathsep + env.get("PYTHONPATH", "")
    env["KIN_SYS_HOST"] = "127.0.0.1"
    env["KIN_SYS_PORT"] = str(port)

    with tempfile.TemporaryDirectory(prefix="kin-sys-smoke-") as data_dir:
        env["KIN_SYS_DATA_DIR"] = data_dir
        server_code = """
import os
from wsgiref.simple_server import make_server
from http_service import create_app
from protocol_pipeline import KinSysProtocolPipeline

class SmokeIdentity:
    def verify_principal(self, request):
        return {"verified": True, "mode": "SYNTHETIC_SMOKE_TEST"}

class SmokeAuthority:
    def verify_action(self, request):
        return {"verified": True, "mode": "SYNTHETIC_SMOKE_TEST"}

data_dir = os.environ["KIN_SYS_DATA_DIR"]
pipeline = KinSysProtocolPipeline(
    cee_database=os.path.join(data_dir, "cee.sqlite3"),
    ivm_database=os.path.join(data_dir, "ivm.sqlite3"),
    identity_adapter=SmokeIdentity(),
    authority_adapter=SmokeAuthority(),
)
app = create_app(
    pipeline=pipeline,
    ingress_database=os.path.join(data_dir, "ingress.sqlite3"),
)
try:
    with make_server(os.environ["KIN_SYS_HOST"], int(os.environ["KIN_SYS_PORT"]), app) as server:
        server.serve_forever()
finally:
    app.close()
"""
        process = subprocess.Popen(
            [sys.executable, "-c", server_code],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 15
            health = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"KIN-SYS prototype exited during startup (code {process.returncode})"
                    )
                try:
                    status, health = _request(f"{base_url}/system/health")
                    if status == 200:
                        break
                except (OSError, TimeoutError, urllib.error.URLError):
                    time.sleep(0.2)
            else:
                raise RuntimeError("KIN-SYS prototype did not become healthy within 15 seconds")

            if not isinstance(health, dict) or health.get("status") not in {"ok", "healthy", "live", "ALIVE"}:
                raise RuntimeError(f"Unexpected health response: {health!r}")

            readiness_status, readiness = _request(f"{base_url}/system/readiness")
            if readiness_status != 200 or not isinstance(readiness, dict):
                raise RuntimeError(f"Readiness endpoint failed: {readiness!r}")

            instruction = {
                "contract_version": "kin-sys-governed-contract-0.1",
                "request_id": f"request-{uuid.uuid4().hex}",
                "operation_id": operation_id,
                "idempotency_key": f"idem-{uuid.uuid4().hex}",
                "principal_ref": "SYNTHETIC-TEST-PRINCIPAL",
                "authority_ref": "authority-check:synthetic-smoke",
                "action": "ACTIVATE",
                "target_ref": target_ref,
                "purpose": "Local synthetic prototype smoke test",
                "jurisdiction": "UG-DEMO",
                "submitted_at": "2026-11-05T00:00:00Z",
                "payload_digest": "sha256:" + ("0" * 64),
                "rule_pack_version": "KIN-SYS-DEMO-RULES:1.0",
                "evidence_refs": ["evidence:synthetic-smoke"],
                "evidence": {
                    "consignment_id": target_ref,
                    "commodity": "SYNTHETIC_MAIZE",
                    "origin": "UGANDA-DEMO",
                    "destination": "BORDER-DEMO",
                    "rule_refs": ["SYNTHETIC-RULE-1"],
                },
            }
            instruction["payload_digest"] = compute_payload_digest(instruction)
            submit_status, decision = _request(
                f"{base_url}/protocol/instructions", "POST", instruction
            )
            if submit_status != 200:
                raise RuntimeError(f"Instruction submission returned HTTP {submit_status}: {decision!r}")
            if decision.get("status") != "COMMITTED":
                raise RuntimeError(f"Synthetic instruction did not commit: {decision!r}")
            if decision.get("decision", {}).get("protocol_decision") != "PASSED":
                raise RuntimeError(f"Unexpected protocol decision: {decision!r}")
            if decision.get("decision", {}).get("external_effect_status") != "NOT_ATTEMPTED":
                raise RuntimeError("Smoke test unexpectedly reports an external effect")

            inspect_status, inspected = _request(
                f"{base_url}/protocol/operations/{operation_id}"
            )
            if inspect_status != 200 or inspected.get("status") != "PASSED":
                raise RuntimeError(f"Read-only operation inspection failed: {inspected!r}")

            print("KIN-SYS local prototype smoke test: PASS")
            print("Health and readiness endpoints responded.")
            print("Governed instruction traversed MINAH-first ingress and the reference pipeline.")
            print("Operation inspection returned the stored decision.")
            print("Synthetic only: no external service was contacted and no external effect occurred.")
            return 0
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
