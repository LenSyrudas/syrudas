# Build SyrudasAI.exe (native desktop app, windowed) into the project root.
# Native calls go through cmd /c with 2>&1: under ErrorActionPreference=Stop,
# PowerShell 5.1 otherwise turns harmless stderr log lines (vite warnings,
# PyInstaller INFO) into terminating errors when streams are redirected.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "Building frontend..."
cmd /c "cd /d $root\web && npm run build 2>&1"
if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }

Write-Host "Installing build dependencies..."
cmd /c ".\.venv\Scripts\python.exe -m pip install --quiet pyinstaller pywebview 2>&1"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "Building exe..."
cmd /c ".\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name SyrudasAI --icon icon.ico --version-file version_info.txt --add-data ""web/dist;web/dist"" --collect-submodules uvicorn --collect-all webview --exclude-module PIL desktop.py 2>&1"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Copy-Item dist\SyrudasAI.exe $root -Force
Write-Host ""
Write-Host "Done: $root\SyrudasAI.exe  (double-click to launch the desktop app)"
Write-Host "Logs when windowed: data\syrudas.log"
