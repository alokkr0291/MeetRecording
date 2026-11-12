"""Pure functions mapping diarization output (or the fixed microphone rule) onto
``TranscriptSegment``s. No I/O, no Qt, no state — easy to unit test exhaustively.
"""

from __future__ import annotations

from local_meeting_recorder.speaker.protocol import DiarizationResult, SpeakerRegion
from local_meeting_recorder.transcription.models import (
    UNKNOWN_SPEAKER_DISPLAY_NAME,
    UNKNOWN_SPEAKER_ID,
    USER_SPEAKER_DISPLAY_NAME,
    USER_SPEAKER_ID,
    AudioSource,
    Speaker,
    TranscriptSegment,
)


def assign_user_speaker(
    segments: list[TranscriptSegment],
) -> tuple[list[TranscriptSegment], Speaker | None]:
    """Every microphone segment always maps to the one reserved user identity —
    unconditional, independent of whether diarization ever runs (Part C: "never assign
    a remote speaker identity to the microphone track")."""
    if not segments:
        return segments, None
    tagged = [seg.model_copy(update={"speaker_id": USER_SPEAKER_ID}) for seg in segments]
    speaker = Speaker(
        speaker_id=USER_SPEAKER_ID,
        display_name=USER_SPEAKER_DISPLAY_NAME,
        source_type=AudioSource.MICROPHONE,
    )
    return tagged, speaker


def assign_system_speakers(
    segments: list[TranscriptSegment], diarization: DiarizationResult
) -> tuple[list[TranscriptSegment], list[Speaker]]:
    """Maps each system segment to whichever diarized speaker region overlaps it the
    most (by duration). A segment with no overlapping region gets ``UNKNOWN_SPEAKER_ID``
    — "do not invent participant identities" means an honest "we don't know" beats a
    guess. Raw backend labels (e.g. ``"SPEAKER_00"``) are renumbered to
    ``"speaker_1"``, ``"speaker_2"``, ... in order of first appearance, for a more
    readable, stable-within-this-recording id."""
    label_map = _renumber_labels(diarization.regions)
    regions = [
        SpeakerRegion(start_s=r.start_s, end_s=r.end_s, speaker_label=label_map[r.speaker_label])
        for r in diarization.regions
    ]

    tagged: list[TranscriptSegment] = []
    used_speaker_ids: set[str] = set()
    used_unknown = False
    for segment in segments:
        best_label = _best_overlapping_label(segment, regions)
        if best_label is None:
            tagged.append(segment.model_copy(update={"speaker_id": UNKNOWN_SPEAKER_ID}))
            used_unknown = True
        else:
            tagged.append(segment.model_copy(update={"speaker_id": best_label}))
            used_speaker_ids.add(best_label)

    speakers = [
        Speaker(
            speaker_id=speaker_id,
            display_name=f"Speaker {speaker_id.rsplit('_', 1)[-1]}",
            source_type=AudioSource.SYSTEM,
        )
        for speaker_id in sorted(used_speaker_ids, key=lambda s: int(s.rsplit("_", 1)[-1]))
    ]
    if used_unknown:
        speakers.append(
            Speaker(
                speaker_id=UNKNOWN_SPEAKER_ID,
                display_name=UNKNOWN_SPEAKER_DISPLAY_NAME,
                source_type=AudioSource.SYSTEM,
            )
        )
    return tagged, speakers


def _renumber_labels(regions: list[SpeakerRegion]) -> dict[str, str]:
    first_appearance: list[str] = []
    for region in sorted(regions, key=lambda r: r.start_s):
        if region.speaker_label not in first_appearance:
            first_appearance.append(region.speaker_label)
    return {raw_label: f"speaker_{i + 1}" for i, raw_label in enumerate(first_appearance)}


def _best_overlapping_label(segment: TranscriptSegment, regions: list[SpeakerRegion]) -> str | None:
    best_label: str | None = None
    best_overlap = 0.0
    for region in regions:
        overlap = _overlap_s(segment.start_s, segment.end_s, region.start_s, region.end_s)
        if overlap > best_overlap:
            best_overlap = overlap
            best_label = region.speaker_label
    return best_label


def _overlap_s(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))
