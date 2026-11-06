# KIN-SYS Portable Test Sandbox

**Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see the repository LICENSE and NOTICE.**

This folder adds a small local operator dashboard around the repository's existing runnable MINAH-first reference path. It is intended for controlled demonstrations and repeatable integration testing from a removable drive.

## What it runs

- A local dashboard at `http://127.0.0.1:8765`.
- The existing governed instruction endpoint: `POST /protocol/instructions`.
- The existing operation inspection endpoint: `GET /protocol/operations/{operation_id}`.
- The existing system health, readiness and capability endpoints.
- Persistent local SQLite stores in `sandbox-data/` (outside the source folder).

The execution path is the repository's current synthetic MINAH-first ingress and CEE/IVM pipeline. The dashboard builds a governed v0.1 instruction envelope, computes its canonical SHA-256 payload digest, submits it and displays the final decision and trace.

## Requirements

- Python 3.11 or newer.
- No third-party Python packages.
- No internet connection is needed after the source files are on the removable drive.

## Start on Windows

From the extracted repository folder, open PowerShell and run:

```powershell
.\sandbox\run-windows.ps1
```

Or run directly:

```powershell
py -3 sandbox\portable_server.py
```

Then open `http://127.0.0.1:8765` in a browser.

## Start on Linux / macOS

```sh
chmod +x sandbox/run-unix.sh
./sandbox/run-unix.sh
```

Then open `http://127.0.0.1:8765`.

## Data and reset

The default data directory is `sandbox-data/` at the repository root. It holds local SQLite state and is deliberately excluded from version control. To use another location, set `KIN_SYS_SANDBOX_DATA_DIR` before starting the server.

To reset the sandbox, stop the server and delete `sandbox-data/`. This permanently deletes the local demonstration records.

## Included test scenarios

1. **Submit instruction:** uses the synthetic local identity, authority and MINAH adapters and runs the governed instruction pipeline.
2. **Test authority rejection:** submits a non-authorised synthetic principal and demonstrates fail-closed authority evaluation.
3. **Inspect operation:** retrieves the persisted operation record using its operation ID.

The origin/destination labels are illustrative scenario fields. This release runs one synthetic jurisdiction profile (`UG-DEMO`); it does not yet coordinate two independently deployed national or institutional MINAH instances.

## Safety and limits

- The server binds only to `127.0.0.1`; it must not be exposed to a LAN or the public internet.
- All identity and authority decisions use synthetic reference adapters. They do not authenticate real people, institutions or mandates.
- MINAH is a deterministic mock, not a live AI service or legal oracle.
- CEE and IVM use separate local SQLite stores. This prototype does not claim distributed atomic commit or distributed finality.
- No real payment, customs clearance, title transfer, financial settlement or other external effect is performed.
- Do not enter real personal data, credentials, confidential business information or production financial records.
- A `PASSED` decision means only that the configured synthetic gates passed for the supplied test instruction.

This is a controlled test harness around a research reference implementation, not a production deployment or a security-certified container sandbox.
