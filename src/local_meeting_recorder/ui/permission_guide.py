"""Permission status display and actionable guidance for Screen Recording + Microphone.

TCC grants are attributed to the responsible process (Terminal/PyCharm in development,
the packaged app once signed). A freshly-granted permission is read correctly by the next
helper launch, but a process already holding a *denied* ScreenCaptureKit handle from
before the grant needs a relaunch — hence "quit and reopen" rather than "just retry".
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

from local_meeting_recorder import APP_DISPLAY_NAME
from local_meeting_recorder.capture.protocols import PermissionStatus
from local_meeting_recorder.ui.shell import Runner, default_runner, open_url

SCREEN_RECORDING_SETTINGS_URL = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
)
MICROPHONE_SETTINGS_URL = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
)


def describe_missing_permissions(status: PermissionStatus) -> str:
    missing = []
    if not status.screen.is_usable:
        missing.append(f"Screen Recording ({status.screen.value})")
    if not status.microphone.is_usable:
        missing.append(f"Microphone ({status.microphone.value})")
    if not missing:
        return "All required permissions are granted."
    return "Missing permission(s): " + ", ".join(missing)


def open_screen_recording_settings(*, runner: Runner = default_runner) -> None:
    open_url(SCREEN_RECORDING_SETTINGS_URL, runner=runner)


def open_microphone_settings(*, runner: Runner = default_runner) -> None:
    open_url(MICROPHONE_SETTINGS_URL, runner=runner)


def show_permission_guide(status: PermissionStatus, parent: QWidget | None = None) -> None:
    """Explain what's missing and offer a button to open the right System Settings pane."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Permission Needed")
    box.setText(
        describe_missing_permissions(status)
        + "\n\nAfter granting access in System Settings, quit and reopen "
        f"{APP_DISPLAY_NAME}, then try again."
    )
    screen_button = (
        box.addButton("Open Screen Recording Settings", QMessageBox.ButtonRole.ActionRole)
        if not status.screen.is_usable
        else None
    )
    mic_button = (
        box.addButton("Open Microphone Settings", QMessageBox.ButtonRole.ActionRole)
        if not status.microphone.is_usable
        else None
    )
    box.addButton(QMessageBox.StandardButton.Ok)
    box.exec()
    clicked = box.clickedButton()
    if screen_button is not None and clicked is screen_button:
        open_screen_recording_settings()
    elif mic_button is not None and clicked is mic_button:
        open_microphone_settings()
