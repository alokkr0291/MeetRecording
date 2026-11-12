"""Read-only transcript viewer, with Retry actions (transcription, speaker
identification) and manual speaker renaming.

One dialog, built fresh each time it's shown — no persistent window to manage in a
menu-bar-only app (same reasoning as ``consent_dialog.py``).
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from local_meeting_recorder.domain.session_metadata import (
    DiarizationStatus,
    SessionMetadata,
    TranscriptionStatus,
)
from local_meeting_recorder.transcription.models import AudioSource, Transcript

RetryFn = Callable[[], None]
RenameSpeakerFn = Callable[[str, str], None]  # speaker_id, new_display_name


def show_transcript_dialog(
    metadata: SessionMetadata,
    transcript: Transcript | None,
    *,
    on_retry: RetryFn | None = None,
    on_retry_diarization: RetryFn | None = None,
    on_rename_speaker: RenameSpeakerFn | None = None,
    on_view_summary: RetryFn | None = None,
    parent: QWidget | None = None,
) -> None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(metadata.title or metadata.session_id)
    dialog.resize(640, 520)
    layout = QVBoxLayout(dialog)

    status = metadata.post_processing.transcript_status
    if status is TranscriptionStatus.FAILED:
        message = metadata.post_processing.transcript_error or "Transcription failed."
        layout.addWidget(QLabel(f"Transcription failed: {message}"))
    elif transcript is not None:
        language = metadata.post_processing.transcript_language or transcript.language
        layout.addWidget(QLabel(f"Detected language: {language}"))
        if status is TranscriptionStatus.PARTIAL and transcript.source_errors:
            failed = ", ".join(
                f"{source.value} ({msg})" for source, msg in transcript.source_errors.items()
            )
            note = QLabel(f"Partial transcript — some audio could not be transcribed: {failed}")
            note.setWordWrap(True)
            layout.addWidget(note)
        body = QPlainTextEdit()
        body.setReadOnly(True)
        body.setPlainText(transcript.text or "(no speech detected)")
        layout.addWidget(body)
        _add_diarization_section(layout, metadata, transcript, on_rename_speaker)
    else:
        layout.addWidget(QLabel("No transcript available yet."))

    layout.addLayout(
        _build_button_row(
            dialog,
            metadata,
            transcript,
            status=status,
            on_retry=on_retry,
            on_retry_diarization=on_retry_diarization,
            on_view_summary=on_view_summary,
        )
    )
    dialog.exec()


def _build_button_row(
    dialog: QDialog,
    metadata: SessionMetadata,
    transcript: Transcript | None,
    *,
    status: TranscriptionStatus,
    on_retry: RetryFn | None,
    on_retry_diarization: RetryFn | None,
    on_view_summary: RetryFn | None,
) -> QHBoxLayout:
    buttons = QHBoxLayout()
    buttons.addStretch(1)
    retryable = (TranscriptionStatus.FAILED, TranscriptionStatus.PARTIAL)
    if status in retryable and on_retry is not None:

        def _retry_and_close() -> None:
            on_retry()
            dialog.accept()

        retry_button = QPushButton("Retry Transcription")
        retry_button.clicked.connect(_retry_and_close)
        buttons.addWidget(retry_button)

    diarization_status = metadata.post_processing.diarization_status
    has_system_audio = transcript is not None and any(
        s.source is AudioSource.SYSTEM for s in transcript.segments
    )
    if (
        diarization_status in (DiarizationStatus.NOT_STARTED, DiarizationStatus.FAILED)
        and has_system_audio
        and on_retry_diarization is not None
    ):

        def _diarize_and_close() -> None:
            on_retry_diarization()
            dialog.accept()

        label = (
            "Retry Speaker ID"
            if diarization_status is DiarizationStatus.FAILED
            else "Identify Speakers"
        )
        diarize_button = QPushButton(label)
        diarize_button.clicked.connect(_diarize_and_close)
        buttons.addWidget(diarize_button)

    if transcript is not None and on_view_summary is not None:

        def _view_summary_and_close() -> None:
            on_view_summary()
            dialog.accept()

        summary_button = QPushButton("Summary…")
        summary_button.clicked.connect(_view_summary_and_close)
        buttons.addWidget(summary_button)

    close_button = QPushButton("Close")
    close_button.clicked.connect(dialog.accept)
    buttons.addWidget(close_button)
    return buttons


def _add_diarization_section(
    layout: QVBoxLayout,
    metadata: SessionMetadata,
    transcript: Transcript,
    on_rename_speaker: RenameSpeakerFn | None,
) -> None:
    """Speaker status + (when any speakers are known) an editable name per speaker —
    "allow users to rename speakers manually," persisted without touching the audio or
    the transcript text itself (only ``Speaker.display_name`` changes)."""
    diarization_status = metadata.post_processing.diarization_status
    if diarization_status is DiarizationStatus.FAILED:
        message = metadata.post_processing.diarization_error or "Speaker identification failed."
        note = QLabel(f"Speaker identification failed: {message}")
        note.setWordWrap(True)
        layout.addWidget(note)
    elif diarization_status is DiarizationStatus.RUNNING:
        layout.addWidget(QLabel("Identifying speakers…"))

    if not transcript.speakers or on_rename_speaker is None:
        return
    form = QFormLayout()
    for speaker in transcript.speakers:
        field = QLineEdit(speaker.display_name)
        save_button = QPushButton("Save")

        def _save(
            checked: bool = False, *, field: QLineEdit = field, speaker_id: str = speaker.speaker_id
        ) -> None:
            new_name = field.text().strip()
            if new_name and on_rename_speaker is not None:
                on_rename_speaker(speaker_id, new_name)

        save_button.clicked.connect(_save)
        row = QHBoxLayout()
        row.addWidget(field, stretch=1)
        row.addWidget(save_button)
        row_widget = QWidget()
        row_widget.setLayout(row)
        form.addRow(f"{speaker.source_type.value}:", row_widget)
    layout.addLayout(form)
