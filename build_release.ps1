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
New-Item -ItemType Directory $stage | Out-Null

Copy-Item "SyrudasAI.exe" $stage
Copy-Item "LICENSE" (Join-Path $stage "LICENSE.txt")
Copy-Item "packaging\README.txt" $stage
if (Test-Path "docs\Syrudas-AI-Whitepaper.pdf") {
    Copy-Item "docs\Syrudas-AI-Whitepaper.pdf" $stage
}

New-Item -ItemType Directory "release" -Force | Out-Null
$zip = "release\SyrudasAI-v$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "$stage\*" -DestinationPath $zip
Remove-Item $stage -Recurse -Force

Write-Host ""
Write-Host "Release ready: $root\$zip"
