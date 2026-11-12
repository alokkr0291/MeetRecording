"""``pyannote.audio``-backed diarization (Phase 5C).

Lazy-loaded exactly like ``transcription/engine.py``'s ``TranscriptionEngine``: importing
this module does nothing heavy by itself (the ``pyannote.audio``/``torch`` import only
happens inside ``_load_pipeline``, on first ``diarize()`` call), and constructing
``PyannoteSpeakerDiarizationEngine`` touches neither disk nor network.

``pyannote/speaker-diarization-3.1`` is Hugging-Face-gated: a user must accept its terms
once (free) and supply a Hugging Face access token for the first download. This cannot be
done on a user's behalf by this application — ``DiarizationUnavailableError`` is raised
with a clear explanation when no token is configured, rather than silently failing or
pretending to work.

Phase 6 (CPU-first readiness): the pipeline is explicitly moved to the CPU device after
loading, via ``pipeline.to(torch.device("cpu"))``, rather than relying on pyannote's own
default (which happens to be CPU today when nothing requests otherwise, but is not
guaranteed to stay that way, and a checkpoint loaded with ``torch.load``'s default
``map_location`` can in principle try to restore CUDA tensors on a machine with no GPU at
all). This app never requires a GPU and never auto-detects one for this pipeline — CPU is
the only device this code ever asks for. ``cpu_threads`` (when given) caps how many
threads torch uses for inference via ``torch.set_num_threads()``, so a "Low Resource"
processing profile (``settings/profiles.py``) can keep diarization from monopolizing every
core on an entry-level machine; ``None``/``0`` leaves torch's own default untouched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from local_meeting_recorder.domain.errors import DiarizationError, DiarizationUnavailableError
from local_meeting_recorder.infrastructure.logging_setup import get_logger
from local_meeting_recorder.speaker.protocol import DiarizationResult, SpeakerRegion
from local_meeting_recorder.storage.paths import is_frozen

log = get_logger(__name__)

DEFAULT_MODEL = "pyannote/speaker-diarization-3.1"


class PyannoteSpeakerDiarizationEngine:
    """Implements ``SpeakerDiarizationEngine`` (structurally — see ``protocol.py``)."""

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_MODEL,
        hf_token: str | None = None,
        cpu_threads: int | None = None,
    ) -> None:
        self.model_name = model_name
        self._hf_token = hf_token
        self._cpu_threads = cpu_threads
        self._pipeline: Any | None = None

    def diarize(self, audio_path: Path) -> DiarizationResult:
        pipeline = self._load_pipeline()
        try:
            annotation = pipeline(str(audio_path))
        except Exception as exc:
            raise DiarizationError(f"pyannote failed to diarize {audio_path}: {exc}") from exc

        regions = [
            SpeakerRegion(start_s=segment.start, end_s=segment.end, speaker_label=str(label))
            for segment, _track, label in annotation.itertracks(yield_label=True)
        ]
        speaker_count = len(annotation.labels())
        return DiarizationResult(
            regions=regions, speaker_count=speaker_count, model_name=self.model_name
        )

    def _load_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        if not self._hf_token:
            raise DiarizationUnavailableError(
                "Diarization needs a Hugging Face access token (free account; accept "
                f"{self.model_name}'s terms once at huggingface.co, then add the token "
                "in Settings)."
            )
        try:
            import torch
            from pyannote.audio import Pipeline
        except ImportError as exc:
            raise DiarizationUnavailableError(self._not_installed_message()) from exc

        log.info("loading diarization model", extra={"model_name": self.model_name})
        try:
            pipeline = Pipeline.from_pretrained(self.model_name, use_auth_token=self._hf_token)
        except Exception as exc:
            raise self._load_failure_as_diarization_error(exc) from exc
        if pipeline is None:
            raise DiarizationUnavailableError(
                f"could not load '{self.model_name}' — check that your Hugging Face "
                "token is valid and you've accepted the model's terms of use."
            )

        # Explicit, never implicit: this app has no GPU-detection code anywhere and
        # never asks pyannote for anything but CPU. See the module docstring.
        pipeline.to(torch.device("cpu"))
        if self._cpu_threads:
            torch.set_num_threads(self._cpu_threads)
        log.info(
            "diarization pipeline ready on cpu",
            extra={"model_name": self.model_name, "cpu_threads": self._cpu_threads or "default"},
        )
        self._pipeline = pipeline
        return pipeline

    def _not_installed_message(self) -> str:
        if is_frozen():
            return (
                "Speaker identification is not available in this build of "
                "LocalMeetingRecorder. Local transcription and recording are unaffected."
            )
        return (
            "pyannote.audio is not installed. Install the 'diarization' extra "
            '(`pip install -e ".[diarization]"`) to enable speaker diarization.'
        )

    def _load_failure_as_diarization_error(self, exc: Exception) -> DiarizationError:
        """Best-effort classification of why loading the gated model failed, so the
        Settings UI can tell a user "your token is invalid" apart from "you haven't
        accepted the model's terms" apart from "we couldn't reach Hugging Face" —
        distinct remediation for each, instead of one generic failure message. The
        exact exception types below are ``huggingface_hub``'s documented behavior as of
        the version pinned in ``pyproject.toml``; this has not been verified against a
        live Hugging Face account with a real invalid/revoked token (no such token was
        available to test against), so treat the specific wording as best-effort, not
        guaranteed to match every real failure mode.
        """
        generic = DiarizationError(f"failed to load diarization model '{self.model_name}': {exc}")
        hf_errors = self._import_hf_hub_errors()
        if hf_errors is None:
            return generic
        gated_repo_error, repository_not_found_error, hf_hub_http_error = hf_errors

        if isinstance(exc, gated_repo_error):
            return DiarizationUnavailableError(
                f"Your Hugging Face token does not have access to '{self.model_name}' "
                "yet. Visit huggingface.co, accept the model's terms of use, and try "
                "again."
            )
        if isinstance(exc, repository_not_found_error):
            return DiarizationUnavailableError(
                "Hugging Face rejected the configured access token (it may be invalid, "
                "expired, or revoked). Open Settings to re-enter it."
            )
        if isinstance(exc, hf_hub_http_error):
            return self._classify_http_error(exc, generic)
        if self._is_connection_failure(exc):
            return DiarizationUnavailableError(
                "Could not reach Hugging Face to download the diarization model — "
                "check your network connection and try again."
            )
        return generic

    @staticmethod
    def _import_hf_hub_errors() -> tuple[type[Exception], type[Exception], type[Exception]] | None:
        try:
            from huggingface_hub.utils import (  # type: ignore[attr-defined]
                GatedRepoError,
                HfHubHTTPError,
                RepositoryNotFoundError,
            )
        except ImportError:
            return None
        return GatedRepoError, RepositoryNotFoundError, HfHubHTTPError

    @staticmethod
    def _classify_http_error(exc: Exception, generic: DiarizationError) -> DiarizationError:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (401, 403):
            return DiarizationUnavailableError(
                "Hugging Face rejected the configured access token — it may be "
                "invalid or lack permission for this model. Open Settings to check it."
            )
        return generic

    @staticmethod
    def _is_connection_failure(exc: Exception) -> bool:
        try:
            import requests
        except ImportError:
            return False
        return isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout))
