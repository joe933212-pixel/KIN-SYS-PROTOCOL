$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Python = Get-Command py -ErrorAction SilentlyContinue
if (-not $Python) {
    Write-Error "Python 3.11+ is required. Install Python, then run this script again."
}
$Version = & py -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$Parts = $Version.Split('.')
if ([int]$Parts[0] -lt 3 -or ([int]$Parts[0] -eq 3 -and [int]$Parts[1] -lt 11)) {
    Write-Error "Python 3.11 or newer is required. Found $Version."
}
if (-not $env:KIN_SYS_SANDBOX_DATA_DIR) {
    $env:KIN_SYS_SANDBOX_DATA_DIR = Join-Path $Root "sandbox-data"
}
Write-Host "Starting KIN-SYS local test sandbox..."
Write-Host "Dashboard: http://127.0.0.1:8765"
Write-Host "Data: $env:KIN_SYS_SANDBOX_DATA_DIR"
& py -3 sandbox\portable_server.py
