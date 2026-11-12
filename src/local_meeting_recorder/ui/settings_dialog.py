"""The Settings window: General (recording storage location), Transcription (provider +
model + API key), About. Every change applies immediately — no separate Save button,
matching macOS's own System Settings panes — except an API key, which needs an explicit
Save (so a half-typed key is never persisted) and an explicit Remove (a deliberate,
distinct action).

Kept deliberately small per the product philosophy in the Phase 4.5 spec: no accounts,
no sign-in, no API key ever required for local recording or local faster-whisper.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from local_meeting_recorder import APP_NAME, __version__
from local_meeting_recorder.domain.errors import CredentialStoreError, StorageDirectoryError
from local_meeting_recorder.infrastructure.credentials.masking import mask_secret
from local_meeting_recorder.infrastructure.credentials.protocol import CredentialStore
from local_meeting_recorder.infrastructure.logging_setup import get_logger
from local_meeting_recorder.infrastructure.resource_governor import ResourceGovernor
from local_meeting_recorder.settings.models import (
    AppSettings,
    ProcessingProfile,
    ProviderAvailability,
    SummaryProviderId,
    TranscriptionProviderId,
)
from local_meeting_recorder.settings.profiles import effects_for, recommended_whisper_model
from local_meeting_recorder.settings.service import SettingsService
from local_meeting_recorder.speaker.factory import HUGGINGFACE_TOKEN_CREDENTIAL
from local_meeting_recorder.storage.paths import StoragePaths
from local_meeting_recorder.summarization.providers.factory import (
    CREDENTIAL_KEY_BY_SUMMARY_PROVIDER,
    summary_provider_availability,
)
from local_meeting_recorder.summarization.providers.http_base import HttpSummaryClientBase
from local_meeting_recorder.summarization.providers.ollama_client import OllamaSummaryClient
from local_meeting_recorder.summarization.providers.openai_client import (
    AVAILABLE_MODELS as OPENAI_SUMMARY_MODELS,
)
from local_meeting_recorder.summarization.providers.openai_client import OpenAISummaryClient
from local_meeting_recorder.summarization.providers.openrouter_client import (
    OpenRouterSummaryClient,
)
from local_meeting_recorder.transcription.manager import TranscriptionManager
from local_meeting_recorder.transcription.providers.factory import (
    CREDENTIAL_KEY_BY_PROVIDER,
    provider_availability,
)
from local_meeting_recorder.transcription.providers.http_base import HttpTranscriptionClientBase
from local_meeting_recorder.transcription.providers.openai_client import (
    AVAILABLE_MODELS as OPENAI_AVAILABLE_MODELS,
)
from local_meeting_recorder.transcription.providers.openai_client import OpenAITranscriptionClient
from local_meeting_recorder.transcription.providers.openrouter_client import (
    OpenRouterTranscriptionClient,
)

log = get_logger(__name__)

_WHISPER_MODEL_SIZES = ["tiny", "base", "small", "medium", "large-v3"]

_PROVIDER_LABELS: dict[TranscriptionProviderId, str] = {
    TranscriptionProviderId.LOCAL_WHISPER: "Local (faster-whisper)",
    TranscriptionProviderId.OPENAI: "OpenAI",
    TranscriptionProviderId.OPENROUTER: "OpenRouter",
}

_AVAILABILITY_LABELS: dict[ProviderAvailability, str] = {
    ProviderAvailability.READY: "Ready",
    ProviderAvailability.NEEDS_API_KEY: "API key needed",
    ProviderAvailability.COMING_SOON: "Coming soon",
}

_SUMMARY_PROVIDER_LABELS: dict[SummaryProviderId, str] = {
    SummaryProviderId.OLLAMA: "Ollama (local)",
    SummaryProviderId.OPENAI: "OpenAI",
    SummaryProviderId.OPENROUTER: "OpenRouter",
}

_PROFILE_LABELS: dict[ProcessingProfile, str] = {
    ProcessingProfile.LOW_RESOURCE: "Low Resource",
    ProcessingProfile.BALANCED: "Balanced (recommended)",
    ProcessingProfile.QUALITY: "Quality",
}

_PROFILE_DESCRIPTIONS: dict[ProcessingProfile, str] = {
    ProcessingProfile.LOW_RESOURCE: (
        "Smallest Whisper model, one AI job at a time, speaker identification off — "
        "for older or entry-level CPUs."
    ),
    ProcessingProfile.BALANCED: (
        "A good default for most computers. CPU only; no GPU is required or used."
    ),
    ProcessingProfile.QUALITY: (
        "Larger Whisper model and diarization/cloud AI stay available — still CPU "
        "only, but local AI processing will be slower."
    ),
}


@dataclass
class SettingsDialogContext:
    """Everything the Settings window needs. Built once in ``app.py``'s composition
    root and handed to every ``show_settings_dialog`` call."""

    settings_service: SettingsService
    credential_store: CredentialStore
    storage_paths: StoragePaths
    transcription_manager: TranscriptionManager
    resource_governor: ResourceGovernor


#: Tab labels, as literally shown on the ``QTabWidget`` — used to jump straight to the
#: tab relevant to whatever failure sent the user here, instead of always opening on
#: whichever tab happened to be active last time.
TAB_TRANSCRIPTION = "Transcription"
TAB_SPEAKERS = "Speakers"
TAB_SUMMARY = "AI Summary"


def show_settings_dialog(
    context: SettingsDialogContext, parent: QWidget | None = None, *, initial_tab: str | None = None
) -> None:
    dialog = SettingsDialog(context, parent)
    if initial_tab is not None:
        dialog.select_tab(initial_tab)
    dialog.exec()


class SettingsDialog(QDialog):
    def __init__(self, context: SettingsDialogContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = context
        self.setWindowTitle("Settings")
        self.resize(460, 380)

        layout = QVBoxLayout(self)
        self._tabs = QTabWidget(self)
        tabs = self._tabs
        layout.addWidget(tabs)
        tabs.addTab(self._build_general_tab(), "General")
        tabs.addTab(self._build_transcription_tab(), "Transcription")
        tabs.addTab(self._build_speakers_tab(), "Speakers")
        tabs.addTab(self._build_summary_tab(), "AI Summary")
        tabs.addTab(self._build_about_tab(), "About")

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        done_button = QPushButton("Done")
        done_button.setDefault(True)
        done_button.clicked.connect(self.accept)
        buttons.addWidget(done_button)
        layout.addLayout(buttons)

    def select_tab(self, label: str) -> None:
        for index in range(self._tabs.count()):
            if self._tabs.tabText(index) == label:
                self._tabs.setCurrentIndex(index)
                return

    # -- General --------------------------------------------------------------------------

    def _build_general_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        settings = self._ctx.settings_service.current

        current = self._ctx.storage_paths.recordings_dir
        self._dir_field = QLineEdit(str(current))
        self._dir_field.setReadOnly(True)
        browse_button = QPushButton("Browse…")
        browse_button.clicked.connect(self._on_browse_recordings_dir)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self._dir_field, stretch=1)
        dir_row.addWidget(browse_button)
        form.addRow("Recording storage location:", _wrap(dir_row))

        self._dir_status_label = QLabel("")
        self._dir_status_label.setWordWrap(True)
        form.addRow("", self._dir_status_label)

        self._profile_combo = QComboBox()
        for profile in ProcessingProfile:
            self._profile_combo.addItem(_PROFILE_LABELS[profile], userData=profile.value)
        self._profile_combo.setCurrentIndex(
            list(ProcessingProfile).index(settings.processing_profile)
        )
        self._profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        form.addRow("Performance:", self._profile_combo)

        self._profile_description_label = QLabel("")
        self._profile_description_label.setWordWrap(True)
        form.addRow("", self._profile_description_label)
        self._refresh_profile_description()
        return tab

    def _on_profile_changed(self) -> None:
        profile = ProcessingProfile(self._profile_combo.currentData())
        try:
            self._ctx.settings_service.update(
                processing_profile=profile,
                whisper_model=recommended_whisper_model(profile),
                diarization_enabled=(
                    self._ctx.settings_service.current.diarization_enabled
                    and effects_for(profile).diarization_allowed
                ),
            )
        except Exception:
            log.exception("failed to persist processing_profile setting")
        self._ctx.resource_governor.set_limit(effects_for(profile).max_concurrent_ai_jobs)
        self._ctx.transcription_manager.invalidate_cached_engine()
        self._refresh_profile_description()
        self._refresh_cross_tab_widgets_for_profile()

    def _refresh_profile_description(self) -> None:
        profile = self._ctx.settings_service.current.processing_profile
        self._profile_description_label.setText(_PROFILE_DESCRIPTIONS[profile])

    def _refresh_cross_tab_widgets_for_profile(self) -> None:
        """The Whisper-model and diarization widgets on other tabs need to reflect the
        side effects ``_on_profile_changed`` just applied, if those tabs have already
        been built (they always have — every tab is built once in ``__init__``).

        Signals are blocked for this sync: ``_on_profile_changed`` already computed and
        persisted the new values itself, so re-emitting ``toggled``/``currentTextChanged``
        here would redundantly re-run those handlers — and for the diarization checkbox,
        would wrongly pop the "download this model?" confirmation dialog for a state
        change the user didn't just make by clicking the checkbox themselves.
        """
        settings = self._ctx.settings_service.current
        if settings.whisper_model in _WHISPER_MODEL_SIZES:
            self._whisper_model_combo.blockSignals(True)
            self._whisper_model_combo.setCurrentText(settings.whisper_model)
            self._whisper_model_combo.blockSignals(False)
        self._diarization_checkbox.blockSignals(True)
        self._diarization_checkbox.setChecked(settings.diarization_enabled)
        self._diarization_checkbox.blockSignals(False)

    def _on_browse_recordings_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose Recordings Folder", str(self._ctx.storage_paths.recordings_dir)
        )
        if not chosen:
            return
        new_dir = Path(chosen)
        try:
            self._ctx.storage_paths.set_recordings_dir_override(new_dir)
        except StorageDirectoryError as exc:
            self._dir_status_label.setText(f"Could not use that folder: {exc}")
            return
        self._dir_field.setText(str(new_dir))
        self._dir_status_label.setText("Saved. Existing recordings were not moved.")
        try:
            self._ctx.settings_service.update(recordings_dir=new_dir)
        except Exception:
            log.exception("failed to persist recordings_dir setting")

    # -- Transcription --------------------------------------------------------------------

    def _build_transcription_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        settings = self._ctx.settings_service.current

        self._provider_combo = QComboBox()
        for provider_id in TranscriptionProviderId:
            self._provider_combo.addItem(_PROVIDER_LABELS[provider_id], userData=provider_id.value)
        self._provider_combo.setCurrentIndex(
            list(TranscriptionProviderId).index(settings.transcription_provider)
        )
        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        form.addRow("Provider:", self._provider_combo)

        self._add_whisper_model_row(form, settings)
        self._add_openai_model_row(form, settings)
        self._add_api_key_row(form)
        self._add_test_connection_row(form)

        self._refresh_provider_visibility()
        self._refresh_credential_status()
        return tab

    def _add_whisper_model_row(self, form: QFormLayout, settings: AppSettings) -> None:
        self._whisper_model_combo = QComboBox()
        self._whisper_model_combo.addItems(_WHISPER_MODEL_SIZES)
        if settings.whisper_model in _WHISPER_MODEL_SIZES:
            self._whisper_model_combo.setCurrentText(settings.whisper_model)
        self._whisper_model_combo.currentTextChanged.connect(self._on_whisper_model_changed)
        self._whisper_model_row_label = QLabel("Local Whisper model:")
        form.addRow(self._whisper_model_row_label, self._whisper_model_combo)

        self._whisper_model_note_label = QLabel(
            'Runs on CPU — no GPU is used or required. "tiny" is fastest and lightest '
            'for older computers; "base" is recommended for most; "medium"/"large-v3" '
            "are advanced options that are noticeably slower on a CPU."
        )
        self._whisper_model_note_label.setWordWrap(True)
        form.addRow("", self._whisper_model_note_label)

    def _add_openai_model_row(self, form: QFormLayout, settings: AppSettings) -> None:
        self._openai_model_combo = QComboBox()
        for model_id, label in OPENAI_AVAILABLE_MODELS:
            self._openai_model_combo.addItem(label, userData=model_id)
        openai_model_ids = [model_id for model_id, _label in OPENAI_AVAILABLE_MODELS]
        if settings.openai_model in openai_model_ids:
            self._openai_model_combo.setCurrentIndex(openai_model_ids.index(settings.openai_model))
        self._openai_model_combo.currentIndexChanged.connect(self._on_openai_model_changed)
        self._openai_model_row_label = QLabel("OpenAI model:")
        form.addRow(self._openai_model_row_label, self._openai_model_combo)

        self._openai_model_note_label = QLabel(
            "Prices are per-minute estimates, not guaranteed — check OpenAI's pricing page. "
            "OpenAI has announced these transcription models retire on 2027-02-26."
        )
        self._openai_model_note_label.setWordWrap(True)
        form.addRow("", self._openai_model_note_label)

    def _add_api_key_row(self, form: QFormLayout) -> None:
        self._api_key_field = QLineEdit()
        self._api_key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_field.setPlaceholderText("Enter a new API key")
        save_key_button = QPushButton("Save Key")
        save_key_button.clicked.connect(self._on_save_api_key)
        remove_key_button = QPushButton("Remove")
        remove_key_button.clicked.connect(self._on_remove_api_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self._api_key_field, stretch=1)
        key_row.addWidget(save_key_button)
        key_row.addWidget(remove_key_button)
        self._api_key_row_label = QLabel("API key:")
        form.addRow(self._api_key_row_label, _wrap(key_row))

        self._credential_status_label = QLabel("")
        form.addRow("", self._credential_status_label)

    def _add_test_connection_row(self, form: QFormLayout) -> None:
        test_button = QPushButton("Test Connection")
        test_button.clicked.connect(self._on_test_connection)
        self._test_button = test_button
        self._test_result_label = QLabel("")
        self._test_result_label.setWordWrap(True)
        test_row = QHBoxLayout()
        test_row.addWidget(test_button)
        test_row.addStretch(1)
        form.addRow("", _wrap(test_row))
        form.addRow("", self._test_result_label)

    def _selected_provider(self) -> TranscriptionProviderId:
        return TranscriptionProviderId(self._provider_combo.currentData())

    def _on_provider_changed(self) -> None:
        provider = self._selected_provider()
        try:
            self._ctx.settings_service.update(transcription_provider=provider)
        except Exception:
            log.exception("failed to persist transcription_provider setting")
        self._ctx.transcription_manager.invalidate_cached_engine()
        self._refresh_provider_visibility()
        self._refresh_credential_status()
        self._test_result_label.setText("")

    def _on_whisper_model_changed(self, model: str) -> None:
        try:
            self._ctx.settings_service.update(whisper_model=model)
        except Exception:
            log.exception("failed to persist whisper_model setting")
        self._ctx.transcription_manager.invalidate_cached_engine()

    def _on_openai_model_changed(self) -> None:
        model = self._openai_model_combo.currentData()
        if not model:
            return
        try:
            self._ctx.settings_service.update(openai_model=model)
        except Exception:
            log.exception("failed to persist openai_model setting")
        self._ctx.transcription_manager.invalidate_cached_engine()

    def _refresh_provider_visibility(self) -> None:
        provider = self._selected_provider()
        is_local = provider is TranscriptionProviderId.LOCAL_WHISPER
        is_openai = provider is TranscriptionProviderId.OPENAI
        self._whisper_model_row_label.setVisible(is_local)
        self._whisper_model_combo.setVisible(is_local)
        self._whisper_model_note_label.setVisible(is_local)
        self._openai_model_row_label.setVisible(is_openai)
        self._openai_model_combo.setVisible(is_openai)
        self._openai_model_note_label.setVisible(is_openai)
        for widget in (self._api_key_row_label, self._api_key_field, self._test_button):
            widget.setVisible(not is_local)

    def _credential_key_for_selected_provider(self) -> str | None:
        return CREDENTIAL_KEY_BY_PROVIDER.get(self._selected_provider())

    def _on_save_api_key(self) -> None:
        key = self._credential_key_for_selected_provider()
        value = self._api_key_field.text().strip()
        if key is None or not value:
            return
        try:
            self._ctx.credential_store.set(key, value)
        except CredentialStoreError as exc:
            self._credential_status_label.setText(f"Could not save key: {exc}")
            return
        self._api_key_field.clear()
        self._ctx.transcription_manager.invalidate_cached_engine()
        self._refresh_credential_status()

    def _on_remove_api_key(self) -> None:
        key = self._credential_key_for_selected_provider()
        if key is None:
            return
        try:
            self._ctx.credential_store.delete(key)
        except CredentialStoreError as exc:
            self._credential_status_label.setText(f"Could not remove key: {exc}")
            return
        self._ctx.transcription_manager.invalidate_cached_engine()
        self._refresh_credential_status()

    def _refresh_credential_status(self) -> None:
        self._refresh_provider_combo_labels()
        key = self._credential_key_for_selected_provider()
        if key is None:
            self._credential_status_label.setText("")
            return
        try:
            value = self._ctx.credential_store.get(key)
        except CredentialStoreError as exc:
            self._credential_status_label.setText(f"Credential store unavailable: {exc}")
            return
        if value:
            self._credential_status_label.setText(f"API key saved ({mask_secret(value)}).")
        else:
            self._credential_status_label.setText(
                "API key not configured. Local recording and local transcription are unaffected."
            )

    def _refresh_provider_combo_labels(self) -> None:
        """Show each provider's current availability right in the dropdown (e.g.
        "OpenAI (API key needed)") rather than only revealing it after selecting one."""
        for index, provider_id in enumerate(TranscriptionProviderId):
            availability = provider_availability(provider_id, self._ctx.credential_store)
            label = _PROVIDER_LABELS[provider_id]
            if availability is not ProviderAvailability.READY:
                label = f"{label} ({_AVAILABILITY_LABELS[availability]})"
            self._provider_combo.setItemText(index, label)

    def _on_test_connection(self) -> None:
        provider = self._selected_provider()
        key = self._credential_key_for_selected_provider()
        api_key = key and self._ctx.credential_store.get(key)
        if not api_key:
            self._test_result_label.setText("Save an API key first.")
            return
        settings = self._ctx.settings_service.current
        client: HttpTranscriptionClientBase
        if provider is TranscriptionProviderId.OPENAI:
            client = OpenAITranscriptionClient(api_key=api_key, model=settings.openai_model)
        elif provider is TranscriptionProviderId.OPENROUTER:
            client = OpenRouterTranscriptionClient(api_key=api_key, model=settings.openrouter_model)
        else:
            return
        result = client.test_connection()
        self._test_result_label.setText(result.message)

    # -- Speakers (diarization) ------------------------------------------------------------

    def _build_speakers_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        settings = self._ctx.settings_service.current

        self._diarization_checkbox = QCheckBox(
            "Identify speakers in system (remote participant) audio after transcription"
        )
        self._diarization_checkbox.setChecked(settings.diarization_enabled)
        self._diarization_checkbox.toggled.connect(self._on_diarization_toggled)
        layout.addWidget(self._diarization_checkbox)

        note = QLabel(
            "Your own microphone is always labeled “You” and is never sent "
            "anywhere for speaker identification. Remote-participant identification runs "
            "locally and requires a one-time Hugging Face access token to download the "
            "model (free account, no payment required)."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        form = QFormLayout()
        self._hf_token_field = QLineEdit()
        self._hf_token_field.setEchoMode(QLineEdit.EchoMode.Password)
        self._hf_token_field.setPlaceholderText("Enter a Hugging Face access token")
        save_token_button = QPushButton("Save Token")
        save_token_button.clicked.connect(self._on_save_hf_token)
        remove_token_button = QPushButton("Remove")
        remove_token_button.clicked.connect(self._on_remove_hf_token)
        token_row = QHBoxLayout()
        token_row.addWidget(self._hf_token_field, stretch=1)
        token_row.addWidget(save_token_button)
        token_row.addWidget(remove_token_button)
        form.addRow("Hugging Face token:", _wrap(token_row))
        layout.addLayout(form)

        self._hf_token_status_label = QLabel("")
        self._hf_token_status_label.setWordWrap(True)
        layout.addWidget(self._hf_token_status_label)
        layout.addStretch(1)

        self._refresh_hf_token_status()
        return tab

    def _on_diarization_toggled(self, checked: bool) -> None:
        if checked and not self._ctx.settings_service.current.diarization_model_download_confirmed:
            if not self._confirm_diarization_download():
                self._diarization_checkbox.setChecked(False)  # reverts; re-enters this
                return  # method with checked=False, which falls through to persist below
            try:
                self._ctx.settings_service.update(diarization_model_download_confirmed=True)
            except Exception:
                log.exception("failed to persist diarization_model_download_confirmed setting")
        try:
            self._ctx.settings_service.update(diarization_enabled=checked)
        except Exception:
            log.exception("failed to persist diarization_enabled setting")

    def _confirm_diarization_download(self) -> bool:
        choice = QMessageBox.question(
            self,
            "Download Speaker Identification Model?",
            "Speaker identification downloads the 'pyannote/speaker-diarization-3.1' "
            "model the first time it runs — several hundred megabytes, fetched once "
            "from Hugging Face and cached locally after that. Continue?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return choice == QMessageBox.StandardButton.Ok

    def _on_save_hf_token(self) -> None:
        value = self._hf_token_field.text().strip()
        if not value:
            return
        try:
            self._ctx.credential_store.set(HUGGINGFACE_TOKEN_CREDENTIAL, value)
        except CredentialStoreError as exc:
            self._hf_token_status_label.setText(f"Could not save token: {exc}")
            return
        self._hf_token_field.clear()
        self._refresh_hf_token_status()

    def _on_remove_hf_token(self) -> None:
        try:
            self._ctx.credential_store.delete(HUGGINGFACE_TOKEN_CREDENTIAL)
        except CredentialStoreError as exc:
            self._hf_token_status_label.setText(f"Could not remove token: {exc}")
            return
        self._refresh_hf_token_status()

    def _refresh_hf_token_status(self) -> None:
        try:
            value = self._ctx.credential_store.get(HUGGINGFACE_TOKEN_CREDENTIAL)
        except CredentialStoreError as exc:
            self._hf_token_status_label.setText(f"Credential store unavailable: {exc}")
            return
        if value:
            self._hf_token_status_label.setText(f"Token saved ({mask_secret(value)}).")
        else:
            self._hf_token_status_label.setText(
                "No token configured. Speaker identification will fail until one is saved."
            )

    # -- AI Summary -------------------------------------------------------------------------

    def _build_summary_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        settings = self._ctx.settings_service.current

        self._summary_enabled_checkbox = QCheckBox(
            "Generate a structured summary after transcription"
        )
        self._summary_enabled_checkbox.setChecked(settings.summary_enabled)
        self._summary_enabled_checkbox.toggled.connect(self._on_summary_enabled_toggled)
        layout.addWidget(self._summary_enabled_checkbox)

        form = QFormLayout()
        self._summary_provider_combo = QComboBox()
        for provider_id in SummaryProviderId:
            self._summary_provider_combo.addItem(
                _SUMMARY_PROVIDER_LABELS[provider_id], userData=provider_id.value
            )
        self._summary_provider_combo.setCurrentIndex(
            list(SummaryProviderId).index(settings.summary_provider)
        )
        self._summary_provider_combo.currentIndexChanged.connect(self._on_summary_provider_changed)
        form.addRow("Provider:", self._summary_provider_combo)

        self._add_ollama_rows(form, settings)
        self._add_openai_summary_model_row(form, settings)
        self._add_openrouter_summary_model_row(form, settings)
        self._add_summary_api_key_row(form)
        self._add_summary_test_connection_row(form)
        layout.addLayout(form)
        layout.addStretch(1)

        self._refresh_summary_provider_visibility()
        self._refresh_summary_credential_status()
        return tab

    def _add_ollama_rows(self, form: QFormLayout, settings: AppSettings) -> None:
        self._ollama_base_url_field = QLineEdit(settings.ollama_base_url)
        self._ollama_base_url_field.editingFinished.connect(self._on_ollama_base_url_changed)
        self._ollama_base_url_row_label = QLabel("Ollama server URL:")
        form.addRow(self._ollama_base_url_row_label, self._ollama_base_url_field)

        self._ollama_model_field = QLineEdit(settings.ollama_summary_model)
        self._ollama_model_field.setPlaceholderText("e.g. llama3.1")
        self._ollama_model_field.editingFinished.connect(self._on_ollama_model_changed)
        self._ollama_model_row_label = QLabel("Ollama model:")
        form.addRow(self._ollama_model_row_label, self._ollama_model_field)

    def _add_openai_summary_model_row(self, form: QFormLayout, settings: AppSettings) -> None:
        self._openai_summary_model_combo = QComboBox()
        for model_id, label in OPENAI_SUMMARY_MODELS:
            self._openai_summary_model_combo.addItem(label, userData=model_id)
        model_ids = [model_id for model_id, _label in OPENAI_SUMMARY_MODELS]
        if settings.openai_summary_model in model_ids:
            self._openai_summary_model_combo.setCurrentIndex(
                model_ids.index(settings.openai_summary_model)
            )
        self._openai_summary_model_combo.currentIndexChanged.connect(
            self._on_openai_summary_model_changed
        )
        self._openai_summary_model_row_label = QLabel("OpenAI model:")
        form.addRow(self._openai_summary_model_row_label, self._openai_summary_model_combo)

    def _add_openrouter_summary_model_row(self, form: QFormLayout, settings: AppSettings) -> None:
        self._openrouter_summary_model_field = QLineEdit(settings.openrouter_summary_model)
        self._openrouter_summary_model_field.setPlaceholderText("e.g. anthropic/claude-3.5-sonnet")
        self._openrouter_summary_model_field.editingFinished.connect(
            self._on_openrouter_summary_model_changed
        )
        self._openrouter_summary_model_row_label = QLabel("OpenRouter model:")
        form.addRow(self._openrouter_summary_model_row_label, self._openrouter_summary_model_field)

    def _add_summary_api_key_row(self, form: QFormLayout) -> None:
        self._summary_api_key_field = QLineEdit()
        self._summary_api_key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self._summary_api_key_field.setPlaceholderText("Enter a new API key")
        save_key_button = QPushButton("Save Key")
        save_key_button.clicked.connect(self._on_save_summary_api_key)
        remove_key_button = QPushButton("Remove")
        remove_key_button.clicked.connect(self._on_remove_summary_api_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self._summary_api_key_field, stretch=1)
        key_row.addWidget(save_key_button)
        key_row.addWidget(remove_key_button)
        self._summary_api_key_row_label = QLabel("API key:")
        form.addRow(self._summary_api_key_row_label, _wrap(key_row))

        self._summary_credential_status_label = QLabel("")
        self._summary_credential_status_label.setWordWrap(True)
        form.addRow("", self._summary_credential_status_label)

    def _add_summary_test_connection_row(self, form: QFormLayout) -> None:
        test_button = QPushButton("Test Connection")
        test_button.clicked.connect(self._on_test_summary_connection)
        self._summary_test_button = test_button
        self._summary_test_result_label = QLabel("")
        self._summary_test_result_label.setWordWrap(True)
        test_row = QHBoxLayout()
        test_row.addWidget(test_button)
        test_row.addStretch(1)
        form.addRow("", _wrap(test_row))
        form.addRow("", self._summary_test_result_label)

    def _selected_summary_provider(self) -> SummaryProviderId:
        return SummaryProviderId(self._summary_provider_combo.currentData())

    def _on_summary_enabled_toggled(self, checked: bool) -> None:
        try:
            self._ctx.settings_service.update(summary_enabled=checked)
        except Exception:
            log.exception("failed to persist summary_enabled setting")

    def _on_summary_provider_changed(self) -> None:
        provider = self._selected_summary_provider()
        try:
            self._ctx.settings_service.update(summary_provider=provider)
        except Exception:
            log.exception("failed to persist summary_provider setting")
        self._refresh_summary_provider_visibility()
        self._refresh_summary_credential_status()
        self._summary_test_result_label.setText("")

    def _on_ollama_base_url_changed(self) -> None:
        try:
            self._ctx.settings_service.update(ollama_base_url=self._ollama_base_url_field.text())
        except Exception:
            log.exception("failed to persist ollama_base_url setting")

    def _on_ollama_model_changed(self) -> None:
        try:
            self._ctx.settings_service.update(
                ollama_summary_model=self._ollama_model_field.text().strip()
            )
        except Exception:
            log.exception("failed to persist ollama_summary_model setting")

    def _on_openai_summary_model_changed(self) -> None:
        model = self._openai_summary_model_combo.currentData()
        if not model:
            return
        try:
            self._ctx.settings_service.update(openai_summary_model=model)
        except Exception:
            log.exception("failed to persist openai_summary_model setting")

    def _on_openrouter_summary_model_changed(self) -> None:
        try:
            self._ctx.settings_service.update(
                openrouter_summary_model=self._openrouter_summary_model_field.text().strip()
            )
        except Exception:
            log.exception("failed to persist openrouter_summary_model setting")

    def _refresh_summary_provider_visibility(self) -> None:
        provider = self._selected_summary_provider()
        is_ollama = provider is SummaryProviderId.OLLAMA
        is_openai = provider is SummaryProviderId.OPENAI
        is_openrouter = provider is SummaryProviderId.OPENROUTER
        for widget in (
            self._ollama_base_url_row_label,
            self._ollama_base_url_field,
            self._ollama_model_row_label,
            self._ollama_model_field,
        ):
            widget.setVisible(is_ollama)
        self._openai_summary_model_row_label.setVisible(is_openai)
        self._openai_summary_model_combo.setVisible(is_openai)
        self._openrouter_summary_model_row_label.setVisible(is_openrouter)
        self._openrouter_summary_model_field.setVisible(is_openrouter)
        for widget in (self._summary_api_key_row_label, self._summary_api_key_field):
            widget.setVisible(not is_ollama)
        self._summary_test_button.setVisible(True)

    def _summary_credential_key_for_selected_provider(self) -> str | None:
        return CREDENTIAL_KEY_BY_SUMMARY_PROVIDER.get(self._selected_summary_provider())

    def _on_save_summary_api_key(self) -> None:
        key = self._summary_credential_key_for_selected_provider()
        value = self._summary_api_key_field.text().strip()
        if key is None or not value:
            return
        try:
            self._ctx.credential_store.set(key, value)
        except CredentialStoreError as exc:
            self._summary_credential_status_label.setText(f"Could not save key: {exc}")
            return
        self._summary_api_key_field.clear()
        self._refresh_summary_credential_status()

    def _on_remove_summary_api_key(self) -> None:
        key = self._summary_credential_key_for_selected_provider()
        if key is None:
            return
        try:
            self._ctx.credential_store.delete(key)
        except CredentialStoreError as exc:
            self._summary_credential_status_label.setText(f"Could not remove key: {exc}")
            return
        self._refresh_summary_credential_status()

    def _refresh_summary_credential_status(self) -> None:
        self._refresh_summary_provider_combo_labels()
        key = self._summary_credential_key_for_selected_provider()
        if key is None:
            self._summary_credential_status_label.setText(
                "Ollama runs locally — no API key needed."
            )
            return
        try:
            value = self._ctx.credential_store.get(key)
        except CredentialStoreError as exc:
            self._summary_credential_status_label.setText(f"Credential store unavailable: {exc}")
            return
        if value:
            self._summary_credential_status_label.setText(f"API key saved ({mask_secret(value)}).")
        else:
            self._summary_credential_status_label.setText("API key not configured yet.")

    def _refresh_summary_provider_combo_labels(self) -> None:
        for index, provider_id in enumerate(SummaryProviderId):
            availability = summary_provider_availability(provider_id, self._ctx.credential_store)
            label = _SUMMARY_PROVIDER_LABELS[provider_id]
            if availability is not ProviderAvailability.READY:
                label = f"{label} ({_AVAILABILITY_LABELS[availability]})"
            self._summary_provider_combo.setItemText(index, label)

    def _on_test_summary_connection(self) -> None:
        provider = self._selected_summary_provider()
        settings = self._ctx.settings_service.current
        if provider is SummaryProviderId.OLLAMA:
            if not settings.ollama_summary_model:
                self._summary_test_result_label.setText("Enter an Ollama model name first.")
                return
            ollama_client = OllamaSummaryClient(
                base_url=settings.ollama_base_url, model=settings.ollama_summary_model
            )
            self._summary_test_result_label.setText(ollama_client.test_connection().message)
            return
        key = self._summary_credential_key_for_selected_provider()
        api_key = key and self._ctx.credential_store.get(key)
        if not api_key:
            self._summary_test_result_label.setText("Save an API key first.")
            return
        cloud_client: HttpSummaryClientBase
        if provider is SummaryProviderId.OPENAI:
            cloud_client = OpenAISummaryClient(api_key=api_key, model=settings.openai_summary_model)
        else:
            cloud_client = OpenRouterSummaryClient(
                api_key=api_key, model=settings.openrouter_summary_model or "x"
            )
        self._summary_test_result_label.setText(cloud_client.test_connection().message)

    # -- About ------------------------------------------------------------------------------

    def _build_about_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(QLabel(f"<b>{APP_NAME}</b>"))
        layout.addWidget(QLabel(f"Version {__version__}"))
        layout.addStretch(1)
        return tab


def _wrap(inner_layout: QHBoxLayout) -> QWidget:
    container = QWidget()
    container.setLayout(inner_layout)
    return container
