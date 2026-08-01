# Build SyrudasAI.exe (native desktop app, windowed) into the project root.
# Native calls go through cmd /c with 2>&1: under ErrorActionPreference=Stop,
# PowerShell 5.1 otherwise turns harmless stderr log lines (vite warnings,
# PyInstaller INFO) into terminating errors when streams are redirected.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
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

# --onedir, not --onefile. A onefile build is a self-extracting archive: every
# launch unpacks the whole runtime into %TEMP% and runs it from there, which is
# both slower to start and the exact shape antivirus heuristics look for -
# Defender's ML classifier flags unsigned self-extractors readily, and an
# unsigned onefile exe with no download reputation is the worst case for it.
# --onedir ships the runtime as plain files beside the exe: nothing extracts at
# run time, and there is no self-extraction pattern to trip. The trade is a
# folder instead of a single file, which costs nothing here because the release
# was always a folder inside a zip.
Write-Host "Building exe..."
cmd /c "$python -m PyInstaller --noconfirm --clean --onedir --windowed --name SyrudasAI --icon icon.ico --version-file version_info.txt --add-data ""web/dist;web/dist"" --collect-submodules uvicorn --collect-all webview --exclude-module PIL desktop.py 2>&1"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$app = Join-Path $root "dist\SyrudasAI"
if (-not (Test-Path (Join-Path $app "SyrudasAI.exe"))) {
    throw "PyInstaller reported success but $app\SyrudasAI.exe is missing"
}
# The exe is NOT copied to the project root any more. Under --onedir it cannot
# run without the _internal folder beside it, so a lone exe at the root would be
# a build that looks finished and fails on launch. It also means a packaged
# build no longer shares the repository's data\ folder - use `python desktop.py`
# or .\run.ps1 for that, which is what they were already for.
Write-Host ""
Write-Host "Done: $app\SyrudasAI.exe  (double-click to launch the desktop app)"
Write-Host "Keep _internal\ beside it - the exe will not start without it."
Write-Host "Logs when windowed: data\syrudas.log (beside the exe)"
