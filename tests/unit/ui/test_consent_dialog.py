"""Only the pure text-building logic is unit-tested; the real modal ``QMessageBox.exec()``
is exercised through AppController's injected ``consent_fn`` seam instead (see
test_app_controller.py) — driving a real native modal loop in headless CI is both
impractical and not where the risk is: the risk is "did we show it and respect the
answer", which the injection point proves.
"""

from __future__ import annotations

from local_meeting_recorder.ui.consent_dialog import consent_text


def test_consent_text_mentions_everything_that_will_be_captured() -> None:
    text = consent_text().lower()
    assert "screen" in text
    assert "system audio" in text or "audio" in text
    assert "microphone" in text


def test_consent_text_states_recordings_stay_local() -> None:
    assert "this mac" in consent_text().lower()
