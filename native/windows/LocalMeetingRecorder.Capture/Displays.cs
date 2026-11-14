using System.Windows.Forms;

namespace LocalMeetingRecorder.Capture;

/// <summary>One enumerated monitor. ``Id`` is this process's own 0-based index into
/// ``Screen.AllScreens`` — stable for the lifetime of one helper invocation (which only
/// ever lives for one logical operation, same as the macOS helper), not a persistent
/// Windows display identifier. ``DeviceName`` (e.g. ``\\.\DISPLAY1``) is kept alongside so
/// Recorder.cs can correlate this entry with the matching DXGI output by name — GDI/WinForms
/// and DXGI enumerate monitors independently and are not guaranteed to agree on order.</summary>
internal sealed record DisplayEntry(
    int Id,
    string DeviceName,
    string Name,
    int Width,
    int Height,
    bool IsMain);

internal static class Displays
{
    /// <summary>Width/height reported here are DEVICE PIXELS, not DPI-scaled "points" —
    /// unverified against a real multi-monitor, mixed-DPI Windows setup (no Windows
    /// hardware available to this project). ``pixel_width``/``pixel_height`` are reported
    /// identical to ``width``/``height`` since WinForms' ``Screen.Bounds`` is already in
    /// physical pixels on a per-monitor-DPI-aware process, unlike macOS's distinct
    /// points-vs-pixels split — a human with real hardware should confirm this holds across
    /// monitors of different DPI scale factors before relying on it.</summary>
    public static List<DisplayEntry> Enumerate()
    {
        var screens = Screen.AllScreens;
        var result = new List<DisplayEntry>(screens.Length);
        for (var i = 0; i < screens.Length; i++)
        {
            var s = screens[i];
            result.Add(new DisplayEntry(
                Id: i,
                DeviceName: s.DeviceName,
                Name: s.DeviceName,
                Width: s.Bounds.Width,
                Height: s.Bounds.Height,
                IsMain: s.Primary));
        }
        return result;
    }

    public static Dictionary<string, object?> ToJson(DisplayEntry d) => new()
    {
        ["id"] = d.Id,
        ["name"] = d.Name,
        ["width"] = d.Width,
        ["height"] = d.Height,
        ["pixel_width"] = d.Width,
        ["pixel_height"] = d.Height,
        ["is_main"] = d.IsMain,
    };
}
