#!/usr/bin/env python3
"""Loopback-only portable dashboard for the KIN-SYS synthetic reference slice.

No external identity, legal, AI, payment, or institutional service is contacted.
The app persists only local SQLite state under the selected sandbox data folder.
"""
from __future__ import annotations

import ipaddress
import os
import sys
from pathlib import Path
from typing import Any, Callable
from wsgiref.simple_server import make_server

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_REFERENCE = ROOT / "protocol-reference"
WEB_ROOT = Path(__file__).resolve().parent / "web"
if str(PROTOCOL_REFERENCE) not in sys.path:
    sys.path.insert(0, str(PROTOCOL_REFERENCE))

from http_service import create_app  # noqa: E402
from protocol_pipeline import KinSysProtocolPipeline  # noqa: E402


class PortableSandboxApplication:
    """Serve a small operator dashboard and delegate API routes to the reference app."""

    def __init__(self, api_app: Any, web_root: Path = WEB_ROOT):
        self.api_app = api_app
        self.web_root = web_root.resolve()
        self.index_file = self.web_root / "index.html"

    def close(self) -> None:
        self.api_app.close()

    def __call__(self, environ: dict[str, Any], start_response: Callable):
        path = environ.get("PATH_INFO", "/")
        method = environ.get("REQUEST_METHOD", "GET").upper()
        if path in {"/", "/dashboard"}:
            if method != "GET":
                body = b"Method not allowed"
                start_response("405 Method Not Allowed", [
                    ("Content-Type", "text/plain; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                    ("Cache-Control", "no-store"),
                ])
                return [body]
            try:
                body = self.index_file.read_bytes()
            except OSError:
                body = b"Dashboard assets are missing. Restore sandbox/web/index.html."
                start_response("503 Service Unavailable", [
                    ("Content-Type", "text/plain; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                    ("Cache-Control", "no-store"),
                ])
                return [body]
            start_response("200 OK", [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
                ("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'"),
                ("X-Content-Type-Options", "nosniff"),
                ("Referrer-Policy", "no-referrer"),
                ("X-Frame-Options", "DENY"),
            ])
            return [body]
        return self.api_app(environ, start_response)


def create_sandbox_app(data_dir: str | Path | None = None) -> PortableSandboxApplication:
    """Create a persistent local instance using the repository's synthetic adapters."""
    folder = Path(data_dir) if data_dir is not None else Path(
        os.environ.get("KIN_SYS_SANDBOX_DATA_DIR", ROOT / "sandbox-data")
    )
    folder = folder.expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(folder, 0o700)
    except OSError:
        # Some removable FAT/exFAT volumes do not implement POSIX permissions.
        pass

    pipeline = KinSysProtocolPipeline(
        cee_database=str(folder / "cee.sqlite3"),
        ivm_database=str(folder / "ivm.sqlite3"),
    )
    api_app = create_app(
        pipeline=pipeline,
        ingress_database=str(folder / "ingress.sqlite3"),
        allow_unauthenticated_loopback=True,
    )
    for database in (folder / "cee.sqlite3", folder / "ivm.sqlite3", folder / "ingress.sqlite3"):
        if database.exists():
            try:
                os.chmod(database, 0o600)
            except OSError:
                pass
    return PortableSandboxApplication(api_app)


def main() -> None:
    host = "127.0.0.1"  # Deliberately never bind to a LAN/public interface.
    try:
        port = int(os.environ.get("KIN_SYS_SANDBOX_PORT", "8765"))
    except ValueError as exc:
        raise SystemExit("KIN_SYS_SANDBOX_PORT must be an integer.") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("KIN_SYS_SANDBOX_PORT must be between 1 and 65535.")
    if not ipaddress.ip_address(host).is_loopback:
        raise SystemExit("The portable sandbox must bind to loopback only.")

    app = create_sandbox_app()
    data_dir = Path(os.environ.get("KIN_SYS_SANDBOX_DATA_DIR", ROOT / "sandbox-data"))
    print("KIN-SYS PORTABLE TEST SANDBOX")
    print(f"Dashboard: http://{host}:{port}")
    print(f"Persistent local data: {data_dir.resolve()}")
    print("Mode: synthetic adapters; local SQLite; no external effects.")
    print("Do not expose this service to a network or use real personal/financial data.")
    print("Press Ctrl+C to stop.")
    try:
        with make_server(host, port, app) as server:
            server.serve_forever()
    except OSError as exc:
        raise SystemExit(f"Could not start the local sandbox on port {port}: {exc}") from exc
    except KeyboardInterrupt:
        print("\nStopping KIN-SYS sandbox.")
    finally:
        app.close()


if __name__ == "__main__":
    main()
