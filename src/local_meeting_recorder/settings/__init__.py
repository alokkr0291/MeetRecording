"""Typed, versioned, UI-framework-independent user preferences (Phase 4.5).

Distinct from ``config.py``'s ``Settings`` (env/``.env``/``config.toml``-driven,
read once at startup — meant for development/deployment overrides) and from
``infrastructure/credentials/`` (secrets never live here): this is the
Settings-window-managed preferences file, read *and written* by the app itself at
runtime. See ``settings/service.py``.
"""

from __future__ import annotations
