# Windows x64 portable bundle

The portable bundle is designed for Windows 64-bit test computers that do not have Python installed.

## Build

From a Windows PowerShell session with internet access, run:

```powershell
.\scripts\package-portable-windows.ps1
```

The build downloads the official CPython 3.14.10 Windows x64 embeddable distribution from python.org, verifies the official Python.org archive SHA-256 against the release page, checks that the runtime can import the standard-library modules required by KIN-SYS, stages the repository without local databases or Git history, and creates:

- `dist/KIN-SYS-Portable-Windows-x64.zip`
- `dist/KIN-SYS-Portable-Windows-x64.zip.sha256`

The archive contains its own runtime and does not require Python or third-party packages on the destination computer. The runtime is isolated from the machine's normal Python installation and user site-packages. The application still uses the existing synthetic adapters.

## Use on the USB drive

1. Copy the ZIP and SHA-256 file to the USB drive.
2. Verify the archive checksum in PowerShell:

   ```powershell
   Get-FileHash .\KIN-SYS-Portable-Windows-x64.zip -Algorithm SHA256
   ```

   Compare the displayed hash with the value in the accompanying `.sha256` file.
3. Extract the complete ZIP to a writable folder on the USB drive.
4. Double-click `START-KIN-SYS.bat`.
5. Open `http://127.0.0.1:8765` if the browser does not open automatically.
6. Keep the console window open during the test. Press Ctrl+C to stop the server.

The default SQLite data directory is `sandbox-data` beside the launcher. Delete that folder only after stopping the server to reset the local demo records.

## Runtime provenance

The bundled runtime is the official CPython Windows x64 embeddable distribution from python.org. It is not a custom interpreter build. The archive's `BUILD-INFO.txt` records the source commit and runtime version. The packaging workflow also emits a SHA-256 checksum for the complete bundle.

## Scope and safety

This is a portable application bundle, not a hardened operating-system sandbox. It does not provide VM/container isolation, application allowlisting, endpoint protection, or protection against a compromised host. It binds to loopback only and uses synthetic identity/authority adapters and local SQLite. Never enter real personal data, credentials, confidential business records, or production financial data.
