from __future__ import annotations

import json
import logging
from pathlib import Path

from local_meeting_recorder.infrastructure.logging_setup import (
    JsonFormatter,
    bind_session,
    configure_logging,
    current_session_id,
    get_logger,
)


def test_json_formatter_includes_session_and_extras() -> None:
    formatter = JsonFormatter()
    logger = get_logger("test.fmt")
    with bind_session("lmr_x"):
        assert current_session_id() == "lmr_x"
        record = logger.makeRecord(
            logger.name,
            logging.INFO,
            __file__,
            1,
            "state %s",
            ("recording",),
            None,
            extra={"frames": 12, "path": Path("/tmp/a")},
        )
        payload = json.loads(formatter.format(record))
    assert current_session_id() is None
    assert payload["msg"] == "state recording"
    assert payload["session_id"] == "lmr_x"
    assert payload["frames"] == 12
    # str(Path("/tmp/a")) is "\tmp\a" on Windows — the invariant under test is "a Path
    # extra is serialized via str()", not a specific separator convention.
    assert payload["path"] == str(Path("/tmp/a"))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "local_meeting_recorder.test.fmt"


def test_configure_logging_writes_json_file(tmp_path: Path) -> None:
    configure_logging(log_dir=tmp_path / "logs", level="DEBUG", console=False)
    log = get_logger("test.file")
    log.info("hello", extra={"k": 1})
    for handler in logging.getLogger("local_meeting_recorder").handlers:
        handler.flush()
    lines = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8").strip().splitlines()
    assert any(json.loads(line)["msg"] == "hello" for line in lines)
    # idempotent: reconfiguring does not duplicate handlers
    configure_logging(log_dir=tmp_path / "logs", level="INFO", console=False)
    assert len(logging.getLogger("local_meeting_recorder").handlers) == 1
    configure_logging(log_dir=None, console=False)
