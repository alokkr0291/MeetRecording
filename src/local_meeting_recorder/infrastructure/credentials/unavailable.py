"""The explicit non-macOS placeholder: raises a clear error on every operation rather
than silently falling back to plaintext storage. A future Windows backend
(``infrastructure/credentials/windows_credential_manager.py``, not yet built — see
PLAN.md Phase 7) replaces this for that platform without any caller needing to change;
until then, running Settings' cloud-provider credential UI on an unsupported platform
tells the user exactly why, instead of pretending it worked.
"""

from __future__ import annotations

from local_meeting_recorder.domain.errors import CredentialStoreError


class UnavailableCredentialStore:
    """Implements ``CredentialStore`` (structurally — see ``protocol.py``)."""

    def __init__(self, *, reason: str) -> None:
        self._reason = reason

    def get(self, key: str) -> str | None:
        raise CredentialStoreError(self._reason)

    def set(self, key: str, value: str) -> None:
        raise CredentialStoreError(self._reason)

    def delete(self, key: str) -> None:
        raise CredentialStoreError(self._reason)

    def is_available(self) -> bool:
        return False
