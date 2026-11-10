"""Wraps ``ffprobe``/``ffmpeg`` to validate a finished recording.

Used after ``stopped``, never during capture (the helper owns real-time encoding). Checks
stream shape (track count, codecs, resolution, duration) and per-track loudness; A/V
offset-at-start is reported by the helper itself (``StoppedInfo.first_*_offset_s``) and is
not recomputed here.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from local_meeting_recorder.domain.errors import MediaError

SILENCE_FLOOR_DB = -60.0


class StreamSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    codec_type: str  # "video" | "audio"
    codec_name: str | None = None
    width: int | None = None
    height: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    avg_frame_rate: str | None = None
    duration_s: float | None = None
    nb_frames: int | None = None


class MediaSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    format_name: str
    duration_s: float
    size_bytes: int
    streams: list[StreamSummary]

    @property
    def video_streams(self) -> list[StreamSummary]:
        return [s for s in self.streams if s.codec_type == "video"]

    @property
    def audio_streams(self) -> list[StreamSummary]:
        return [s for s in self.streams if s.codec_type == "audio"]


def is_audible(rms_db: float | None, *, floor_db: float = SILENCE_FLOOR_DB) -> bool:
    return rms_db is not None and rms_db > floor_db


class MediaInspector:
    def __init__(
        self,
        *,
        ffprobe_path: str = "ffprobe",
        ffmpeg_path: str = "ffmpeg",
        timeout: float = 30.0,
    ) -> None:
        self._ffprobe = ffprobe_path
        self._ffmpeg = ffmpeg_path
        self._timeout = timeout

    def probe(self, path: Path) -> MediaSummary:
        argv = [
            self._ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        proc = self._run(argv)
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise MediaError(f"ffprobe produced invalid JSON for {path}: {exc}") from exc
        fmt = data.get("format", {})
        streams = [self._parse_stream(s) for s in data.get("streams", [])]
        try:
            duration_s = float(fmt["duration"])
        except (KeyError, ValueError, TypeError) as exc:
            raise MediaError(f"ffprobe did not report a duration for {path}") from exc
        return MediaSummary(
            path=str(path),
            format_name=fmt.get("format_name", ""),
            duration_s=duration_s,
            size_bytes=int(fmt.get("size", 0)),
            streams=streams,
        )

    def measure_rms_db(self, path: Path, stream_index: int) -> float | None:
        """Overall RMS level (dBFS) of one stream, via ffmpeg's ``astats`` filter.

        Returns ``None`` if ffmpeg produced no readable level (e.g. a stream with zero
        samples); never raises for that case, since "silent" is itself a result.
        """
        argv = [
            self._ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-map",
            f"0:{stream_index}",
            "-af",
            "astats",
            "-f",
            "null",
            "-",
        ]
        proc = self._run(argv)
        matches = re.findall(r"RMS level dB:\s*(-?[0-9.]+|-inf)", proc.stderr)
        if not matches:
            return None
        last = matches[-1]
        return float("-inf") if last == "-inf" else float(last)

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                argv, capture_output=True, text=True, timeout=self._timeout, check=True
            )
        except FileNotFoundError as exc:
            raise MediaError(f"{argv[0]} not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise MediaError(f"{argv[0]} timed out after {self._timeout}s") from exc
        except subprocess.CalledProcessError as exc:
            raise MediaError(f"{argv[0]} failed (exit {exc.returncode}): {exc.stderr}") from exc

    @staticmethod
    def _parse_stream(raw: dict[str, object]) -> StreamSummary:
        duration = raw.get("duration")
        sample_rate = raw.get("sample_rate")
        nb_frames = raw.get("nb_frames")
        width = raw.get("width")
        height = raw.get("height")
        channels = raw.get("channels")
        return StreamSummary(
            index=int(str(raw["index"])),
            codec_type=str(raw.get("codec_type", "unknown")),
            codec_name=str(raw["codec_name"]) if raw.get("codec_name") else None,
            width=int(str(width)) if width else None,
            height=int(str(height)) if height else None,
            sample_rate=int(str(sample_rate)) if sample_rate else None,
            channels=int(str(channels)) if channels else None,
            avg_frame_rate=str(raw["avg_frame_rate"]) if raw.get("avg_frame_rate") else None,
            duration_s=float(str(duration)) if duration is not None else None,
            nb_frames=int(str(nb_frames)) if nb_frames else None,
        )
