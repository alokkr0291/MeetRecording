"""Secure, provider-neutral credential storage: a key (e.g. ``"openai_api_key"``) in,
a secret out. Never plaintext JSON/TOML/env files, never logged. macOS uses the system
Keychain (``security`` CLI); a future Windows backend uses Credential Manager without
changing any caller — see ``factory.py``."""

from __future__ import annotations
