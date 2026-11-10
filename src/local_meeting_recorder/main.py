"""Entry point: ``python -m local_meeting_recorder.main`` or the ``local-meeting-recorder``
console script (see ``pyproject.toml``). Loads settings, configures logging, enforces a
single running instance, picks the platform capture backend, and runs the Qt app.
"""

from __future__ import annotations

import sys

from local_meeting_recorder.app import Application
from local_meeting_recorder.capture.factory import default_backend_factory
from local_meeting_recorder.config import Settings, load_settings
from local_meeting_recorder.infrastructure.logging_setup import configure_logging, get_logger
from local_meeting_recorder.infrastructure.single_instance import (
    AlreadyRunningError,
    SingleInstanceGuard,
)
from local_meeting_recorder.storage.paths import StoragePaths
from local_meeting_recorder.storage.session_store import SessionStore

log = get_logger(__name__)


def main() -> int:
    settings = load_settings()
    paths = StoragePaths.from_settings_dir(settings.data_dir)
    paths.ensure()
    configure_logging(
        log_dir=paths.logs_dir,
        level=settings.log_level,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
    )

    try:
        with SingleInstanceGuard(paths.root / "app.lock"):
            return _run(paths, settings)
    except AlreadyRunningError:
        log.error("another instance of LocalMeetingRecorder is already running")
        print("LocalMeetingRecorder is already running.", file=sys.stderr)
        return 1


def _run(paths: StoragePaths, settings: Settings) -> int:
    store = SessionStore(paths)
    backend_factory = default_backend_factory(
        helper_path=settings.helper_path, helper_log_path=paths.logs_dir / "helper.log"
    )
    app = Application(backend_factory=backend_factory, session_store=store, settings=settings)
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
