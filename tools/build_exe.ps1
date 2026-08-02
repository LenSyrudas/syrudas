# Build SyrudasAI.exe (native desktop app, windowed) into the project root.
# Native calls go through cmd /c with 2>&1: under ErrorActionPreference=Stop,
# PowerShell 5.1 otherwise turns harmless stderr log lines (vite warnings,
# PyInstaller INFO) into terminating errors when streams are redirected.
$ErrorActionPreference = "Stop"
# This script lives in tools\ but every path below - the venv, web\, dist\,
# desktop.py - is relative to the repository root, so $root is this file's
# grandparent and the location is set there. Anything that genuinely belongs to
# tools\ is spelled with the prefix explicitly.
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

# Same fallback run_tests.ps1 uses: a git worktree, a CI checkout, or a plain
# `pip install -r requirements.txt` has no .venv here, and hardcoding the path
# is what stops this script running anywhere but a developer's own clone.
$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

Write-Host "Building frontend..."
cmd /c "cd /d $root\web && npm run build 2>&1"
if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }

Write-Host "Installing build dependencies..."
cmd /c "$python -m pip install --quiet pyinstaller pywebview 2>&1"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# --onefile, and --onedir has already been tried. A onefile build is a
# self-extracting archive, which is the shape antivirus heuristics distrust, so
# v1.0.1 shipped as --onedir to remove that trait. Defender quarantined the
# v1.0.1 zip two seconds after it downloaded (Trojan:Script/Wacatac.B!ml) - a
# verdict the onefile v1.0.0 zip never got. One observation each way is not a
# controlled result, but onedir demonstrably did not buy what it was meant to,
# and it costs a self-contained exe. Do not switch again on reasoning alone;
# the fix for false positives is a code-signing certificate.
Write-Host "Building exe..."
cmd /c "$python -m PyInstaller --noconfirm --clean --onefile --windowed --name SyrudasAI --icon tools\icon.ico --version-file tools\version_info.txt --add-data ""web/dist;web/dist"" --collect-submodules uvicorn --collect-all webview --exclude-module PIL desktop.py 2>&1"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Copy-Item dist\SyrudasAI.exe $root -Force
Write-Host ""
Write-Host "Done: $root\SyrudasAI.exe  (double-click to launch the desktop app)"
Write-Host "Logs when windowed: data\syrudas.log"
