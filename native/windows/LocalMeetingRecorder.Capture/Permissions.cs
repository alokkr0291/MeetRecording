namespace LocalMeetingRecorder.Capture;

/// <summary>
/// Windows has no OS-level consent gate for an unpackaged desktop app calling DXGI Desktop
/// Duplication or WASAPI loopback/capture, unlike macOS's TCC — confirmed by this project's
/// own prior gap analysis (PLAN.md's Phase 7 section) before any Windows code existed.
/// Windows 11 shows its own capture *indicator* while a Desktop Duplication session is
/// active, but that is notification, not a consent prompt this helper can query or that can
/// block capture. So both permissions are reported granted unconditionally: the app's own
/// ``consent_dialog.py`` ("has everyone been informed this is a recording?") already covers
/// the actual product requirement regardless of platform.
///
/// Known gap this DOES surface honestly: if the user has NO microphone or NO audio output
/// device at all, WASAPI device enumeration fails at `start` time with a real `error` event
/// (see Recorder.cs), not here — `check_permissions`/`request_permissions` only report
/// whether access is *allowed*, which on Windows is unconditionally true; device *presence*
/// is a separate, later check, exactly mirroring how macOS's own permission check and
/// "no displays available" check are two different things.
/// </summary>
internal static class Permissions
{
    public static Dictionary<string, object?> Snapshot() => new()
    {
        ["screen"] = "granted",
        ["screen_preflight"] = true,
        ["microphone"] = "granted",
    };

    public static Dictionary<string, object?> Request()
    {
        var snap = Snapshot();
        snap["screen_request_result"] = true;
        snap["microphone_request_result"] = true;
        return snap;
    }
}
