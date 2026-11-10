"""Recording lifecycle orchestration: state machine, session persistence, recovery.

Platform-independent — everything here talks to ``capture.protocols.CaptureBackend``
only, never to a specific backend implementation or UI framework.
"""

from __future__ import annotations
