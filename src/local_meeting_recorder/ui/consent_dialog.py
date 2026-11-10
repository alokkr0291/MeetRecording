"""Pre-recording consent: one popup, explicit OK, shown fresh for every session.

See SECURITY_AND_PRIVACY.md — the app cannot verify that other participants were
informed; this dialog states that the responsibility stays with the user.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

CONSENT_TITLE = "Start Recording?"


def consent_text() -> str:
    return (
        "This will record your screen, system audio, and microphone.\n\n"
        "Make sure anyone else in the meeting has been informed, as required by your "
        "local consent laws and workplace policy.\n\n"
        "Recordings are saved only on this Mac."
    )


def request_consent(parent: QWidget | None = None) -> bool:
    """Show the consent popup and return True only if the user clicked OK."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle(CONSENT_TITLE)
    box.setText(consent_text())
    box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    return box.exec() == QMessageBox.StandardButton.Ok.value
