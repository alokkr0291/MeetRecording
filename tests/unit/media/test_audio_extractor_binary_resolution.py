"""``AudioExtractor`` resolves its ffmpeg/ffprobe paths through
``storage.paths.resolve_external_binary`` at construction time — the actual
``ffmpeg``/``ffprobe`` behavior is covered by the real-ffmpeg integration tests in
``tests/integration/media/``; this file only checks the resolution wiring itself,
which matters for a double-clicked ``.app`` whose minimal PATH excludes Homebrew
(Phase 6C)."""

from __future__ import annotations

import pytest

import local_meeting_recorder.media.audio_extractor as audio_extractor_module
from local_meeting_recorder.media.audio_extractor import AudioExtractor


def test_ffmpeg_and_ffprobe_paths_are_resolved_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audio_extractor_module,
        "resolve_external_binary",
        lambda name: f"/resolved/{name}",
    )

    extractor = AudioExtractor(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")

    assert extractor._ffmpeg == "/resolved/ffmpeg"
    assert extractor._inspector._ffmpeg == "/resolved/ffmpeg"
    assert extractor._inspector._ffprobe == "/resolved/ffprobe"
