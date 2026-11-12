"""Credential storage: the in-memory fake (used everywhere else in the suite), secret
masking, the explicit "unavailable" placeholder, and ``MacOSKeychainStore``'s error
wrapping against a mocked ``keyring`` (never the real Keychain in automated tests).
"""

from __future__ import annotations

import keyring.errors
import pytest

from local_meeting_recorder.domain.errors import CredentialStoreError
from local_meeting_recorder.infrastructure.credentials.macos_keychain import MacOSKeychainStore
from local_meeting_recorder.infrastructure.credentials.masking import mask_secret
from local_meeting_recorder.infrastructure.credentials.memory import InMemoryCredentialStore
from local_meeting_recorder.infrastructure.credentials.unavailable import UnavailableCredentialStore


def test_memory_store_round_trips_a_secret() -> None:
    store = InMemoryCredentialStore()
    assert store.get("openai_api_key") is None
    store.set("openai_api_key", "sk-abc123")
    assert store.get("openai_api_key") == "sk-abc123"


def test_memory_store_replace_overwrites() -> None:
    store = InMemoryCredentialStore()
    store.set("openai_api_key", "sk-old")
    store.set("openai_api_key", "sk-new")
    assert store.get("openai_api_key") == "sk-new"


def test_memory_store_delete_removes_secret() -> None:
    store = InMemoryCredentialStore()
    store.set("openai_api_key", "sk-abc123")
    store.delete("openai_api_key")
    assert store.get("openai_api_key") is None


def test_memory_store_delete_missing_key_is_a_no_op() -> None:
    store = InMemoryCredentialStore()
    store.delete("never_set")  # must not raise


def test_memory_store_is_available() -> None:
    assert InMemoryCredentialStore().is_available()


def test_unavailable_store_raises_on_every_operation() -> None:
    store = UnavailableCredentialStore(reason="no secure store on this platform")
    assert not store.is_available()
    with pytest.raises(CredentialStoreError, match="no secure store"):
        store.get("x")
    with pytest.raises(CredentialStoreError, match="no secure store"):
        store.set("x", "y")
    with pytest.raises(CredentialStoreError, match="no secure store"):
        store.delete("x")


def test_mask_secret_shows_only_last_four_characters() -> None:
    assert mask_secret("sk-abcdef123456") == "•••••••••••3456"


def test_mask_secret_short_value_fully_masked() -> None:
    assert mask_secret("ab") == "••"


def test_mask_secret_empty_string() -> None:
    assert mask_secret("") == ""


def test_keychain_store_get_wraps_keyring_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(service: str, key: str) -> str:
        raise keyring.errors.KeyringError("locked")

    monkeypatch.setattr("keyring.get_password", boom)
    with pytest.raises(CredentialStoreError):
        MacOSKeychainStore().get("openai_api_key")


def test_keychain_store_set_wraps_keyring_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(service: str, key: str, value: str) -> None:
        raise keyring.errors.PasswordSetError("denied")

    monkeypatch.setattr("keyring.set_password", boom)
    with pytest.raises(CredentialStoreError):
        MacOSKeychainStore().set("openai_api_key", "sk-abc123")


def test_keychain_store_delete_missing_credential_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(service: str, key: str) -> None:
        raise keyring.errors.PasswordDeleteError("not found")

    monkeypatch.setattr("keyring.delete_password", boom)
    MacOSKeychainStore().delete("never_set")  # must not raise


def test_keychain_store_delete_other_errors_are_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(service: str, key: str) -> None:
        raise keyring.errors.KeyringError("locked")

    monkeypatch.setattr("keyring.delete_password", boom)
    with pytest.raises(CredentialStoreError):
        MacOSKeychainStore().delete("openai_api_key")


def test_keychain_store_uses_its_own_service_name(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "keyring.set_password", lambda service, key, value: calls.append((service, key, value))
    )
    MacOSKeychainStore(service_name="TestService").set("k", "v")
    assert calls == [("TestService", "k", "v")]
