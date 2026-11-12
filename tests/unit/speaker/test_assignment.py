"""speaker/assignment.py: pure logic, no pyannote/torch needed to test it."""

from __future__ import annotations

from local_meeting_recorder.speaker.assignment import assign_system_speakers, assign_user_speaker
from local_meeting_recorder.speaker.protocol import DiarizationResult, SpeakerRegion
from local_meeting_recorder.transcription.models import (
    UNKNOWN_SPEAKER_ID,
    USER_SPEAKER_ID,
    AudioSource,
    TranscriptSegment,
)


def _segment(start: float, end: float, text: str = "hi") -> TranscriptSegment:
    return TranscriptSegment(start_s=start, end_s=end, text=text, source=AudioSource.SYSTEM)


# -- assign_user_speaker ----------------------------------------------------------------


def test_assign_user_speaker_tags_every_segment() -> None:
    segments = [_segment(0.0, 1.0), _segment(1.0, 2.0)]
    tagged, speaker = assign_user_speaker(segments)
    assert all(s.speaker_id == USER_SPEAKER_ID for s in tagged)
    assert speaker is not None
    assert speaker.speaker_id == USER_SPEAKER_ID
    assert speaker.source_type is AudioSource.MICROPHONE


def test_assign_user_speaker_with_no_segments_returns_no_speaker() -> None:
    tagged, speaker = assign_user_speaker([])
    assert tagged == []
    assert speaker is None


def test_assign_user_speaker_preserves_everything_else() -> None:
    original = _segment(0.0, 1.0, "hello")
    tagged, _speaker = assign_user_speaker([original])
    assert tagged[0].start_s == original.start_s
    assert tagged[0].end_s == original.end_s
    assert tagged[0].text == original.text


# -- assign_system_speakers ---------------------------------------------------------------


def test_assigns_segment_to_the_overlapping_region() -> None:
    segments = [_segment(0.0, 2.0), _segment(2.0, 4.0)]
    diarization = DiarizationResult(
        regions=[
            SpeakerRegion(start_s=0.0, end_s=2.0, speaker_label="SPEAKER_00"),
            SpeakerRegion(start_s=2.0, end_s=4.0, speaker_label="SPEAKER_01"),
        ],
        speaker_count=2,
        model_name="pyannote/speaker-diarization-3.1",
    )
    tagged, speakers = assign_system_speakers(segments, diarization)
    assert [s.speaker_id for s in tagged] == ["speaker_1", "speaker_2"]
    assert {s.speaker_id for s in speakers} == {"speaker_1", "speaker_2"}
    assert all(s.source_type is AudioSource.SYSTEM for s in speakers)


def test_speaker_ids_are_renumbered_by_first_appearance_not_raw_label_order() -> None:
    """Raw backend labels can appear in any order; the first speaker to actually speak
    becomes speaker_1, regardless of whether its raw label sorts first."""
    segments = [_segment(0.0, 1.0)]
    diarization = DiarizationResult(
        regions=[
            SpeakerRegion(start_s=5.0, end_s=6.0, speaker_label="SPEAKER_00"),  # speaks later
            SpeakerRegion(start_s=0.0, end_s=1.0, speaker_label="SPEAKER_05"),  # speaks first
        ],
        speaker_count=2,
        model_name="m",
    )
    tagged, _speakers = assign_system_speakers(segments, diarization)
    assert tagged[0].speaker_id == "speaker_1"  # SPEAKER_05, which spoke first


def test_segment_with_no_overlapping_region_is_unknown() -> None:
    segments = [_segment(10.0, 11.0)]
    diarization = DiarizationResult(
        regions=[SpeakerRegion(start_s=0.0, end_s=1.0, speaker_label="SPEAKER_00")],
        speaker_count=1,
        model_name="m",
    )
    tagged, speakers = assign_system_speakers(segments, diarization)
    assert tagged[0].speaker_id == UNKNOWN_SPEAKER_ID
    assert any(s.speaker_id == UNKNOWN_SPEAKER_ID for s in speakers)


def test_speakers_list_omits_unknown_entry_when_never_used() -> None:
    segments = [_segment(0.0, 1.0)]
    diarization = DiarizationResult(
        regions=[SpeakerRegion(start_s=0.0, end_s=1.0, speaker_label="SPEAKER_00")],
        speaker_count=1,
        model_name="m",
    )
    _tagged, speakers = assign_system_speakers(segments, diarization)
    assert UNKNOWN_SPEAKER_ID not in {s.speaker_id for s in speakers}


def test_picks_the_region_with_the_largest_overlap_for_overlapping_speech() -> None:
    """Multiple regions can overlap one Whisper segment (two people talking over each
    other); the dominant (longest-overlap) speaker wins rather than crashing or
    splitting the segment."""
    segment = _segment(0.0, 4.0)
    diarization = DiarizationResult(
        regions=[
            SpeakerRegion(start_s=0.0, end_s=1.0, speaker_label="SPEAKER_00"),  # 1s overlap
            SpeakerRegion(start_s=0.5, end_s=4.0, speaker_label="SPEAKER_01"),  # 3.5s overlap
        ],
        speaker_count=2,
        model_name="m",
    )
    tagged, _speakers = assign_system_speakers([segment], diarization)
    assert tagged[0].speaker_id == "speaker_2"  # SPEAKER_01 spoke first chronologically? no:
    # SPEAKER_00 starts at 0.0 (first appearance) -> speaker_1; SPEAKER_01 -> speaker_2;
    # the segment is still assigned to whichever has the larger overlap (SPEAKER_01).


def test_multiple_segments_map_to_multiple_distinct_speakers() -> None:
    segments = [_segment(0.0, 1.0), _segment(1.0, 2.0), _segment(2.0, 3.0)]
    diarization = DiarizationResult(
        regions=[
            SpeakerRegion(start_s=0.0, end_s=1.0, speaker_label="SPEAKER_00"),
            SpeakerRegion(start_s=1.0, end_s=2.0, speaker_label="SPEAKER_01"),
            SpeakerRegion(start_s=2.0, end_s=3.0, speaker_label="SPEAKER_00"),
        ],
        speaker_count=2,
        model_name="m",
    )
    tagged, speakers = assign_system_speakers(segments, diarization)
    assert [s.speaker_id for s in tagged] == ["speaker_1", "speaker_2", "speaker_1"]
    assert len(speakers) == 2


def test_no_regions_at_all_marks_every_segment_unknown() -> None:
    segments = [_segment(0.0, 1.0)]
    diarization = DiarizationResult(regions=[], speaker_count=0, model_name="m")
    tagged, speakers = assign_system_speakers(segments, diarization)
    assert tagged[0].speaker_id == UNKNOWN_SPEAKER_ID
    assert len(speakers) == 1
