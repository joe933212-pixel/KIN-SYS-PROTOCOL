[CmdletBinding()]
param(
    [string]$OutputDirectory = "dist"
)

$ErrorActionPreference = "Stop"
$PythonVersion = "3.14.10"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$OutputPath = Join-Path $RepoRoot $OutputDirectory
$TempRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
if (-not $TempRoot) { throw "Could not determine a temporary build directory." }
$StageRoot = Join-Path $TempRoot "kin-sys-portable-build"
$BundleRoot = Join-Path $StageRoot "KIN-SYS-Portable-Windows-x64"

if (Test-Path $StageRoot) { Remove-Item $StageRoot -Recurse -Force }
New-Item -ItemType Directory -Path $BundleRoot -Force | Out-Null
New-Item -ItemType Directory -Path $OutputPath -Force | Out-Null

Write-Host "Preparing KIN-SYS portable bundle..."
$ExcludedDirectories = @(".git", ".venv", "venv", "sandbox-data", "__pycache__", ".pytest_cache", ".mypy_cache", "dist")
$RobocopyArgs = @($RepoRoot, $BundleRoot, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/XF", "*.pyc", "*.pyo", "*.sqlite3", "*.db", "/XD") + $ExcludedDirectories
& robocopy @RobocopyArgs | Out-Null
$CopyCode = $LASTEXITCODE
if ($CopyCode -ge 8) { throw "Repository staging failed (robocopy exit code $CopyCode)." }

$RuntimeDirectory = Join-Path $BundleRoot "runtime"
New-Item -ItemType Directory -Path $RuntimeDirectory -Force | Out-Null
$RuntimeArchive = Join-Path $TempRoot "python-$PythonVersion-embed-amd64.zip"
$RuntimeUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"

Write-Host "Downloading official CPython $PythonVersion Windows x64 embeddable distribution..."
Invoke-WebRequest -Uri $RuntimeUrl -OutFile $RuntimeArchive
$ExpectedRuntimeSha256 = "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159"
$ActualRuntimeSha256 = (Get-FileHash -LiteralPath $RuntimeArchive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualRuntimeSha256 -ne $ExpectedRuntimeSha256) {
    throw "The CPython archive SHA-256 does not match the official Python.org release checksum."
}
Expand-Archive -LiteralPath $RuntimeArchive -DestinationPath $RuntimeDirectory -Force

$EmbeddedPython = Join-Path $RuntimeDirectory "python.exe"
if (-not (Test-Path $EmbeddedPython)) { throw "The official Python archive did not contain python.exe." }

$Probe = & $EmbeddedPython -I -c "import hashlib, json, sqlite3, wsgiref.simple_server, urllib.request; print('runtime-ok')"
if ($LASTEXITCODE -ne 0 -or $Probe -notcontains "runtime-ok") {
    throw "The bundled Python runtime failed its standard-library capability check."
}

$StartScript = @'
@echo off
setlocal
cd /d "%~dp0"
set "KIN_SYS_SANDBOX_DATA_DIR=%~dp0sandbox-data"
set "KIN_SYS_SANDBOX_PORT=8765"
if not exist "runtime\python.exe" (
  echo The bundled Python runtime is missing. Re-extract the complete ZIP archive.
  pause
  exit /b 1
)
echo Starting KIN-SYS Portable Test Sandbox...
echo Dashboard: http://127.0.0.1:8765
echo Local data: "%KIN_SYS_SANDBOX_DATA_DIR%"
echo Use Ctrl+C in this window to stop the sandbox.
echo.
start "" /B powershell.exe -NoProfile -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8765'"
"runtime\python.exe" -I "sandbox\portable_server.py"
if errorlevel 1 (
  echo.
  echo KIN-SYS stopped with an error. Review the message above.
  pause
)
endlocal
'@
Set-Content -LiteralPath (Join-Path $BundleRoot "START-KIN-SYS.bat") -Value $StartScript -Encoding ASCII

$BuildCommit = if ($env:GITHUB_SHA) { $env:GITHUB_SHA } else { "local-build" }
$BuildInfo = @"
KIN-SYS Portable Windows x64
Repository: joe933212-pixel/KIN-SYS-PROTOCOL
Source commit: $BuildCommit
Bundled CPython: $PythonVersion (official python.org embeddable distribution)
Runtime mode: isolated embeddable distribution; standard library only
Build UTC: $([DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ"))

This bundle contains synthetic test adapters only. It is not a production deployment.
"@
Set-Content -LiteralPath (Join-Path $BundleRoot "BUILD-INFO.txt") -Value $BuildInfo -Encoding UTF8

$BundleReadme = @'
KIN-SYS PORTABLE TEST SANDBOX — WINDOWS x64

1. Extract the entire ZIP archive to a writable folder on your USB drive.
2. Double-click START-KIN-SYS.bat.
3. The dashboard opens at http://127.0.0.1:8765.
4. Keep the console window open while testing. Press Ctrl+C there to stop.
5. Synthetic test records are stored in sandbox-data beside this launcher.
6. To reset the demo, stop the server and delete sandbox-data.

This bundle includes its own isolated CPython runtime. Python does not need to
be installed on the test computer. No internet access is needed after extraction.

SAFETY
- The service binds only to 127.0.0.1. Do not change this to a LAN/public bind.
- Use synthetic identities, authorities, amounts and business records only.
- A PASSED decision is only a result from the synthetic reference rules.
- No real identity registry, legal source, bank, payment rail or customs system
  is contacted. No real-world transaction or settlement takes place.
- This is an application-level local test harness, not a hardened VM/container
  or security-certified isolation boundary. Do not use confidential or sensitive
  production data.
- Windows may display a security prompt for files copied from the internet.
  Verify the archive checksum supplied alongside the ZIP before use.
'@
Set-Content -LiteralPath (Join-Path $BundleRoot "PORTABLE-README.txt") -Value $BundleReadme -Encoding UTF8

$ZipPath = Join-Path $OutputPath "KIN-SYS-Portable-Windows-x64.zip"
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Compress-Archive -Path (Join-Path $BundleRoot "*") -DestinationPath $ZipPath -CompressionLevel Optimal

$Hash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$ZipPath.sha256" -Value "$Hash  KIN-SYS-Portable-Windows-x64.zip" -Encoding ASCII

Write-Host "Portable bundle created: $ZipPath"
Write-Host "SHA-256: $Hash"
