"""LocalMeetingRecorder: local-first meeting recording for macOS."""

from __future__ import annotations

__version__ = "0.1.0"
APP_NAME = "LocalMeetingRecorder"
"""Internal identity: Application Support folder name, Keychain service name. Stable
across renames so existing settings/recordings/credentials are never orphaned by a
cosmetic rebrand — see APP_DISPLAY_NAME for what the user actually sees."""
APP_DISPLAY_NAME = "My Recorder"
"""User-facing product name: .app bundle name, tray tooltip, dialog text, DMG volume."""
APP_BUNDLE_ID = "com.localmeetingrecorder.app"
HELPER_BUNDLE_ID = APP_BUNDLE_ID
"""The capture helper is code-signed and Info.plist-identified as the SAME bundle ID as the
main app (not a distinct one) so TCC permission grants (Screen Recording in particular) made
to the main app are recognized when the helper itself calls the protected API — see
scripts/build_helper.sh's codesign comment for what broke when it had its own identity."""

__all__ = ["APP_BUNDLE_ID", "APP_DISPLAY_NAME", "APP_NAME", "HELPER_BUNDLE_ID", "__version__"]
