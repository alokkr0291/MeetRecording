"""Application: composition root startup/shutdown, with a fake backend (no subprocess)."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer

from local_meeting_recorder.app import Application
from local_meeting_recorder.capture.fake import FakeCaptureBackend
from local_meeting_recorder.capture.protocols import CaptureBackend, CaptureCallbacks
from local_meeting_recorder.config import Settings
from local_meeting_recorder.domain.recording_state import RecordingState
from local_meeting_recorder.storage.paths import StoragePaths
from local_meeting_recorder.storage.session_store import SessionStore


def _fake_backend_factory(fake: FakeCaptureBackend) -> Callable[[CaptureCallbacks], CaptureBackend]:
    def factory(callbacks: CaptureCallbacks) -> CaptureBackend:
        fake.callbacks = callbacks
        return fake

    return factory


def test_application_constructs_shows_tray_and_shuts_down_cleanly(data_dir: Path) -> None:
    paths = StoragePaths(root=data_dir)
    paths.ensure()
    store = SessionStore(paths)
    fake = FakeCaptureBackend()
    settings = Settings(_env_file=None, data_dir=data_dir)

    app = Application(
        backend_factory=_fake_backend_factory(fake),
        session_store=store,
        settings=settings,
    )
    assert app.coordinator.state is RecordingState.IDLE
    assert not app.qt_app.quitOnLastWindowClosed()

    QTimer.singleShot(0, app.qt_app.quit)
    exit_code = app.run()
    assert exit_code == 0
    assert app.tray.is_visible


def test_shutdown_stops_an_active_recording_and_cleans_up_the_backend(data_dir: Path) -> None:
    paths = StoragePaths(root=data_dir)
    paths.ensure()
    store = SessionStore(paths)
    fake = FakeCaptureBackend()
    settings = Settings(_env_file=None, data_dir=data_dir)

    app = Application(
        backend_factory=_fake_backend_factory(fake),
        session_store=store,
        settings=settings,
    )
    app.coordinator.start_recording()
    assert app.coordinator.state is RecordingState.RECORDING

    QTimer.singleShot(0, app.qt_app.quit)
    app.run()

    assert app.coordinator.state is RecordingState.SAVED
    assert fake.stop_calls == 1
    assert fake.shutdown_calls == 1
