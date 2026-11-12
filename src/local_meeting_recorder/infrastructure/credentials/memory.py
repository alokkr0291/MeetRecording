"""In-process, non-persistent ``CredentialStore`` fake — test-only, never wired as a
production fallback (see ``factory.py``: a platform with no real secure store gets
``UnavailableCredentialStore``, not this)."""

from __future__ import annotations


class InMemoryCredentialStore:
    """Implements ``CredentialStore`` (structurally — see ``protocol.py``)."""

    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._secrets.get(key)

    def set(self, key: str, value: str) -> None:
        self._secrets[key] = value

    def delete(self, key: str) -> None:
        self._secrets.pop(key, None)

    def is_available(self) -> bool:
        return True
