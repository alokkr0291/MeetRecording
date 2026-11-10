"""Structured JSON logging with session context.

Rules enforced by convention (and reviewed in CLAUDE.md): log identifiers, states, timings,
device names, and error codes. Never log audio samples, transcript or summary text, or
credentials.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_session_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "lmr_session_id", default=None
)

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


def current_session_id() -> str | None:
    return _session_id_var.get()


@contextmanager
def bind_session(session_id: str | None) -> Iterator[None]:
    """Attach a session id to every log record emitted inside the block."""
    token = _session_id_var.set(session_id)
    try:
        yield
    finally:
        _session_id_var.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        session_id = getattr(record, "session_id", None) or _session_id_var.get()
        if session_id:
            payload["session_id"] = session_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key != "session_id" and not key.startswith("_"):
                payload[key] = _jsonable(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        session_id = getattr(record, "session_id", None) or _session_id_var.get()
        prefix = f"[{session_id}] " if session_id else ""
        base = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} {record.name}: "
        text = base + prefix + record.getMessage()
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        return text


def _jsonable(value: object) -> object:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict | list | tuple):
        return value
    return repr(value)


def configure_logging(
    *,
    log_dir: Path | None,
    level: str = "INFO",
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 10,
    console: bool = True,
) -> None:
    """Install handlers on the ``local_meeting_recorder`` logger hierarchy (idempotent)."""
    root = logging.getLogger("local_meeting_recorder")
    root.setLevel(level.upper())
    root.propagate = False
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "app.log", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)

    if console:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(HumanFormatter())
        root.addHandler(stream_handler)


def get_logger(name: str) -> logging.Logger:
    if not name.startswith("local_meeting_recorder"):
        name = f"local_meeting_recorder.{name}"
    return logging.getLogger(name)
