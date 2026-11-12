# PyInstaller spec for LocalMeetingRecorder.app (Phase 6).
#
# Run via scripts/build_macos.sh, not `pyinstaller` directly — that script builds the
# Swift helper, generates the icon, and copies the helper into Contents/Helpers after
# PyInstaller produces the bundle (PyInstaller has no notion of the helper binary).
#
# Signing/notarization are separate manual steps; see .claude/skills/packaging/SKILL.md.

from pathlib import Path

block_cipher = None

PROJECT_ROOT = Path(SPECPATH).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
ICON = PROJECT_ROOT / "packaging" / "macos" / "AppIcon.icns"

# Phase 6 (CPU-first, low-resource readiness): PyInstaller's static import analysis
# follows `speaker/pyannote_engine.py`'s lazily-imported `torch`/`pyannote.audio` calls
# into site-packages and would bundle them — and their whole dependency tree, ~1GB+
# installed (`pip show pyannote.audio`) — into every user's download the moment the
# `diarization` extra happens to be installed in whatever venv this spec is built from,
# even though most users never enable speaker identification. Excluding them here means
# the default packaged app ships WITHOUT local diarization: constructing
# `PyannoteSpeakerDiarizationEngine` still works, but its lazy `import torch` raises
# `ImportError` at runtime, which `_load_pipeline()` already turns into a clean
# `DiarizationUnavailableError` ("not available in this build") rather than a crash —
# see `storage.paths.is_frozen()`. A future "full" build variant that keeps diarization
# bundled is a separate, not-yet-built spec, not this one; see IMPLEMENTATION_STATUS.md.
#
# Verified via `pip show pyannote.audio`/`pip show faster-whisper` that none of these are
# also required by faster-whisper (the default, always-on local transcription path) —
# huggingface_hub, ctranslate2, numpy, tokenizers, onnxruntime, av, and tqdm are
# faster-whisper's own dependencies and are deliberately NOT excluded.
#
# Phase 6C correction: the first version of this list (Phase 6B) only excluded
# `torch`/`pyannote` by name and was verified by grepping the built bundle for
# "*torch*"/"*pyannote*" — which passed, but missed that `pyannote.metrics`/
# `pyannote.database`/`pyannote.pipeline`/`speechbrain` also transitively pull in
# matplotlib, pandas, scipy, Pillow, scikit-learn, SQLAlchemy, alembic, and optuna
# (traced via `pip show <pkg> | grep Required-by` for each, not assumed) — none of
# which have "torch" or "pyannote" in their name, so the earlier grep-based
# verification silently missed them. Confirmed via the same `pip show` trace that none
# of these are required by PySide6/pydantic/pydantic-settings/keyring/faster-whisper's
# own tree either.
# Separately: `pydantic.mypy` (pydantic's own *optional* mypy-plugin submodule, for
# projects that want pydantic-aware mypy checking — never imported by this app) statically
# imports the whole `mypy` package, and `fsspec` (a huggingface_hub dependency) ships a
# `conftest.py` that imports `pytest`/`pluggy`/`iniconfig` as part of its own installed
# package. Both `mypy` and `pytest` are declared only in this project's own `[dev]` extra
# (`pyproject.toml`) — genuinely developer-only tooling that has no business in a
# distributed app bundle, confirmed via the Analysis warnings log
# (`packaging/macos/build/LocalMeetingRecorder/warn-LocalMeetingRecorder.txt`), not guessed.
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
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
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

app = BUNDLE(
    coll,
    name="My Recorder.app",
    icon=str(ICON) if ICON.is_file() else None,
    bundle_identifier="com.localmeetingrecorder.app",
    info_plist={
        "CFBundleDisplayName": "My Recorder",
        "CFBundleName": "My Recorder",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "15.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": (
            "My Recorder records your microphone so your own voice is part of "
            "the meeting recording. Audio never leaves this Mac."
        ),
        "NSAudioCaptureUsageDescription": (
            "My Recorder records system audio so the other participants' "
            "voices are part of the meeting recording. Audio never leaves this Mac."
        ),
        "NSScreenCaptureUsageDescription": (
            "My Recorder records the selected display so the meeting video is "
            "part of the recording. Video never leaves this Mac."
        ),
    },
)
