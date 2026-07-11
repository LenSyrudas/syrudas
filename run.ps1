# Start Syrudas AI at http://127.0.0.1:8040
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path ".venv")) {
    Write-Host "No venv found - run .\setup.ps1 first."
    exit 1
}
if (-not (Test-Path "web\dist")) {
    Write-Host "Frontend not built - run .\setup.ps1 first."
    exit 1
}

Write-Host "Syrudas AI running at http://127.0.0.1:8040  (Ctrl+C to stop)"
& .\.venv\Scripts\python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8040
