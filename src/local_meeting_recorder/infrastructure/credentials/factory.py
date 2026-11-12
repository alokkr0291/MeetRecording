"""Platform dispatch for ``CredentialStore`` — the one place that knows which concrete
backend exists. Mirrors ``capture/factory.py``'s ``sys.platform`` dispatch pattern.
"""

from __future__ import annotations

from local_meeting_recorder.infrastructure.credentials.macos_keychain import MacOSKeychainStore
from local_meeting_recorder.infrastructure.credentials.protocol import CredentialStore
from local_meeting_recorder.infrastructure.credentials.unavailable import UnavailableCredentialStore
from local_meeting_recorder.storage.paths import platform_name

# macos_keychain.py only imports the cross-platform `keyring` package at module scope
# (safe anywhere); only *using* MacOSKeychainStore assumes darwin. ``platform_name()``
# (not a bare ``sys.platform``) keeps type checkers from pruning the non-darwin branch —
# same reasoning as ``storage/paths.py``'s own docstring for that function.


def default_credential_store() -> CredentialStore:
    platform = platform_name()
    if platform == "darwin":
        return MacOSKeychainStore()
    return UnavailableCredentialStore(
        reason=(
            f"No secure credential store is implemented for {platform!r} yet "
            "(Windows Credential Manager support is planned — see PLAN.md Phase 7). "
            "Cloud transcription providers that need an API key are unavailable on this "
            "platform; local faster-whisper transcription needs no credentials and is "
            "unaffected."
        )
    )
