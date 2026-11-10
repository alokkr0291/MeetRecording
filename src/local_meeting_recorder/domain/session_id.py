"""Session identifiers: sortable, filesystem-safe, unique."""

from __future__ import annotations

import re
import secrets
from datetime import datetime
from typing import NewType

SessionId = NewType("SessionId", str)

_PATTERN = re.compile(r"^lmr_\d{8}T\d{6}_[0-9a-f]{4}$")


def new_session_id(now: datetime | None = None) -> SessionId:
    """Return an id such as ``lmr_20261015T143022_a3f9``.

    The timestamp part uses local time so directory names match what the user sees in
    the UI; the random suffix prevents collisions when two sessions start in the same
    second (for example in tests).
    """
    stamp = (now or datetime.now().astimezone()).strftime("%Y%m%dT%H%M%S")
    return SessionId(f"lmr_{stamp}_{secrets.token_hex(2)}")


def is_valid_session_id(value: str) -> bool:
    return bool(_PATTERN.match(value))
