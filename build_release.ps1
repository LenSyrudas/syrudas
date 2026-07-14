# Package a shippable portable release: release\SyrudasAI-vX.Y.Z-win64.zip
# Reads the version from server\config.py, builds the exe, and zips it with
# the end-user README and LICENSE.
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
Copy-Item "packaging\README.txt" $appdir
if (Test-Path "docs\Syrudas-AI-Whitepaper.pdf") {
    Copy-Item "docs\Syrudas-AI-Whitepaper.pdf" $appdir
}
if (Test-Path "docs\SETUP.md") {
    Copy-Item "docs\SETUP.md" (Join-Path $appdir "SETUP.txt")
}
# optional provider connectors (Anthropic, Gemini, ...) ship as drop-in
# plugins next to the exe - configure with an API key in Settings to activate
New-Item -ItemType Directory (Join-Path $appdir "plugins") | Out-Null
Copy-Item "plugins\*.py" (Join-Path $appdir "plugins")

New-Item -ItemType Directory "release" -Force | Out-Null
$zip = "release\SyrudasAI-v$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
# archive the folder itself (not its contents) so the zip has a single
# top-level "SyrudasAI\" entry
Compress-Archive -Path $appdir -DestinationPath $zip
Remove-Item $stage -Recurse -Force

Write-Host ""
Write-Host "Release ready: $root\$zip"
