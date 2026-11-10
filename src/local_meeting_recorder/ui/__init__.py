"""PySide6 presentation layer. No recording logic lives here — see CLAUDE.md's layer
rules: this package renders widgets and forwards user actions; ``application/`` owns the
state machine and session lifecycle. Keeping that boundary sharp is also what would let a
future Windows build have its own native tray/notification layer without touching
anything below ``ui/``.
"""

from __future__ import annotations
