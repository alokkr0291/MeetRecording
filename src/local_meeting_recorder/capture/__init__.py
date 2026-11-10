"""Platform-independent capture interfaces and backends.

Everything in this package and below is safe to import on any platform. macOS-specific
code lives in ``capture.macos``; it must never be imported from outside this package
except behind a platform check (see ``capture.macos.backend``).
"""

from __future__ import annotations
