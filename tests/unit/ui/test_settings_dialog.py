"""SettingsDialog: built directly (never ``.exec()``'d, which would block) so tests can
inspect and drive its widgets the same way ``test_tray.py`` drives ``TrayController``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMessageBox

from local_meeting_recorder import __version__
from local_meeting_recorder.infrastructure.credentials.memory import InMemoryCredentialStore
from local_meeting_recorder.infrastructure.resource_governor import ResourceGovernor
from local_meeting_recorder.settings.models import ProcessingProfile, TranscriptionProviderId
from local_meeting_recorder.settings.service import SettingsService
from local_meeting_recorder.storage.paths import StoragePaths
from local_meeting_recorder.summarization.protocol import SummaryConnectionTestResult
from local_meeting_recorder.summarization.providers.ollama_client import OllamaSummaryClient
from local_meeting_recorder.transcription.providers.factory import (
    OPENAI_API_KEY_CREDENTIAL,
    OPENROUTER_API_KEY_CREDENTIAL,
)
from local_meeting_recorder.transcription.providers.openai_client import OpenAITranscriptionClient
from local_meeting_recorder.transcription.providers.protocol import ConnectionTestResult
from local_meeting_recorder.ui.settings_dialog import SettingsDialog, SettingsDialogContext


class _FakeTranscriptionManager:
    def __init__(self) -> None:
        self.invalidate_calls = 0

    def invalidate_cached_engine(self) -> None:
        self.invalidate_calls += 1


def _build(
    data_dir: Path,
) -> tuple[SettingsDialog, SettingsDialogContext, _FakeTranscriptionManager]:
    paths = StoragePaths(root=data_dir)
    paths.ensure()
    manager = _FakeTranscriptionManager()
    context = SettingsDialogContext(
        settings_service=SettingsService(data_dir / "settings.json"),
        credential_store=InMemoryCredentialStore(),
        storage_paths=paths,
        transcription_manager=manager,  # type: ignore[arg-type]
        resource_governor=ResourceGovernor(),
    )
    dialog = SettingsDialog(context)
    dialog.show()  # isVisible() below needs the window actually shown (offscreen QPA)
    return dialog, context, manager


def test_general_tab_shows_current_recordings_dir(qapp: QApplication, data_dir: Path) -> None:
    dialog, _context, _manager = _build(data_dir)
    assert dialog._dir_field.text() == str(data_dir / "recordings")


def test_browse_to_a_valid_directory_applies_and_persists(
    qapp: QApplication, data_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dialog, context, _manager = _build(data_dir)
    new_dir = tmp_path / "chosen-recordings"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(new_dir))

    dialog._on_browse_recordings_dir()

    assert context.storage_paths.recordings_dir == new_dir
    assert dialog._dir_field.text() == str(new_dir)
    assert context.settings_service.current.recordings_dir == new_dir


def test_browse_cancelled_does_nothing(
    qapp: QApplication, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog, context, _manager = _build(data_dir)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "")
    original = context.storage_paths.recordings_dir

    dialog._on_browse_recordings_dir()
    assert context.storage_paths.recordings_dir == original


def test_browse_to_an_unusable_path_shows_error_and_does_not_apply(
    qapp: QApplication, data_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dialog, context, _manager = _build(data_dir)
    not_a_dir = tmp_path / "a_file"
    not_a_dir.write_text("not a directory")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(not_a_dir))
    original = context.storage_paths.recordings_dir

    dialog._on_browse_recordings_dir()

    assert context.storage_paths.recordings_dir == original
    assert "Could not use" in dialog._dir_status_label.text()


def test_transcription_tab_defaults_to_local_whisper(qapp: QApplication, data_dir: Path) -> None:
    dialog, _context, _manager = _build(data_dir)
    dialog._tabs.setCurrentIndex(1)  # "Transcription" — isVisible() needs the active tab
    assert dialog._selected_provider() is TranscriptionProviderId.LOCAL_WHISPER
    assert dialog._whisper_model_combo.isVisible()
    assert not dialog._api_key_field.isVisible()


def test_switching_to_openai_shows_api_key_field_and_persists_and_invalidates(
    qapp: QApplication, data_dir: Path
) -> None:
    dialog, context, manager = _build(data_dir)
    dialog._tabs.setCurrentIndex(1)  # "Transcription" — isVisible() needs the active tab
    index = list(TranscriptionProviderId).index(TranscriptionProviderId.OPENAI)
    dialog._provider_combo.setCurrentIndex(index)

    assert context.settings_service.current.transcription_provider is TranscriptionProviderId.OPENAI
    assert manager.invalidate_calls >= 1
    assert dialog._api_key_field.isVisible()
    assert not dialog._whisper_model_combo.isVisible()


def test_whisper_model_change_persists_and_invalidates(qapp: QApplication, data_dir: Path) -> None:
    dialog, context, manager = _build(data_dir)
    dialog._whisper_model_combo.setCurrentText("tiny")
    assert context.settings_service.current.whisper_model == "tiny"
    assert manager.invalidate_calls >= 1


# -- processing profile (Phase 6: CPU-first, low-resource readiness) --------------------


def test_default_profile_is_balanced_with_base_whisper_model(
    qapp: QApplication, data_dir: Path
) -> None:
    _dialog, context, _manager = _build(data_dir)
    assert context.settings_service.current.processing_profile is ProcessingProfile.BALANCED
    assert context.settings_service.current.whisper_model == "base"


def test_low_resource_profile_sets_tiny_model_and_disables_diarization(
    qapp: QApplication, data_dir: Path
) -> None:
    dialog, context, manager = _build(data_dir)
    context.settings_service.update(diarization_enabled=True)

    index = list(ProcessingProfile).index(ProcessingProfile.LOW_RESOURCE)
    dialog._profile_combo.setCurrentIndex(index)

    assert context.settings_service.current.processing_profile is ProcessingProfile.LOW_RESOURCE
    assert context.settings_service.current.whisper_model == "tiny"
    assert context.settings_service.current.diarization_enabled is False
    assert dialog._whisper_model_combo.currentText() == "tiny"
    assert dialog._diarization_checkbox.isChecked() is False
    assert manager.invalidate_calls >= 1


def test_low_resource_profile_sets_the_resource_governor_limit_to_one(
    qapp: QApplication, data_dir: Path
) -> None:
    dialog, context, _manager = _build(data_dir)
    index = list(ProcessingProfile).index(ProcessingProfile.LOW_RESOURCE)
    dialog._profile_combo.setCurrentIndex(index)
    assert context.resource_governor.limit == 1


def test_quality_profile_sets_small_model_and_allows_diarization(
    qapp: QApplication, data_dir: Path
) -> None:
    dialog, context, _manager = _build(data_dir)
    context.settings_service.update(diarization_enabled=True)

    index = list(ProcessingProfile).index(ProcessingProfile.QUALITY)
    dialog._profile_combo.setCurrentIndex(index)

    assert context.settings_service.current.whisper_model == "small"
    assert context.settings_service.current.diarization_enabled is True  # not force-disabled
    assert context.resource_governor.limit == 0  # unlimited


def test_switching_away_from_low_resource_does_not_auto_reenable_diarization(
    qapp: QApplication, data_dir: Path
) -> None:
    """A one-way override, not a remembered preference — see ProfileEffects docs."""
    dialog, context, _manager = _build(data_dir)
    context.settings_service.update(diarization_enabled=True)
    dialog._profile_combo.setCurrentIndex(
        list(ProcessingProfile).index(ProcessingProfile.LOW_RESOURCE)
    )
    assert context.settings_service.current.diarization_enabled is False

    dialog._profile_combo.setCurrentIndex(list(ProcessingProfile).index(ProcessingProfile.BALANCED))
    assert context.settings_service.current.diarization_enabled is False


def test_openai_model_combo_only_visible_for_openai_provider(
    qapp: QApplication, data_dir: Path
) -> None:
    dialog, _context, _manager = _build(data_dir)
    dialog._tabs.setCurrentIndex(1)  # "Transcription" — isVisible() needs the active tab
    assert not dialog._openai_model_combo.isVisible()  # default provider is local whisper

    dialog._provider_combo.setCurrentIndex(
        list(TranscriptionProviderId).index(TranscriptionProviderId.OPENAI)
    )
    assert dialog._openai_model_combo.isVisible()

    dialog._provider_combo.setCurrentIndex(
        list(TranscriptionProviderId).index(TranscriptionProviderId.OPENROUTER)
    )
    assert not dialog._openai_model_combo.isVisible()


def test_openai_model_defaults_to_whisper1(qapp: QApplication, data_dir: Path) -> None:
    dialog, _context, _manager = _build(data_dir)
    assert dialog._openai_model_combo.currentData() == "whisper-1"


def test_selecting_a_cheaper_openai_model_persists_and_invalidates(
    qapp: QApplication, data_dir: Path
) -> None:
    dialog, context, manager = _build(data_dir)
    index = next(
        i
        for i in range(dialog._openai_model_combo.count())
        if dialog._openai_model_combo.itemData(i) == "gpt-4o-mini-transcribe"
    )
    dialog._openai_model_combo.setCurrentIndex(index)

    assert context.settings_service.current.openai_model == "gpt-4o-mini-transcribe"
    assert manager.invalidate_calls >= 1


def test_save_api_key_persists_and_shows_masked_status(qapp: QApplication, data_dir: Path) -> None:
    dialog, context, manager = _build(data_dir)
    dialog._provider_combo.setCurrentIndex(
        list(TranscriptionProviderId).index(TranscriptionProviderId.OPENAI)
    )
    dialog._api_key_field.setText("sk-abcdef123456")
    dialog._on_save_api_key()

    assert context.credential_store.get(OPENAI_API_KEY_CREDENTIAL) == "sk-abcdef123456"
    assert dialog._api_key_field.text() == ""  # cleared after saving
    assert "saved" in dialog._credential_status_label.text()
    assert "3456" in dialog._credential_status_label.text()
    assert "sk-abcdef123456" not in dialog._credential_status_label.text()
    assert manager.invalidate_calls >= 1


def test_remove_api_key_clears_credential_and_status(qapp: QApplication, data_dir: Path) -> None:
    dialog, context, _manager = _build(data_dir)
    dialog._provider_combo.setCurrentIndex(
        list(TranscriptionProviderId).index(TranscriptionProviderId.OPENROUTER)
    )
    context.credential_store.set(OPENROUTER_API_KEY_CREDENTIAL, "or-abc123")
    dialog._refresh_credential_status()
    assert "saved" in dialog._credential_status_label.text()

    dialog._on_remove_api_key()
    assert context.credential_store.get(OPENROUTER_API_KEY_CREDENTIAL) is None
    assert "not configured" in dialog._credential_status_label.text()


def test_provider_combo_shows_availability_in_its_labels(
    qapp: QApplication, data_dir: Path
) -> None:
    dialog, context, _manager = _build(data_dir)
    openai_index = list(TranscriptionProviderId).index(TranscriptionProviderId.OPENAI)
    local_index = list(TranscriptionProviderId).index(TranscriptionProviderId.LOCAL_WHISPER)

    assert "API key needed" in dialog._provider_combo.itemText(openai_index)
    assert "API key needed" not in dialog._provider_combo.itemText(local_index)

    context.credential_store.set(OPENAI_API_KEY_CREDENTIAL, "sk-test")
    dialog._refresh_credential_status()
    assert "API key needed" not in dialog._provider_combo.itemText(openai_index)


def test_credential_status_shows_not_configured_by_default(
    qapp: QApplication, data_dir: Path
) -> None:
    dialog, _context, _manager = _build(data_dir)
    dialog._provider_combo.setCurrentIndex(
        list(TranscriptionProviderId).index(TranscriptionProviderId.OPENAI)
    )
    assert "not configured" in dialog._credential_status_label.text()
    assert "Local recording" in dialog._credential_status_label.text()


def test_test_connection_without_a_key_prompts_to_save_one(
    qapp: QApplication, data_dir: Path
) -> None:
    dialog, _context, _manager = _build(data_dir)
    dialog._provider_combo.setCurrentIndex(
        list(TranscriptionProviderId).index(TranscriptionProviderId.OPENAI)
    )
    dialog._on_test_connection()
    assert "Save an API key first" in dialog._test_result_label.text()


def test_test_connection_with_a_key_reports_the_result(
    qapp: QApplication, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        OpenAITranscriptionClient,
        "test_connection",
        lambda self, **kwargs: ConnectionTestResult(ok=True, message="Connected successfully."),
    )
    dialog, context, _manager = _build(data_dir)
    dialog._provider_combo.setCurrentIndex(
        list(TranscriptionProviderId).index(TranscriptionProviderId.OPENAI)
    )
    context.credential_store.set(OPENAI_API_KEY_CREDENTIAL, "sk-test")

    dialog._on_test_connection()
    assert dialog._test_result_label.text() == "Connected successfully."


def test_enabling_diarization_first_time_asks_for_download_confirmation(
    qapp: QApplication, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Ok)
    dialog, context, _manager = _build(data_dir)
    dialog._tabs.setCurrentIndex(2)  # "Speakers"

    dialog._diarization_checkbox.setChecked(True)

    assert context.settings_service.current.diarization_enabled is True
    assert context.settings_service.current.diarization_model_download_confirmed is True


def test_declining_the_download_confirmation_reverts_the_checkbox(
    qapp: QApplication, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    dialog, context, _manager = _build(data_dir)
    dialog._tabs.setCurrentIndex(2)  # "Speakers"

    dialog._diarization_checkbox.setChecked(True)

    assert dialog._diarization_checkbox.isChecked() is False
    assert context.settings_service.current.diarization_enabled is False
    assert context.settings_service.current.diarization_model_download_confirmed is False


def test_re_enabling_diarization_after_confirming_once_does_not_ask_again(
    qapp: QApplication, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts: list[int] = []

    def fake_question(*args: object, **kwargs: object) -> QMessageBox.StandardButton:
        prompts.append(1)
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "question", fake_question)
    dialog, context, _manager = _build(data_dir)
    dialog._tabs.setCurrentIndex(2)  # "Speakers"

    dialog._diarization_checkbox.setChecked(True)
    dialog._diarization_checkbox.setChecked(False)
    dialog._diarization_checkbox.setChecked(True)

    assert len(prompts) == 1  # only the very first enable prompted
    assert context.settings_service.current.diarization_enabled is True


def test_ollama_test_connection_without_a_model_prompts_to_enter_one(
    qapp: QApplication, data_dir: Path
) -> None:
    dialog, _context, _manager = _build(data_dir)
    dialog._tabs.setCurrentIndex(3)  # "AI Summary" — default provider is Ollama
    dialog._on_test_summary_connection()
    assert "Enter an Ollama model name first" in dialog._summary_test_result_label.text()


def test_ollama_test_connection_with_a_model_reports_the_result(
    qapp: QApplication, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        OllamaSummaryClient,
        "test_connection",
        lambda self, **kwargs: SummaryConnectionTestResult(
            ok=False, message="Connected to Ollama, but model 'llama3.1' is not installed locally."
        ),
    )
    dialog, _context, _manager = _build(data_dir)
    dialog._tabs.setCurrentIndex(3)  # "AI Summary"
    dialog._ollama_model_field.setText("llama3.1")
    dialog._on_ollama_model_changed()

    dialog._on_test_summary_connection()
    assert "not installed locally" in dialog._summary_test_result_label.text()


def test_about_tab_shows_app_name_and_version(qapp: QApplication, data_dir: Path) -> None:
    dialog, _context, _manager = _build(data_dir)
    about_tab = dialog._build_about_tab()
    labels = [w.text() for w in about_tab.findChildren(QLabel)]
    assert any("LocalMeetingRecorder" in label for label in labels)
    assert any(__version__ in label for label in labels)
