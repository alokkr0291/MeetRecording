"""TranscriptStore: atomic write of transcript.json/transcript.txt, round-trip read."""

from __future__ import annotations

from pathlib import Path

from local_meeting_recorder.transcription.models import Transcript, TranscriptSegment
from local_meeting_recorder.transcription.store import TranscriptStore


def _transcript() -> Transcript:
    return Transcript(
        session_id="lmr_20261110T000000_aaaa",
        language="en",
        language_probability=0.91,
        model_name="small",
        segments=[
            TranscriptSegment(start_s=0.0, end_s=1.2, text="hello there"),
            TranscriptSegment(start_s=1.2, end_s=2.5, text="general kenobi"),
        ],
    )


def test_write_creates_json_and_txt(tmp_path: Path) -> None:
    store = TranscriptStore()
    paths = store.write(tmp_path, _transcript())

    assert paths.json_path == tmp_path / "transcript.json"
    assert paths.txt_path == tmp_path / "transcript.txt"
    assert paths.json_path.is_file()
    assert paths.txt_path.is_file()
    assert paths.txt_path.read_text(encoding="utf-8") == "hello there\ngeneral kenobi"


def test_read_round_trips_the_written_transcript(tmp_path: Path) -> None:
    store = TranscriptStore()
    original = _transcript()
    store.write(tmp_path, original)

    restored = store.read(tmp_path)
    assert restored == original


def test_exists_false_before_write_true_after(tmp_path: Path) -> None:
    store = TranscriptStore()
    assert not store.exists(tmp_path)
    store.write(tmp_path, _transcript())
    assert store.exists(tmp_path)


def test_write_creates_missing_session_dir(tmp_path: Path) -> None:
    store = TranscriptStore()
    session_dir = tmp_path / "not-yet-created"
    store.write(session_dir, _transcript())
    assert session_dir.is_dir()
    assert (session_dir / "transcript.json").is_file()


def test_write_leaves_no_temp_files_behind(tmp_path: Path) -> None:
    store = TranscriptStore()
    store.write(tmp_path, _transcript())
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".transcript-")]
    assert leftovers == []
