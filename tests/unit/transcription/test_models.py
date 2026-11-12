"""Transcript/TranscriptSegment: validation and JSON round-trip."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from local_meeting_recorder.transcription.models import (
    UNKNOWN_SPEAKER_ID,
    USER_SPEAKER_ID,
    AudioSource,
    Speaker,
    Transcript,
    TranscriptSegment,
)


def _segment(start: float, end: float, text: str = "hello") -> TranscriptSegment:
    return TranscriptSegment(start_s=start, end_s=end, text=text)


def test_valid_segments_round_trip_through_json() -> None:
    transcript = Transcript(
        session_id="lmr_20261110T000000_aaaa",
        language="en",
        language_probability=0.98,
        model_name="small",
        segments=[_segment(0.0, 1.5, "hello"), _segment(1.5, 3.0, "world")],
    )
    restored = Transcript.model_validate_json(transcript.model_dump_json())
    assert restored == transcript
    assert restored.text == "hello\nworld"


def test_segment_end_before_start_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Transcript(
            session_id="s",
            language="en",
            model_name="small",
            segments=[_segment(2.0, 1.0)],
        )


def test_out_of_order_segments_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Transcript(
            session_id="s",
            language="en",
            model_name="small",
            segments=[_segment(5.0, 6.0), _segment(1.0, 2.0)],
        )


def test_empty_segments_produce_empty_text() -> None:
    transcript = Transcript(session_id="s", language="en", model_name="small", segments=[])
    assert transcript.text == ""


def test_blank_segment_text_is_skipped_in_joined_text() -> None:
    transcript = Transcript(
        session_id="s",
        language="en",
        model_name="small",
        segments=[_segment(0.0, 1.0, "  "), _segment(1.0, 2.0, "real text")],
    )
    assert transcript.text == "real text"


def test_segment_is_frozen() -> None:
    segment = _segment(0.0, 1.0)
    with pytest.raises(ValidationError):
        segment.text = "changed"  # type: ignore[misc]


def test_segment_source_defaults_to_none() -> None:
    assert _segment(0.0, 1.0).source is None


def test_text_prefixes_source_tagged_segments() -> None:
    transcript = Transcript(
        session_id="s",
        language="en",
        model_name="small",
        segments=[
            TranscriptSegment(
                start_s=0.0, end_s=1.0, text="hello everyone", source=AudioSource.SYSTEM
            ),
            TranscriptSegment(
                start_s=0.5, end_s=1.2, text="thanks for joining", source=AudioSource.MICROPHONE
            ),
        ],
    )
    assert transcript.text == "[System] hello everyone\n[Mic] thanks for joining"


def test_text_mixes_tagged_and_untagged_segments() -> None:
    transcript = Transcript(
        session_id="s",
        language="en",
        model_name="small",
        segments=[
            TranscriptSegment(start_s=0.0, end_s=1.0, text="untagged"),
            TranscriptSegment(start_s=1.0, end_s=2.0, text="tagged", source=AudioSource.SYSTEM),
        ],
    )
    assert transcript.text == "untagged\n[System] tagged"


def test_text_with_segment_indices_numbers_by_list_position() -> None:
    transcript = Transcript(
        session_id="s",
        language="en",
        model_name="small",
        segments=[
            TranscriptSegment(start_s=0.0, end_s=1.0, text="hello", source=AudioSource.SYSTEM),
            TranscriptSegment(
                start_s=1.0, end_s=2.0, text="hi there", source=AudioSource.MICROPHONE
            ),
        ],
    )
    assert transcript.text_with_segment_indices == "[0] System: hello\n[1] Mic: hi there"


def test_text_with_segment_indices_skips_blank_segments_but_keeps_real_positions() -> None:
    """A blank segment is omitted from the rendered lines, exactly like ``text``, but
    the surviving indices must still match the segment's real position in ``segments``
    — a summary provider citing index 2 must resolve to the actual third segment."""
    transcript = Transcript(
        session_id="s",
        language="en",
        model_name="small",
        segments=[
            TranscriptSegment(start_s=0.0, end_s=1.0, text="first"),
            TranscriptSegment(start_s=1.0, end_s=2.0, text="   "),  # blank
            TranscriptSegment(start_s=2.0, end_s=3.0, text="third"),
        ],
    )
    assert transcript.text_with_segment_indices == "[0] first\n[2] third"


def test_source_tagged_segments_round_trip_through_json() -> None:
    transcript = Transcript(
        session_id="s",
        language="en",
        model_name="small",
        segments=[
            TranscriptSegment(start_s=0.0, end_s=1.0, text="a", source=AudioSource.SYSTEM),
            TranscriptSegment(start_s=0.5, end_s=1.5, text="b", source=AudioSource.MICROPHONE),
        ],
    )
    restored = Transcript.model_validate_json(transcript.model_dump_json())
    assert restored == transcript
    assert restored.segments[0].source is AudioSource.SYSTEM
    assert restored.segments[1].source is AudioSource.MICROPHONE


def test_old_transcript_json_without_source_field_still_loads() -> None:
    """Backward compatibility: a Phase 4 transcript.json has no 'source' key at all."""
    old_json = (
        '{"schema_version": 1, "session_id": "s", "language": "en", "model_name": "small", '
        '"created_at": "2026-11-04T00:00:00Z", '
        '"segments": [{"start_s": 0.0, "end_s": 1.0, "text": "hello"}]}'
    )
    transcript = Transcript.model_validate_json(old_json)
    assert transcript.segments[0].source is None
    assert transcript.text == "hello"


def test_overlapping_cross_source_segments_are_allowed() -> None:
    """Real meetings have overlapping speech; only non-decreasing *start* time is
    required, not non-overlap."""
    transcript = Transcript(
        session_id="s",
        language="en",
        model_name="small",
        segments=[
            TranscriptSegment(start_s=0.0, end_s=5.0, text="long", source=AudioSource.SYSTEM),
            TranscriptSegment(
                start_s=1.0, end_s=2.0, text="interjection", source=AudioSource.MICROPHONE
            ),
        ],
    )
    assert len(transcript.segments) == 2


# -- speakers (Phase 5C) --------------------------------------------------------------------


def test_segment_speaker_id_defaults_to_none() -> None:
    assert _segment(0.0, 1.0).speaker_id is None


def test_speaker_is_frozen() -> None:
    speaker = Speaker(
        speaker_id=USER_SPEAKER_ID, display_name="You", source_type=AudioSource.MICROPHONE
    )
    with pytest.raises(ValidationError):
        speaker.display_name = "Someone else"  # type: ignore[misc]


def test_text_uses_speaker_display_name_over_source_label() -> None:
    transcript = Transcript(
        session_id="s",
        language="en",
        model_name="small",
        speakers=[
            Speaker(
                speaker_id=USER_SPEAKER_ID, display_name="You", source_type=AudioSource.MICROPHONE
            ),
            Speaker(speaker_id="speaker_1", display_name="Alex", source_type=AudioSource.SYSTEM),
        ],
        segments=[
            TranscriptSegment(
                start_s=0.0,
                end_s=1.0,
                text="let's discuss the deployment",
                source=AudioSource.MICROPHONE,
                speaker_id=USER_SPEAKER_ID,
            ),
            TranscriptSegment(
                start_s=1.0,
                end_s=2.0,
                text="i agree with that approach",
                source=AudioSource.SYSTEM,
                speaker_id="speaker_1",
            ),
        ],
    )
    assert transcript.text == (
        "[You] let's discuss the deployment\n[Alex] i agree with that approach"
    )


def test_text_falls_back_to_source_label_when_speaker_unresolved() -> None:
    """Diarization hasn't run (or found nothing for this segment): speaker_id is None,
    so the source-level [System]/[Mic] label is still shown."""
    transcript = Transcript(
        session_id="s",
        language="en",
        model_name="small",
        segments=[
            TranscriptSegment(start_s=0.0, end_s=1.0, text="hello", source=AudioSource.SYSTEM),
        ],
    )
    assert transcript.text == "[System] hello"


def test_unknown_speaker_shown_when_present_in_speakers_list() -> None:
    transcript = Transcript(
        session_id="s",
        language="en",
        model_name="small",
        speakers=[
            Speaker(
                speaker_id=UNKNOWN_SPEAKER_ID,
                display_name="Unidentified speaker",
                source_type=AudioSource.SYSTEM,
            ),
        ],
        segments=[
            TranscriptSegment(
                start_s=0.0,
                end_s=1.0,
                text="mumbling",
                source=AudioSource.SYSTEM,
                speaker_id=UNKNOWN_SPEAKER_ID,
            ),
        ],
    )
    assert transcript.text == "[Unidentified speaker] mumbling"


def test_speakers_and_diarization_model_round_trip_through_json() -> None:
    transcript = Transcript(
        session_id="s",
        language="en",
        model_name="small",
        diarization_model="pyannote/speaker-diarization-3.1",
        speakers=[
            Speaker(
                speaker_id=USER_SPEAKER_ID, display_name="You", source_type=AudioSource.MICROPHONE
            ),
        ],
        segments=[
            TranscriptSegment(
                start_s=0.0, end_s=1.0, text="hi", source=AudioSource.MICROPHONE, speaker_id="user"
            ),
        ],
    )
    restored = Transcript.model_validate_json(transcript.model_dump_json())
    assert restored == transcript
    assert restored.diarization_model == "pyannote/speaker-diarization-3.1"


def test_old_transcript_without_speakers_still_loads() -> None:
    """Backward compatibility: a Phase 4/5A/B transcript.json has no 'speakers' key."""
    old_json = (
        '{"schema_version": 1, "session_id": "s", "language": "en", "model_name": "small", '
        '"created_at": "2026-11-04T00:00:00Z", '
        '"segments": [{"start_s": 0.0, "end_s": 1.0, "text": "hello"}]}'
    )
    transcript = Transcript.model_validate_json(old_json)
    assert transcript.speakers == []
    assert transcript.diarization_model is None
    assert transcript.segments[0].speaker_id is None
