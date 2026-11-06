$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not $env:KIN_SYS_SANDBOX_DATA_DIR) {
    $env:KIN_SYS_SANDBOX_DATA_DIR = Join-Path $Root "sandbox-data"
}

$BundledPython = Join-Path $Root "runtime/python.exe"
if (Test-Path $BundledPython) {
    $PythonCommand = $BundledPython
    $PythonArgs = @("-I", "sandbox/portable_server.py")
    $Version = & $PythonCommand -I -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
    Write-Host "Using bundled isolated Python runtime $Version"
} else {
    $PythonLauncher = Get-Command py -ErrorAction SilentlyContinue
    if (-not $PythonLauncher) {
        throw "No bundled runtime found. Use the self-contained USB bundle, or install Python 3.11+."
    }
    $Version = & py -3 -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
    $Parts = $Version.Split('.')
    if ([int]$Parts[0] -lt 3 -or ([int]$Parts[0] -eq 3 -and [int]$Parts[1] -lt 11)) {
        throw "Python 3.11 or newer is required. Found $Version."
    }
    $PythonCommand = "py"
    $PythonArgs = @("-3", "sandbox/portable_server.py")
    Write-Host "Using system Python $Version"
}

Write-Host "Starting KIN-SYS local test sandbox..."
Write-Host "Dashboard: http://127.0.0.1:8765"
Write-Host "Data: $env:KIN_SYS_SANDBOX_DATA_DIR"
& $PythonCommand @PythonArgs
