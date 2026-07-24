# Smoke-test a built release the way a new user meets it: unzip it somewhere
# clean, run the exe, and check the app actually comes up.
#
# This exists because v0.7.3 shipped broken. Everything had been verified from
# source, and the version metadata and zip contents were checked - but the
# packaged exe was never launched, so a first-run bug that left the app with an
# empty model picker reached users. Building without running the artifact is the
# specific mistake this guards.
#
#   .\verify_release.ps1                 # verify the zip matching APP_VERSION
#   .\verify_release.ps1 -Zip path.zip   # verify a specific archive
param(
    [string]$Zip = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$match = Select-String -Path "server\config.py" -Pattern 'APP_VERSION = "([^"]+)"'
if (-not $match) { throw "APP_VERSION not found in server\config.py" }
$version = $match.Matches[0].Groups[1].Value
if (-not $Zip) { $Zip = "release\SyrudasAI-v$version-win64.zip" }
if (-not (Test-Path $Zip)) { throw "archive not found: $Zip (run .\build_release.ps1 first)" }

$base = "http://127.0.0.1:8040"

# The exe serves on a fixed port. If something is already there we would be
# testing THAT process and calling the artifact good on someone else's output -
# exactly the confusion this script exists to prevent.
$busy = Get-NetTCPConnection -LocalPort 8040 -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    throw "port 8040 is already in use (PID $($busy[0].OwningProcess)). Close the running Syrudas AI first - otherwise this would test that instance instead of the new build."
}

$work = Join-Path $env:TEMP ("syrudas-verify-" + [System.IO.Path]::GetRandomFileName())
$app = Join-Path $work "SyrudasAI"
$proc = $null
$failures = @()

function Check($name, $ok, $detail) {
    if ($ok) {
        Write-Host ("  {0,-42} PASS" -f $name) -ForegroundColor Green
    } else {
        Write-Host ("  {0,-42} FAIL  {1}" -f $name, $detail) -ForegroundColor Red
        $script:failures += $name
    }
}

function Get-Json($path) {
    $r = Invoke-WebRequest -Uri ($base + $path) -TimeoutSec 8 -UseBasicParsing
    return $r.Content | ConvertFrom-Json
}

function Start-App {
    $p = Start-Process -FilePath (Join-Path $app "SyrudasAI.exe") -WorkingDirectory $app -PassThru
    # wait for the server rather than sleeping a fixed guess
    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep -Seconds 1
        try {
            $h = Get-Json "/api/health"
            if ($h.ok) { return $p }
        } catch { }
    }
    throw "the app did not answer on $base within 45s"
}

function Stop-App {
    Get-Process SyrudasAI -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($work) } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

try {
    Write-Host ""
    Write-Host "Verifying $Zip" -ForegroundColor Cyan
    New-Item -ItemType Directory $work -Force | Out-Null
    Expand-Archive -Path $Zip -DestinationPath $work -Force

    Check "archive contains SyrudasAI\SyrudasAI.exe" (Test-Path (Join-Path $app "SyrudasAI.exe")) "missing"
    if ($failures.Count) { throw "archive layout is wrong; stopping" }

    $vi = (Get-Item (Join-Path $app "SyrudasAI.exe")).VersionInfo
    Check "exe metadata says $version" ($vi.ProductVersion -eq $version) "got '$($vi.ProductVersion)'"

    # ---- cold start, the way a user meets it ----
    $proc = Start-App
    $health = Get-Json "/api/health"
    Check "server answers /api/health" ($health.ok -eq $true) "ok=$($health.ok)"
    Check "reports version $version" ($health.version -eq $version) "got '$($health.version)'"

    $index = Invoke-WebRequest -Uri $base -TimeoutSec 8 -UseBasicParsing
    Check "serves the built frontend" ($index.StatusCode -eq 200 -and $index.Content -match '<div id="root"') "status=$($index.StatusCode)"
    # a bundle missing from the exe still returns index.html, so follow the script tag
    $asset = [regex]::Match($index.Content, 'src="(/assets/[^"]+\.js)"')
    Check "serves the JS bundle" $asset.Success "no /assets/*.js referenced by index.html"
    if ($asset.Success) {
        $js = Invoke-WebRequest -Uri ($base + $asset.Groups[1].Value) -TimeoutSec 8 -UseBasicParsing
        Check "JS bundle downloads" ($js.StatusCode -eq 200 -and $js.RawContentLength -gt 10000) "status=$($js.StatusCode) len=$($js.RawContentLength)"
    }

    Check "/api/providers responds" ((Get-Json "/api/providers") -ne $null) "no response"

    # ---- first-run recovery (the v0.7.3 regression) ----
    # Only meaningful when a local backend is actually running: with nothing to
    # find, "no providers" is the correct answer and proves nothing.
    $backendUp = $false
    foreach ($u in @("http://localhost:11434/v1/models", "http://localhost:1234/v1/models")) {
        try {
            $r = Invoke-WebRequest -Uri $u -TimeoutSec 3 -UseBasicParsing
            if ($r.StatusCode -eq 200) { $backendUp = $true; break }
        } catch { }
    }

    if (-not $backendUp) {
        Write-Host "  first-run recovery                         SKIP  (no local backend running)" -ForegroundColor Yellow
    } else {
        Stop-App
        # recreate the state a user is left in after a first launch with no
        # backend: nothing configured, detection never succeeded
        $py = ".\.venv\Scripts\python.exe"
        if (-not (Test-Path $py)) { $py = "python" }
        $db = Join-Path $app "data\syrudas.db"
        # via a temp script, not python -c: PowerShell re-quotes arguments on the
        # way to a native exe and mangles embedded double quotes
        $script = Join-Path $work "reset_state.py"
        @'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute("DELETE FROM provider_instances")
c.execute("DELETE FROM settings WHERE key = ?", ("auto_detect_done",))
c.commit()
c.close()
'@ | Set-Content -Path $script -Encoding utf8
        & $py $script $db
        if ($LASTEXITCODE -ne 0) { throw "could not reset the database for the recovery check" }
        $proc = Start-App
        $provs = Get-Json "/api/providers"
        Check "recovers after a backend appears" (@($provs).Count -gt 0) "still no providers - detection did not re-run"
    }
}
finally {
    Stop-App
    if (Test-Path $work) { Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue }
}

Write-Host ""
if ($failures.Count) {
    Write-Host "Release verification FAILED: $($failures -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host "Release verified - the packaged app starts and serves the UI." -ForegroundColor Green
