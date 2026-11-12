"""``sanitize_error_detail``: bounding how much of a provider's raw HTTP error body ends
up in an exception message (and therefore in logs)."""

from __future__ import annotations

from local_meeting_recorder.domain.errors import sanitize_error_detail


def test_short_detail_is_returned_unchanged() -> None:
    assert sanitize_error_detail("invalid request") == "invalid request"


def test_long_detail_is_truncated() -> None:
    detail = "x" * 1000
    result = sanitize_error_detail(detail, max_length=300)
    assert len(result) < 1000
    assert result.endswith("… (truncated)")
    assert result.startswith("x" * 300)


def test_embedded_newlines_and_whitespace_are_collapsed() -> None:
    detail = "line one\nline two\r\n\tline three"
    result = sanitize_error_detail(detail)
    assert "\n" not in result
    assert "\r" not in result
    assert result == "line one line two line three"
