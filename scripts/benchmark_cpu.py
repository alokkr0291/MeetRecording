#!/usr/bin/env python3
"""Reproducible CPU-only benchmark for LocalMeetingRecorder (Phase 6).

Measures, for each stage actually run, wall-clock duration, peak resident memory, and
CPU time consumed — using only the stdlib (``resource`` on POSIX; a documented
"not available" on Windows, where that module doesn't exist) so this script never
needs a new dependency just to benchmark one. If ``psutil`` happens to be installed it
is used for a live average-CPU-percent sample too; its absence never fails the run.

This script measures what is actually run. It never estimates or fabricates a number
for a stage you didn't ask it to run — see ``--help`` for exactly what each flag does,
and read the printed/JSON report's ``"not_run"`` list before trusting any omission.

Usage:
    python scripts/benchmark_cpu.py --transcribe path/to/audio_or_video.wav \\
        --whisper-model base --device cpu --compute-type int8

    python scripts/benchmark_cpu.py --synthesize-speech-minutes 5 --whisper-model tiny
        # macOS only: uses `say` + ffmpeg to generate real (if synthetic-voice) speech
        # of the requested length, instead of requiring a pre-recorded file.

    python scripts/benchmark_cpu.py --diarize path/to/audio.wav --hf-token "$LMR_HF_TOKEN"

    python scripts/benchmark_cpu.py --summarize-transcript path/to/transcript.json \\
        --ollama-model llama3.1
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from local_meeting_recorder.speaker.pyannote_engine import PyannoteSpeakerDiarizationEngine
from local_meeting_recorder.summarization.providers.ollama_client import OllamaSummaryClient
from local_meeting_recorder.transcription.engine import TranscriptionEngine
from local_meeting_recorder.transcription.models import Transcript

try:
    import resource  # POSIX only — no equivalent module on Windows
except ImportError:
    resource = None  # type: ignore[assignment]

try:
    import psutil
except ImportError:
    psutil = None  # optional — see module docstring


@dataclass
class HardwareInfo:
    os: str
    os_version: str
    machine: str
    cpu_model: str | None
    logical_cpu_count: int | None
    total_ram_gb: float | None


@dataclass
class StageResult:
    stage: str
    duration_s: float
    peak_rss_mb: float | None
    cpu_time_s: float | None
    average_cpu_percent: float | None
    detail: dict[str, object] = field(default_factory=dict)


def _cpu_model() -> str | None:
    if sys.platform == "darwin":
        try:
            return subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            return None
    return platform.processor() or None


def _total_ram_gb() -> float | None:
    if sys.platform == "darwin":
        try:
            raw = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout.strip()
            return round(int(raw) / (1024**3), 1)
        except (OSError, subprocess.CalledProcessError, ValueError):
            return None
    if psutil is not None:
        return round(psutil.virtual_memory().total / (1024**3), 1)
    return None


def gather_hardware_info() -> HardwareInfo:
    logical_cpus = psutil.cpu_count(logical=True) if psutil is not None else None
    if logical_cpus is None:
        logical_cpus = os.cpu_count()
    return HardwareInfo(
        os=platform.system(),
        os_version=platform.platform(),
        machine=platform.machine(),
        cpu_model=_cpu_model(),
        logical_cpu_count=logical_cpus,
        total_ram_gb=_total_ram_gb(),
    )


def _peak_rss_mb() -> float | None:
    """``ru_maxrss`` is bytes on macOS but KB on Linux — handle both. Unavailable on
    Windows (no ``resource`` module there); returns ``None`` honestly rather than a
    wrong number."""
    if resource is None:
        return None
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return usage / (1024 * 1024) if sys.platform == "darwin" else usage / 1024


def _cpu_time_s() -> float | None:
    if resource is None:
        return None
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


def _run_stage(stage: str, fn: object, *, detail: dict[str, object] | None = None) -> StageResult:
    cpu_before = _cpu_time_s()
    sampler = None
    samples: list[float] = []
    if psutil is not None:
        process = psutil.Process()
        process.cpu_percent(interval=None)  # prime the counter

        def sample() -> None:
            samples.append(process.cpu_percent(interval=0.2))

        stop = threading.Event()

        def loop() -> None:
            while not stop.is_set():
                sample()

        sampler = threading.Thread(target=loop, daemon=True)
        sampler.start()

    start = time.monotonic()
    fn()  # type: ignore[operator]
    duration = time.monotonic() - start

    if sampler is not None:
        stop.set()
        sampler.join(timeout=1.0)

    cpu_after = _cpu_time_s()
    cpu_delta = (
        (cpu_after - cpu_before) if (cpu_before is not None and cpu_after is not None) else None
    )
    average_cpu = sum(samples) / len(samples) if samples else None
    return StageResult(
        stage=stage,
        duration_s=round(duration, 3),
        peak_rss_mb=(round(rss, 1) if (rss := _peak_rss_mb()) is not None else None),
        cpu_time_s=(round(cpu_delta, 3) if cpu_delta is not None else None),
        average_cpu_percent=(round(average_cpu, 1) if average_cpu is not None else None),
        detail=detail or {},
    )


def _synthesize_speech_wav(path: Path, *, minutes: float) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("--synthesize-speech-minutes uses macOS's `say`; not available here")
    words_per_minute = 160  # a conservative, documented estimate for macOS `say`'s default rate
    sentence = (
        "The quarterly roadmap review continues as the team discusses budget allocation "
        "timelines and the upcoming product launch milestones for next quarter. "
    )
    target_words = int(words_per_minute * minutes)
    text = sentence * ((target_words // len(sentence.split())) + 1)
    aiff_path = path.with_suffix(".aiff")
    subprocess.run(
        ["say", "-o", str(aiff_path), text], check=True, timeout=max(60, int(minutes * 60))
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(aiff_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            str(path),
        ],
        check=True,
        timeout=120,
    )


def benchmark_transcription(
    audio_path: Path, *, whisper_model: str, device: str, compute_type: str
) -> StageResult:
    engine = TranscriptionEngine(model_size=whisper_model, device=device, compute_type=compute_type)
    result_holder: dict[str, object] = {}

    def run() -> None:
        result = engine.transcribe(audio_path)
        result_holder["segments"] = len(result.segments)
        result_holder["audio_duration_s"] = result.duration_s

    stage = _run_stage(
        f"transcription ({whisper_model}, {device}/{compute_type})",
        run,
        detail={"whisper_model": whisper_model, "device": device, "compute_type": compute_type},
    )
    stage.detail.update(result_holder)
    return stage


def benchmark_diarization(audio_path: Path, *, hf_token: str) -> StageResult:
    engine = PyannoteSpeakerDiarizationEngine(hf_token=hf_token)
    result_holder: dict[str, object] = {}

    def run() -> None:
        result = engine.diarize(audio_path)
        result_holder["speaker_count"] = result.speaker_count
        result_holder["region_count"] = len(result.regions)

    stage = _run_stage("diarization (pyannote, cpu)", run)
    stage.detail.update(result_holder)
    return stage


def benchmark_summarization(
    transcript_path: Path, *, ollama_model: str, ollama_base_url: str
) -> StageResult:
    transcript = Transcript.model_validate_json(transcript_path.read_text(encoding="utf-8"))
    client = OllamaSummaryClient(base_url=ollama_base_url, model=ollama_model)
    result_holder: dict[str, object] = {}

    def run() -> None:
        summary = client.generate_summary(transcript.text_with_segment_indices)
        result_holder["title"] = summary.title

    stage = _run_stage(f"summarization (ollama/{ollama_model})", run)
    stage.detail.update(result_holder)
    return stage


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--transcribe", type=Path, help="Path to an audio/video file to transcribe")
    parser.add_argument(
        "--synthesize-speech-minutes",
        type=float,
        help="macOS only: synthesize this many minutes of speech via `say` and transcribe it",
    )
    parser.add_argument("--whisper-model", default="base")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--diarize", type=Path, help="Path to a WAV file to diarize")
    parser.add_argument("--hf-token", default=None, help="Hugging Face token for --diarize")
    parser.add_argument(
        "--summarize-transcript", type=Path, help="Path to a transcript.json to summarize"
    )
    parser.add_argument("--ollama-model", default="llama3.1")
    parser.add_argument("--ollama-base-url", default="http://127.0.0.1:11434")
    parser.add_argument(
        "--json-out", type=Path, default=None, help="Write the full report as JSON here"
    )
    args = parser.parse_args()

    hardware = gather_hardware_info()
    results: list[StageResult] = []
    not_run: list[str] = []

    if args.synthesize_speech_minutes:
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "benchmark_speech.wav"
            print(f"synthesizing ~{args.synthesize_speech_minutes} minutes of speech via `say`...")
            _synthesize_speech_wav(wav_path, minutes=args.synthesize_speech_minutes)
            results.append(
                benchmark_transcription(
                    wav_path,
                    whisper_model=args.whisper_model,
                    device=args.device,
                    compute_type=args.compute_type,
                )
            )
    elif args.transcribe:
        results.append(
            benchmark_transcription(
                args.transcribe,
                whisper_model=args.whisper_model,
                device=args.device,
                compute_type=args.compute_type,
            )
        )
    else:
        not_run.append("transcription (no --transcribe or --synthesize-speech-minutes given)")

    if args.diarize:
        if not args.hf_token:
            not_run.append("diarization (--diarize given but no --hf-token)")
        else:
            results.append(benchmark_diarization(args.diarize, hf_token=args.hf_token))
    else:
        not_run.append("diarization (no --diarize given)")

    if args.summarize_transcript:
        results.append(
            benchmark_summarization(
                args.summarize_transcript,
                ollama_model=args.ollama_model,
                ollama_base_url=args.ollama_base_url,
            )
        )
    else:
        not_run.append("summarization (no --summarize-transcript given)")

    report = {
        "hardware": asdict(hardware),
        "psutil_available": psutil is not None,
        "results": [asdict(r) for r in results],
        "not_run": not_run,
    }
    print(json.dumps(report, indent=2))
    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    if not_run:
        print("\nNOT RUN (see above) — do not report these as measured:", file=sys.stderr)
        for item in not_run:
            print(f"  - {item}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
