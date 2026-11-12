"""``CredentialStore``: the shape every secret backend (macOS Keychain, a future Windows
Credential Manager backend, the in-memory test fake) implements. Application code never
imports a concrete backend directly — see ``factory.py``.
"""

from __future__ import annotations

from typing import Protocol


class CredentialStore(Protocol):
    def get(self, key: str) -> str | None:
        """Return the stored secret for ``key``, or ``None`` if nothing is stored."""
        ...

    def set(self, key: str, value: str) -> None:
        """Store (or replace) the secret for ``key``."""
        ...

    def delete(self, key: str) -> None:
        """Remove the secret for ``key``. No-op if nothing was stored."""
        ...

    def is_available(self) -> bool:
        """Whether this store can actually be used on this machine right now."""
        ...
