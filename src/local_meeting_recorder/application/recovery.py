"""Startup recovery: resolve sessions a crash or force-quit left ``in_progress``.

Never deletes or overwrites a recording file — this only updates each session's own
``session.json`` status so the application (and, later, the UI) can tell the user what
happened. See SECURITY_AND_PRIVACY.md: "never silently overwrite or delete existing
recordings."
"""

from __future__ import annotations

from local_meeting_recorder.domain.session_metadata import SessionMetadata, SessionStatus, utc_now
from local_meeting_recorder.infrastructure.logging_setup import get_logger
from local_meeting_recorder.storage.session_store import SessionStore

log = get_logger(__name__)


def recover_incomplete_sessions(store: SessionStore) -> list[SessionMetadata]:
    """Resolve every session still marked ``in_progress`` and persist its new status.

    A session whose recording file exists and is non-empty is marked ``recovered`` — the
    file itself is left exactly as the helper/OS left it (possibly a truncated but
    readable fragmented ``.mov``). One with no recording file at all is marked
    ``unrecoverable``. Intended to be called once, at application startup, before any new
    recording can begin.
    """
    resolved: list[SessionMetadata] = []
    for metadata in store.find_incomplete():
        recording_path = store.recording_path(metadata)
        has_recording = recording_path.is_file() and recording_path.stat().st_size > 0
        metadata.finalized_at = utc_now()
        if has_recording:
            metadata.status = SessionStatus.RECOVERED
            metadata.error_code = "interrupted"
            metadata.error_message = (
                "The application exited before this recording finished. The file may be "
                "truncated but has not been modified."
            )
        else:
            metadata.status = SessionStatus.UNRECOVERABLE
            metadata.error_code = "no_recording_file"
            metadata.error_message = "The application exited before any media was written."
        store.write(metadata)
        log.warning(
            "recovered incomplete session",
            extra={"session_id": metadata.session_id, "status": metadata.status.value},
        )
        resolved.append(metadata)
    return resolved
