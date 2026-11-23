# Build the Windows LocalMeetingRecorder distribution folder with PyInstaller
# (Phase 8B packaging). Mirrors scripts/build_macos.sh's steps:
#   helper -> icon -> pyinstaller -> copy helper into Helpers\
#
# Differences from the macOS script, on purpose:
#   - The helper is published SELF-CONTAINED here (scripts/build_helper_windows.ps1's
#     dev build is framework-dependent), so a machine running the packaged app does not
#     need any .NET runtime installed. This costs bundle size; it buys "unzip and run".
#   - No code signing: no signing identity exists for this project on Windows yet.
#     SmartScreen will warn on machines that download the unsigned build; running it
#     locally is unaffected.
#
# Output: packaging\windows\dist\LocalMeetingRecorder\ (run LocalMeetingRecorder.exe).
# ffmpeg/ffprobe are NOT bundled (same policy as macOS) and must be on PATH for
# recording validation and transcription.

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    $DotnetExe = Join-Path $env:ProgramFiles "dotnet\dotnet.exe"
    if (Test-Path $DotnetExe) {
        # Freshly installed SDKs are on the machine PATH but not necessarily on this
        # session's PATH yet.
        $env:Path = (Join-Path $env:ProgramFiles "dotnet") + ";" + $env:Path
    } else {
        Write-Error "dotnet not found. Install the .NET 9 SDK: https://dotnet.microsoft.com/download/dotnet/9.0"
        exit 1
    }
}

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error ".venv not found. Create it first: python -m venv .venv; .venv\Scripts\pip install -e `".[dev,transcription]`""
    exit 1
}

$HelperDist = Join-Path $RepoRoot "packaging\windows\helper-dist"
Write-Host "==> publishing self-contained capture helper -> $HelperDist"
dotnet publish "native\windows\LocalMeetingRecorder.Capture" `
    --configuration Release `
    --runtime win-x64 `
    --self-contained true `
    --output $HelperDist `
    -p:UseAppHost=true
if ($LASTEXITCODE -ne 0) { Write-Error "helper publish failed"; exit 1 }

# -e (editable) for the same reason documented in scripts/build_macos.sh: a plain
# install would silently replace the editable dev install and break dev-mode
# project_root() resolution for later test runs from this venv.
& $VenvPython -m pip install -q -e ".[packaging]"
if ($LASTEXITCODE -ne 0) { Write-Error "pip install failed"; exit 1 }

Write-Host "==> rendering AppIcon.ico"
& $VenvPython scripts\make_app_icon.py packaging\windows\AppIcon.ico
if ($LASTEXITCODE -ne 0) { Write-Error "icon rendering failed"; exit 1 }

Write-Host "==> running PyInstaller"
& $VenvPython -m PyInstaller --noconfirm --clean `
    --distpath packaging\windows\dist `
    --workpath packaging\windows\build `
    packaging\windows\LocalMeetingRecorder.spec
if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller failed"; exit 1 }

$AppDir = Join-Path $RepoRoot "packaging\windows\dist\LocalMeetingRecorder"
$HelpersDir = Join-Path $AppDir "Helpers"
New-Item -ItemType Directory -Force $HelpersDir | Out-Null
Copy-Item (Join-Path $HelperDist "*") $HelpersDir -Recurse -Force

$ExePath = Join-Path $AppDir "LocalMeetingRecorder.exe"
if (-not (Test-Path $ExePath)) { Write-Error "expected $ExePath to exist"; exit 1 }
if (-not (Test-Path (Join-Path $HelpersDir "lmr-capture-helper.exe"))) {
    Write-Error "expected Helpers\lmr-capture-helper.exe to exist"; exit 1
}

Write-Host "==> built $AppDir"
Write-Host "Run: $ExePath   (ffmpeg/ffprobe must be on PATH; the folder is relocatable)"
