# PyInstaller spec for the Windows LocalMeetingRecorder build (Phase 8B packaging).
#
# Run via scripts/build_windows.ps1, not `pyinstaller` directly — that script publishes
# the C# capture helper (self-contained, so target machines need no .NET runtime),
# renders the .ico, and copies the helper into <dist>/LocalMeetingRecorder/Helpers/
# after PyInstaller produces the folder (PyInstaller has no notion of the helper).
# `storage.paths.resolve_helper_binary()` looks for Helpers/ next to the frozen exe.
#
# The exclude lists are shared rationale with packaging/macos/LocalMeetingRecorder.spec —
# see that file's comments for the full provenance (diarization's torch tree must never
# ride along into every user's download just because the build venv has it installed,
# and dev-only tooling has no business in a distributed app).

from pathlib import Path

block_cipher = None

PROJECT_ROOT = Path(SPECPATH).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
ICON = PROJECT_ROOT / "packaging" / "windows" / "AppIcon.ico"

DEV_TOOL_EXCLUDES = ["mypy", "pytest", "_pytest", "pluggy", "iniconfig"]

DIARIZATION_ONLY_EXCLUDES = [
    "torch",
    "torchaudio",
    "torchvision",
    "torchmetrics",
    "torch_audiomentations",
    "torch_pitch_shift",
    "pytorch_metric_learning",
    "lightning",
    "pytorch_lightning",
    "speechbrain",
    "asteroid_filterbanks",
    "julius",
    "einops",
    "omegaconf",
    "semver",
    "tensorboardX",
    "soundfile",
    "rich",
    "typer",
    "pyannote",
    "matplotlib",
    "pandas",
    "scipy",
    "PIL",
    "sklearn",
    "sqlalchemy",
    "alembic",
    "optuna",
    "mako",
    "colorlog",
    "narwhals",
    "threadpoolctl",
    "joblib",
]

a = Analysis(
    [str(SRC / "local_meeting_recorder" / "main.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=DIARIZATION_ONLY_EXCLUDES + DEV_TOOL_EXCLUDES,
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LocalMeetingRecorder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # tray app: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LocalMeetingRecorder",
)
