"""PyannoteSpeakerDiarizationEngine: the wrapper logic, against the real installed
``pyannote.audio`` with ``Pipeline.from_pretrained`` mocked out (no real model download,
no Hugging Face token needed) — matching the project's "fake the external boundary"
convention. Skips entirely if the optional ``diarization`` extra isn't installed, same
pattern as the ffmpeg-gated integration tests.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from local_meeting_recorder.domain.errors import DiarizationError, DiarizationUnavailableError
from local_meeting_recorder.speaker.pyannote_engine import PyannoteSpeakerDiarizationEngine

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("pyannote.audio") is None,
    reason="pyannote.audio not installed — install the 'diarization' extra to run this",
)


class _FakeAnnotation:
    def __init__(self, tracks: list[tuple[float, float, str]]) -> None:
        self._tracks = tracks

    def itertracks(self, *, yield_label: bool = False) -> list[tuple[object, None, str]]:
        from pyannote.core import Segment

        return [(Segment(start, end), None, label) for start, end, label in self._tracks]

    def labels(self) -> list[str]:
        return sorted({label for _s, _e, label in self._tracks})


def test_no_hf_token_raises_unavailable_without_importing_pyannote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = PyannoteSpeakerDiarizationEngine(hf_token=None)
    with pytest.raises(DiarizationUnavailableError, match="Hugging Face"):
        engine.diarize(tmp_path / "audio.wav")


class _FakePipeline:
    """Mirrors the one real-``Pipeline`` interface this engine actually uses,
    including ``to()`` (Phase 6: the engine always calls this to force CPU)."""

    def __init__(self, annotation: _FakeAnnotation) -> None:
        self._annotation = annotation
        self.to_calls: list[object] = []

    def __call__(self, audio_path: str) -> _FakeAnnotation:
        return self._annotation

    def to(self, device: object) -> _FakePipeline:
        self.to_calls.append(device)
        return self


def test_successful_diarization_returns_regions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_annotation = _FakeAnnotation([(0.0, 1.5, "SPEAKER_00"), (1.5, 3.0, "SPEAKER_01")])
    pipeline = _FakePipeline(fake_annotation)
    monkeypatch.setattr(
        "pyannote.audio.Pipeline.from_pretrained", classmethod(lambda cls, *a, **k: pipeline)
    )

    engine = PyannoteSpeakerDiarizationEngine(hf_token="fake-token")
    result = engine.diarize(tmp_path / "audio.wav")

    assert result.speaker_count == 2
    assert [(r.start_s, r.end_s, r.speaker_label) for r in result.regions] == [
        (0.0, 1.5, "SPEAKER_00"),
        (1.5, 3.0, "SPEAKER_01"),
    ]
    assert result.model_name == engine.model_name


def test_pipeline_is_explicitly_moved_to_cpu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Phase 6: CPU is never implicit — the engine must call ``pipeline.to(cpu)``
    itself, not rely on pyannote's/torch's own default device selection."""
    import torch

    pipeline = _FakePipeline(_FakeAnnotation([]))
    monkeypatch.setattr(
        "pyannote.audio.Pipeline.from_pretrained", classmethod(lambda cls, *a, **k: pipeline)
    )

    engine = PyannoteSpeakerDiarizationEngine(hf_token="fake-token")
    engine.diarize(tmp_path / "audio.wav")

    assert pipeline.to_calls == [torch.device("cpu")]


def test_cpu_threads_are_set_when_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import torch

    pipeline = _FakePipeline(_FakeAnnotation([]))
    monkeypatch.setattr(
        "pyannote.audio.Pipeline.from_pretrained", classmethod(lambda cls, *a, **k: pipeline)
    )
    set_calls: list[int] = []
    monkeypatch.setattr(torch, "set_num_threads", set_calls.append)

    engine = PyannoteSpeakerDiarizationEngine(hf_token="fake-token", cpu_threads=2)
    engine.diarize(tmp_path / "audio.wav")

    assert set_calls == [2]


def test_cpu_threads_left_alone_when_not_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import torch

    pipeline = _FakePipeline(_FakeAnnotation([]))
    monkeypatch.setattr(
        "pyannote.audio.Pipeline.from_pretrained", classmethod(lambda cls, *a, **k: pipeline)
    )
    set_calls: list[int] = []
    monkeypatch.setattr(torch, "set_num_threads", set_calls.append)

    engine = PyannoteSpeakerDiarizationEngine(hf_token="fake-token")
    engine.diarize(tmp_path / "audio.wav")

    assert set_calls == []


def test_pipeline_is_loaded_only_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = []

    def fake_from_pretrained(cls: object, *a: object, **k: object) -> _FakePipeline:
        calls.append(1)
        return _FakePipeline(_FakeAnnotation([]))

    monkeypatch.setattr(
        "pyannote.audio.Pipeline.from_pretrained", classmethod(fake_from_pretrained)
    )

    engine = PyannoteSpeakerDiarizationEngine(hf_token="fake-token")
    engine.diarize(tmp_path / "a.wav")
    engine.diarize(tmp_path / "b.wav")
    assert len(calls) == 1


def test_pipeline_load_failure_is_wrapped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom(cls: object, *a: object, **k: object) -> None:
        raise RuntimeError("bad token")

    monkeypatch.setattr("pyannote.audio.Pipeline.from_pretrained", classmethod(boom))
    engine = PyannoteSpeakerDiarizationEngine(hf_token="fake-token")
    with pytest.raises(DiarizationError):
        engine.diarize(tmp_path / "audio.wav")


def test_none_pipeline_raises_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "pyannote.audio.Pipeline.from_pretrained", classmethod(lambda cls, *a, **k: None)
    )
    engine = PyannoteSpeakerDiarizationEngine(hf_token="fake-token")
    with pytest.raises(DiarizationUnavailableError):
        engine.diarize(tmp_path / "audio.wav")


def test_diarize_call_failure_is_wrapped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _BrokenPipeline(_FakePipeline):
        def __call__(self, audio_path: str) -> None:
            raise RuntimeError("decoder crashed")

    monkeypatch.setattr(
        "pyannote.audio.Pipeline.from_pretrained",
        classmethod(lambda cls, *a, **k: _BrokenPipeline(_FakeAnnotation([]))),
    )
    engine = PyannoteSpeakerDiarizationEngine(hf_token="fake-token")
    with pytest.raises(DiarizationError):
        engine.diarize(tmp_path / "audio.wav")


# -- distinguishing *why* loading the gated model failed --------------------------------


class _FakeHttpResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self.request = None


def test_gated_repo_error_means_terms_not_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from huggingface_hub.utils import GatedRepoError

    def boom(cls: object, *a: object, **k: object) -> None:
        raise GatedRepoError("gated", response=_FakeHttpResponse(403))

    monkeypatch.setattr("pyannote.audio.Pipeline.from_pretrained", classmethod(boom))
    engine = PyannoteSpeakerDiarizationEngine(hf_token="fake-token")
    with pytest.raises(DiarizationUnavailableError, match="accept the model's terms"):
        engine.diarize(tmp_path / "audio.wav")


def test_repository_not_found_error_means_invalid_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from huggingface_hub.utils import RepositoryNotFoundError

    def boom(cls: object, *a: object, **k: object) -> None:
        raise RepositoryNotFoundError("not found", response=_FakeHttpResponse(401))

    monkeypatch.setattr("pyannote.audio.Pipeline.from_pretrained", classmethod(boom))
    engine = PyannoteSpeakerDiarizationEngine(hf_token="fake-token")
    with pytest.raises(DiarizationUnavailableError, match="rejected the configured access token"):
        engine.diarize(tmp_path / "audio.wav")


def test_401_http_error_means_invalid_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from huggingface_hub.utils import HfHubHTTPError

    def boom(cls: object, *a: object, **k: object) -> None:
        raise HfHubHTTPError("unauthorized", response=_FakeHttpResponse(401))

    monkeypatch.setattr("pyannote.audio.Pipeline.from_pretrained", classmethod(boom))
    engine = PyannoteSpeakerDiarizationEngine(hf_token="fake-token")
    with pytest.raises(DiarizationUnavailableError, match="invalid or lack permission"):
        engine.diarize(tmp_path / "audio.wav")


def test_other_http_error_falls_back_to_a_generic_diarization_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from huggingface_hub.utils import HfHubHTTPError

    def boom(cls: object, *a: object, **k: object) -> None:
        raise HfHubHTTPError("server error", response=_FakeHttpResponse(500))

    monkeypatch.setattr("pyannote.audio.Pipeline.from_pretrained", classmethod(boom))
    engine = PyannoteSpeakerDiarizationEngine(hf_token="fake-token")
    with pytest.raises(DiarizationError) as excinfo:
        engine.diarize(tmp_path / "audio.wav")
    assert not isinstance(excinfo.value, DiarizationUnavailableError)


def test_connection_error_means_network_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import requests

    def boom(cls: object, *a: object, **k: object) -> None:
        raise requests.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr("pyannote.audio.Pipeline.from_pretrained", classmethod(boom))
    engine = PyannoteSpeakerDiarizationEngine(hf_token="fake-token")
    with pytest.raises(DiarizationUnavailableError, match="Could not reach Hugging Face"):
        engine.diarize(tmp_path / "audio.wav")
