# Build SyrudasAI.exe (one-click launcher) into the project root.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "Building frontend..."
Set-Location web
npm run build
Set-Location $root

Write-Host "Installing PyInstaller..."
& .\.venv\Scripts\python.exe -m pip install --quiet pyinstaller

Write-Host "Building exe..."
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile `
    --name SyrudasAI `
    --icon icon.ico `
    --add-data "web/dist;web/dist" `
    --collect-submodules uvicorn `
    launcher.py

Copy-Item dist\SyrudasAI.exe $root -Force
Write-Host ""
Write-Host "Done: $root\SyrudasAI.exe  (double-click to launch)"
