"""Mask a secret for display — the UI never shows a full API key once it is saved."""

from __future__ import annotations

_VISIBLE_SUFFIX_LEN = 4


def mask_secret(value: str) -> str:
    """``"sk-abc123xyz987"`` -> ``"••••••••••987"``: all but the last few characters,
    so a saved key can be recognized without being fully displayed. A very short value
    is masked entirely rather than revealing most of it."""
    if len(value) <= _VISIBLE_SUFFIX_LEN:
        return "•" * len(value)
    hidden = len(value) - _VISIBLE_SUFFIX_LEN
    return ("•" * hidden) + value[-_VISIBLE_SUFFIX_LEN:]
