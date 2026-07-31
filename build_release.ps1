# Package a shippable portable release: release\SyrudasAI-vX.Y.Z-win64.zip
# Reads the version from server\config.py, builds the exe, and zips it with
# the end-user README and LICENSE.
#
# The finished archive is then smoke-tested by verify_release.ps1: unzipped
# clean and actually launched. Pass -SkipVerify to skip that (for example when
# an instance is already using port 8040), but do not ship an unverified build -
# v0.7.3 went out broken precisely because the packaged exe was never run.
param(
    [switch]$SkipVerify
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$match = Select-String -Path "server\config.py" -Pattern 'APP_VERSION = "([^"]+)"'
if (-not $match) { throw "APP_VERSION not found in server\config.py" }
$version = $match.Matches[0].Groups[1].Value
Write-Host "Packaging Syrudas AI v$version"

& .\build_exe.ps1
if (-not (Test-Path "SyrudasAI.exe")) { throw "build_exe.ps1 did not produce SyrudasAI.exe" }

$stage = Join-Path $env:TEMP "syrudas-release-stage"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
# Everything goes inside a single, version-free "SyrudasAI" folder so that
# unzipping yields a ready-to-use folder the user can drop anywhere - nothing
# to rename or reorganize, and the name stays stable across version upgrades.
$appdir = Join-Path $stage "SyrudasAI"
New-Item -ItemType Directory $appdir | Out-Null

Copy-Item "SyrudasAI.exe" $appdir
Copy-Item "LICENSE" (Join-Path $appdir "LICENSE.txt")

# stamp the version into the end-user readme rather than hand-maintaining it:
# a zip on someone's disk should say what it is without being launched.
# WriteAllText with an explicit no-BOM encoding, because Set-Content -Encoding
# utf8 on PowerShell 5.1 emits a BOM, which some editors render as "i>>?" on
# the very first line the user reads.
$readme = (Get-Content "packaging\README.txt" -Raw).Replace("{{VERSION}}", "v$version")
[System.IO.File]::WriteAllText(
    (Join-Path $appdir "README.txt"), $readme,
    (New-Object System.Text.UTF8Encoding $false))

# these were conditional, which meant a release could quietly ship without its
# documentation; a missing doc is a broken build, not an optional extra
foreach ($doc in @(
    @{ Src = "docs\Syrudas-AI-Whitepaper.pdf"; Dest = "Syrudas-AI-Whitepaper.pdf" },
    @{ Src = "docs\SETUP.md";                  Dest = "SETUP.txt" }
)) {
    if (-not (Test-Path $doc.Src)) {
        throw "$($doc.Src) is missing; release would ship without it. Regenerate it and retry."
    }
    Copy-Item $doc.Src (Join-Path $appdir $doc.Dest)
}
# optional provider connectors (Anthropic, Gemini, ...) ship as drop-in
# plugins next to the exe - configure with an API key in Settings to activate.
# Named explicitly rather than globbed: a glob also shipped example_echo.py,
# which showed up for real users as a selectable "Echo (example plugin)"
# provider that replied with their own words back.
$connectors = @("anthropic.py", "gemini.py")
New-Item -ItemType Directory (Join-Path $appdir "plugins") | Out-Null
foreach ($c in $connectors) {
    $src = Join-Path "plugins" $c
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $appdir "plugins")
    } else {
        throw "Expected connector plugins\$c is missing; release would ship without it."
    }
}

New-Item -ItemType Directory "release" -Force | Out-Null
$zip = "release\SyrudasAI-v$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
# archive the folder itself (not its contents) so the zip has a single
# top-level "SyrudasAI\" entry
Compress-Archive -Path $appdir -DestinationPath $zip
Remove-Item $stage -Recurse -Force

Write-Host ""
Write-Host "Release ready: $root\$zip"

if ($SkipVerify) {
    Write-Host ""
    Write-Host "Skipped artifact verification (-SkipVerify). Run .\verify_release.ps1 before shipping." -ForegroundColor Yellow
} else {
    & .\verify_release.ps1 -Zip $zip
    if ($LASTEXITCODE -ne 0) { throw "release verification failed - do not ship this build" }
}
