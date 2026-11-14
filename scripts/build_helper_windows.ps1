# Build the native capture helper with the .NET SDK. Mirrors scripts/build_helper.sh's
# role for macOS: produces native/windows/build/lmr-capture-helper.exe, which
# storage.paths.resolve_helper_binary() looks for in dev mode.
#
# Requires: .NET 9 SDK (`winget install Microsoft.DotNet.SDK.9` or
# https://dotnet.microsoft.com/download/dotnet/9.0), Windows 10 build 19041+ to actually
# RUN the output (see native/windows/README.md for why; this script itself can build on
# older Windows, it is only the DXGI Desktop Duplication + Media Foundation calls at
# runtime that need 19041+).
#
# This script has only been run on non-Windows during development (see
# native/windows/README.md) — `dotnet build`/`dotnet publish` succeeded there, but nothing
# about actually producing and running a working .exe on real Windows has been verified.

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ProjectDir = Join-Path $RepoRoot "native\windows\LocalMeetingRecorder.Capture"
$OutDir = Join-Path $RepoRoot "native\windows\build"

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    Write-Error "dotnet not found. Install the .NET 9 SDK: https://dotnet.microsoft.com/download/dotnet/9.0"
    exit 1
}

Write-Host "==> dotnet $(dotnet --version)"
Write-Host "==> publishing $ProjectDir -> $OutDir"

dotnet publish $ProjectDir `
    --configuration Release `
    --runtime win-x64 `
    --self-contained false `
    --output $OutDir `
    -p:UseAppHost=true

$ExePath = Join-Path $OutDir "lmr-capture-helper.exe"
if (-not (Test-Path $ExePath)) {
    Write-Error "expected $ExePath to exist after publish"
    exit 1
}

Write-Host "==> built $ExePath"
& $ExePath --version
