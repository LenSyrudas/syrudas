# Syrudas AI setup: create venv, install backend deps, build frontend.
# Requires: Python 3.13 (py launcher), Node.js + npm.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path ".venv")) {
    Write-Host "Creating Python 3.13 venv..."
    py -3.13 -m venv .venv
}

Write-Host "Installing backend dependencies..."
& .\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt

Write-Host "Installing and building frontend..."
Set-Location web
npm install
npm run build
Set-Location $root

Write-Host ""
Write-Host "Setup complete. Start Syrudas AI with: .\run.ps1"
