from __future__ import annotations

from pathlib import Path

import pytest

from local_meeting_recorder.capture.factory import default_backend_factory
from local_meeting_recorder.capture.macos.backend import MacOSCaptureBackend
from local_meeting_recorder.capture.protocols import CaptureCallbacks
from local_meeting_recorder.domain.errors import HelperUnavailableError


def test_darwin_factory_builds_a_macos_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("local_meeting_recorder.capture.factory.sys.platform", "darwin")
    fake_helper = tmp_path / "lmr-capture-helper"
    fake_helper.write_text("#!/bin/sh\n")
    fake_helper.chmod(0o755)

    factory = default_backend_factory(helper_path=fake_helper)
    backend = factory(CaptureCallbacks())
    assert isinstance(backend, MacOSCaptureBackend)


def test_unsupported_platform_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("local_meeting_recorder.capture.factory.sys.platform", "win32")
    with pytest.raises(HelperUnavailableError):
        default_backend_factory()
