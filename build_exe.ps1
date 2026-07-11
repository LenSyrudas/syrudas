# Build SyrudasAI.exe (native desktop app, windowed) into the project root.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "Building frontend..."
Set-Location web
npm run build
Set-Location $root

Write-Host "Installing build dependencies..."
& .\.venv\Scripts\python.exe -m pip install --quiet pyinstaller pywebview

Write-Host "Building exe..."
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name SyrudasAI `
    --icon icon.ico `
    --add-data "web/dist;web/dist" `
    --collect-submodules uvicorn `
    --collect-all webview `
    desktop.py

Copy-Item dist\SyrudasAI.exe $root -Force
Write-Host ""
Write-Host "Done: $root\SyrudasAI.exe  (double-click to launch the desktop app)"
Write-Host "Logs when windowed: data\syrudas.log"
