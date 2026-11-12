"""TranscriptionEngine against a scripted fake ``WhisperModel`` — no real model load,
download, or inference. Mirrors the project's existing "fake the external boundary"
pattern (``capture/fake.py`` for the Swift helper).
"""

from __future__ import annotations

import wave
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from local_meeting_recorder.domain.errors import TranscriptionEmptyAudioError, TranscriptionError
from local_meeting_recorder.transcription import engine as engine_module
from local_meeting_recorder.transcription.engine import TranscriptionEngine


def _write_wav(path: Path, *, frames: int = 1600, rate: int = 16_000) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(b"\x00\x00" * frames)


def _write_empty_wav(path: Path) -> None:
    _write_wav(path, frames=0)


@dataclass
class _FakeSegment:
    start: float
    end: float
    text: str


@dataclass
class _FakeInfo:
    language: str = "en"
    language_probability: float = 0.99
    duration: float = 3.0


@dataclass
class _FakeWhisperModel:
    """Stands in for ``faster_whisper.WhisperModel``: records how it was built and called."""

    model_size_or_path: str
    device: str = "cpu"
    compute_type: str = "default"
    download_root: str | None = None
    segments: list[_FakeSegment] = field(
        default_factory=lambda: [_FakeSegment(0.0, 1.0, " hello "), _FakeSegment(1.0, 2.0, "world")]
    )
    info: _FakeInfo = field(default_factory=_FakeInfo)
    transcribe_calls: list[tuple[str, str | None]] = field(default_factory=list)

    def transcribe(
        self, audio_path: str, *, language: str | None = None, **_: object
    ) -> tuple[list[_FakeSegment], _FakeInfo]:
        self.transcribe_calls.append((audio_path, language))
        return self.segments, self.info


class _ConstructorSpy:
    """Factory standing in for the ``WhisperModel`` class itself: records every
    construction attempt so tests can assert the model was (or was not) built."""

    def __init__(self, instance: _FakeWhisperModel | None = None, *, fail: Exception | None = None):
        self.instance = instance or _FakeWhisperModel(model_size_or_path="unset")
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def __call__(self, model_size_or_path: str, **kwargs: object) -> _FakeWhisperModel:
        self.calls.append({"model_size_or_path": model_size_or_path, **kwargs})
        if self.fail is not None:
            raise self.fail
        self.instance.model_size_or_path = model_size_or_path
        return self.instance


def test_model_is_not_loaded_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _ConstructorSpy()
    monkeypatch.setattr(engine_module, "WhisperModel", spy)
    TranscriptionEngine(model_size="small")
    assert spy.calls == []


def test_model_loads_lazily_on_first_transcribe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spy = _ConstructorSpy()
    monkeypatch.setattr(engine_module, "WhisperModel", spy)
    wav = tmp_path / "audio.wav"
    _write_wav(wav)

    engine = TranscriptionEngine(model_size="small", device="cpu", compute_type="int8")
    engine.transcribe(wav)
    assert len(spy.calls) == 1
    assert spy.calls[0]["model_size_or_path"] == "small"
    assert spy.calls[0]["device"] == "cpu"
    assert spy.calls[0]["compute_type"] == "int8"

    engine.transcribe(wav)
    assert len(spy.calls) == 1  # second call reuses the already-loaded model


# -- Phase 6: CPU-first by default, no GPU auto-detection ------------------------------


def test_defaults_are_cpu_and_int8_with_no_explicit_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``WhisperModel`` itself defaults to ``device="auto"`` (which can select CUDA) —
    this app must never rely on that: constructing ``TranscriptionEngine`` with no
    arguments at all must still load the model with ``device="cpu"``,
    ``compute_type="int8"``."""
    spy = _ConstructorSpy()
    monkeypatch.setattr(engine_module, "WhisperModel", spy)
    wav = tmp_path / "audio.wav"
    _write_wav(wav)

    TranscriptionEngine().transcribe(wav)

    assert spy.calls[0]["device"] == "cpu"
    assert spy.calls[0]["compute_type"] == "int8"
    assert spy.calls[0]["model_size_or_path"] == "base"


def test_gpu_availability_does_not_change_the_default_device(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Even if the environment reports a GPU, nothing in this engine ever asks about
    it — the only thing that can change ``device`` is an explicit constructor argument.
    Proven here by making ``torch.cuda.is_available`` return True (if torch happens to
    be importable at all) and confirming it has zero effect."""
    try:
        import torch  # noqa: PLC0415 — optional; this test file has no module-level torch import

        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    except ImportError:
        pass  # faster-whisper's own CPU path (ctranslate2) doesn't need torch at all

    spy = _ConstructorSpy()
    monkeypatch.setattr(engine_module, "WhisperModel", spy)
    wav = tmp_path / "audio.wav"
    _write_wav(wav)

    TranscriptionEngine().transcribe(wav)

    assert spy.calls[0]["device"] == "cpu"


def test_cpu_threads_defaults_to_zero_and_is_forwarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``0`` means "let ctranslate2 decide" (unchanged pre-Phase-6 behavior); an
    explicit value (e.g. from a "Low Resource" profile) must reach ``WhisperModel``."""
    spy = _ConstructorSpy()
    monkeypatch.setattr(engine_module, "WhisperModel", spy)
    wav = tmp_path / "audio.wav"
    _write_wav(wav)

    TranscriptionEngine().transcribe(wav)
    assert spy.calls[0]["cpu_threads"] == 0

    spy2 = _ConstructorSpy()
    monkeypatch.setattr(engine_module, "WhisperModel", spy2)
    TranscriptionEngine(cpu_threads=2).transcribe(wav)
    assert spy2.calls[0]["cpu_threads"] == 2


def test_missing_cuda_or_torch_does_not_break_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Constructing ``TranscriptionEngine`` (never mind running it) must not import
    torch/cuda at all — faster-whisper's CPU path (ctranslate2) has no such
    dependency, and this engine never touches ``WhisperModel`` until ``transcribe()``
    is actually called."""
    TranscriptionEngine()  # must not raise, must not need torch/cuda to exist


def test_segments_preserve_order_and_are_stripped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spy = _ConstructorSpy()
    monkeypatch.setattr(engine_module, "WhisperModel", spy)
    wav = tmp_path / "audio.wav"
    _write_wav(wav)

    engine = TranscriptionEngine()
    result = engine.transcribe(wav)

    assert [s.text for s in result.segments] == ["hello", "world"]
    assert [(s.start_s, s.end_s) for s in result.segments] == [(0.0, 1.0), (1.0, 2.0)]
    assert result.language == "en"
    assert result.language_probability == 0.99
    assert result.duration_s == 3.0


def test_explicit_language_is_passed_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_model = _FakeWhisperModel(model_size_or_path="small")
    spy = _ConstructorSpy(instance=fake_model)
    monkeypatch.setattr(engine_module, "WhisperModel", spy)
    wav = tmp_path / "audio.wav"
    _write_wav(wav)

    engine = TranscriptionEngine()
    engine.transcribe(wav, language="fr")
    assert fake_model.transcribe_calls == [(str(wav), "fr")]


def test_language_none_means_auto_detect(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_model = _FakeWhisperModel(model_size_or_path="small")
    spy = _ConstructorSpy(instance=fake_model)
    monkeypatch.setattr(engine_module, "WhisperModel", spy)
    wav = tmp_path / "audio.wav"
    _write_wav(wav)

    TranscriptionEngine().transcribe(wav)
    assert fake_model.transcribe_calls == [(str(wav), None)]


def test_on_segment_callback_fires_per_segment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spy = _ConstructorSpy()
    monkeypatch.setattr(engine_module, "WhisperModel", spy)
    wav = tmp_path / "audio.wav"
    _write_wav(wav)

    seen: list[tuple[float, float | None]] = []
    TranscriptionEngine().transcribe(
        wav, on_segment=lambda processed_s, total_s: seen.append((processed_s, total_s))
    )
    assert seen == [(1.0, 3.0), (2.0, 3.0)]


def test_empty_audio_raises_without_loading_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spy = _ConstructorSpy()
    monkeypatch.setattr(engine_module, "WhisperModel", spy)
    wav = tmp_path / "empty.wav"
    _write_empty_wav(wav)

    with pytest.raises(TranscriptionEmptyAudioError):
        TranscriptionEngine().transcribe(wav)
    assert spy.calls == []  # never even tried to load the model


def test_model_load_failure_is_wrapped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    spy = _ConstructorSpy(fail=RuntimeError("no internet, cannot download model"))
    monkeypatch.setattr(engine_module, "WhisperModel", spy)
    wav = tmp_path / "audio.wav"
    _write_wav(wav)

    with pytest.raises(TranscriptionError):
        TranscriptionEngine().transcribe(wav)


def test_transcribe_failure_is_wrapped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _BrokenModel(_FakeWhisperModel):
        def transcribe(  # type: ignore[override]
            self, audio_path: str, *, language: str | None = None, **_: object
        ) -> tuple[list[_FakeSegment], _FakeInfo]:
            raise RuntimeError("decoder error")

    spy = _ConstructorSpy(instance=_BrokenModel(model_size_or_path="small"))
    monkeypatch.setattr(engine_module, "WhisperModel", spy)
    wav = tmp_path / "audio.wav"
    _write_wav(wav)

    with pytest.raises(TranscriptionError):
        TranscriptionEngine().transcribe(wav)


def test_not_a_wav_file_raises_transcription_error(tmp_path: Path) -> None:
    bogus = tmp_path / "not_audio.wav"
    bogus.write_bytes(b"this is not a wav file")
    with pytest.raises(TranscriptionError):
        TranscriptionEngine().transcribe(bogus)
