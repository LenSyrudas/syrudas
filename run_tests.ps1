# Syrudas AI test runner: offline Python suites, plus the frontend typecheck/lint.
# The offline suites need no network, no GPU and no running model - they drive
# the real code paths against fakes at the true boundaries (network, model).
#
#   .\run_tests.ps1              # offline suites + frontend checks
#   .\run_tests.ps1 -SkipWeb     # Python only (no Node needed)
#   .\run_tests.ps1 -Smoke       # also run smoke_*.py (needs a live model backend)
param(
    [switch]$SkipWeb,
    [switch]$Smoke
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    # CI (and a plain `pip install -r requirements.txt`) may have no venv
    $python = "python"
}

$failed = @()
$passed = 0

# Windows PowerShell wraps a native command's stderr lines as NativeCommandError
# when merged with 2>&1, which would abort the run under "Stop" even though the
# suite exited 0 (a library deprecation warning is enough). Success is decided by
# $LASTEXITCODE alone, so switch to Continue for the native calls below.
$ErrorActionPreference = "Continue"

function Invoke-Suite($name, $path) {
    Write-Host ("  {0,-26} " -f $name) -NoNewline
    # stderr is merged so a traceback is visible when a suite fails
    $out = & $python $path 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "PASS" -ForegroundColor Green
        $script:passed++
    } else {
        Write-Host "FAIL" -ForegroundColor Red
        $script:failed += $name
        $out | ForEach-Object { Write-Host "      $_" }
    }
}

Write-Host ""
Write-Host "Offline suites (no network, no model)" -ForegroundColor Cyan
Get-ChildItem "scripts\test_*.py" | Sort-Object Name | ForEach-Object {
    Invoke-Suite $_.BaseName $_.FullName
}

if ($Smoke) {
    Write-Host ""
    Write-Host "Smoke suites (require a live model backend)" -ForegroundColor Cyan
    Get-ChildItem "scripts\smoke_*.py" | Sort-Object Name | ForEach-Object {
        Invoke-Suite $_.BaseName $_.FullName
    }
}

if (-not $SkipWeb) {
    Write-Host ""
    Write-Host "Frontend" -ForegroundColor Cyan
    Set-Location web
    try {
        Write-Host ("  {0,-26} " -f "lint") -NoNewline
        $out = & npm run --silent lint 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "PASS" -ForegroundColor Green; $passed++
        } else {
            Write-Host "FAIL" -ForegroundColor Red; $failed += "lint"
            $out | ForEach-Object { Write-Host "      $_" }
        }

        Write-Host ("  {0,-26} " -f "unit (vitest)") -NoNewline
        $out = & npm run --silent test 2>&1
        if ($LASTEXITCODE -eq 0) {
            # surface the count so a suite silently collecting nothing is visible
            $summary = ($out | Select-String -Pattern "Tests\s+\d+ passed" | Select-Object -Last 1)
            Write-Host "PASS" -ForegroundColor Green -NoNewline
            if ($summary) { Write-Host ("  ({0})" -f ($summary -replace '.*Tests\s+', '' -replace '\s+$', '')) }
            else { Write-Host "" }
            $passed++
        } else {
            Write-Host "FAIL" -ForegroundColor Red; $failed += "unit"
            $out | ForEach-Object { Write-Host "      $_" }
        }

        # `npm run build` runs tsc -b first, so this covers the typecheck
        Write-Host ("  {0,-26} " -f "build (tsc + vite)") -NoNewline
        $out = & npm run --silent build 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "PASS" -ForegroundColor Green; $passed++
        } else {
            Write-Host "FAIL" -ForegroundColor Red; $failed += "build"
            $out | ForEach-Object { Write-Host "      $_" }
        }
    } finally {
        Set-Location $root
    }
}

Write-Host ""
if ($failed.Count -gt 0) {
    Write-Host "$passed passed, $($failed.Count) failed: $($failed -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host "All $passed checks passed." -ForegroundColor Green
