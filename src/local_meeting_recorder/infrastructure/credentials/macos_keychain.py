"""macOS Keychain-backed ``CredentialStore``, via the ``keyring`` package.

``keyring``'s macOS backend talks to Security.framework directly (ctypes), not by
shelling out to the ``security`` CLI — shelling out would mean every secret briefly
appears on the subprocess's argv (visible to ``ps`` for that instant), which this app's
"never print complete API keys" rule rules out.

Every secret this app stores lives under one Keychain service name
(``LocalMeetingRecorder``), so it is all identifiable as one app's data in Keychain
Access rather than scattered ad-hoc items.
"""

from __future__ import annotations

import keyring
import keyring.errors

from local_meeting_recorder.domain.errors import CredentialStoreError
from local_meeting_recorder.infrastructure.logging_setup import get_logger

log = get_logger(__name__)

KEYCHAIN_SERVICE_NAME = "LocalMeetingRecorder"


class MacOSKeychainStore:
    """Implements ``CredentialStore`` (structurally — see ``protocol.py``)."""

    def __init__(self, *, service_name: str = KEYCHAIN_SERVICE_NAME) -> None:
        self._service_name = service_name

    def get(self, key: str) -> str | None:
        try:
            return keyring.get_password(self._service_name, key)
        except keyring.errors.KeyringError as exc:
            raise CredentialStoreError(f"could not read credential {key!r} from Keychain") from exc

    def set(self, key: str, value: str) -> None:
        try:
            keyring.set_password(self._service_name, key, value)
        except keyring.errors.KeyringError as exc:
            raise CredentialStoreError(f"could not save credential {key!r} to Keychain") from exc

    def delete(self, key: str) -> None:
        try:
            keyring.delete_password(self._service_name, key)
        except keyring.errors.PasswordDeleteError:
            pass  # nothing was stored — deleting a missing credential is not an error
        except keyring.errors.KeyringError as exc:
            raise CredentialStoreError(
                f"could not delete credential {key!r} from Keychain"
            ) from exc

    def is_available(self) -> bool:
        try:
            backend = keyring.get_keyring()
        except Exception:
            return False
        return "macOS" in type(backend).__module__
