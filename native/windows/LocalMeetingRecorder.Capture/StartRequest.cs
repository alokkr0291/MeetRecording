namespace LocalMeetingRecorder.Capture;

/// <summary>Mirrors ``StartOptions`` in ``capture/protocols.py`` field-for-field — see
/// ../../macos/PROTOCOL.md's ``start`` fields table for defaults/clamping.</summary>
internal sealed class StartRequest
{
    public required string OutputPath { get; init; }
    public int? DisplayId { get; init; }
    public bool CaptureMicrophone { get; init; } = true;
    public string? MicrophoneDeviceId { get; init; }
    public int Fps { get; init; } = 30;
    public int VideoBitrateKbps { get; init; } = 6000;
    public int AudioBitrateKbps { get; init; } = 128;
    public int MaxDimension { get; init; } = 1920;
    public bool ShowsCursor { get; init; } = true;
    public double StatsIntervalS { get; init; } = 5.0;
    public double MinFreeMB { get; init; } = 512.0;

    public static StartRequest Parse(Command cmd)
    {
        var outputPath = cmd.GetString("output_path");
        if (string.IsNullOrEmpty(outputPath))
        {
            throw new HelperError("bad_command", "\"start\" requires a non-empty \"output_path\"");
        }
        return new StartRequest
        {
            OutputPath = outputPath,
            DisplayId = cmd.Fields["display_id"] is System.Text.Json.Nodes.JsonValue dv
                && dv.TryGetValue(out int displayId)
                ? displayId
                : null,
            CaptureMicrophone = cmd.GetBool("capture_microphone", true),
            MicrophoneDeviceId = cmd.GetString("microphone_device_id"),
            Fps = Math.Clamp(cmd.GetInt("fps", 30), 1, 60),
            VideoBitrateKbps = Math.Max(500, cmd.GetInt("video_bitrate_kbps", 6000)),
            AudioBitrateKbps = Math.Clamp(cmd.GetInt("audio_bitrate_kbps", 128), 32, 320),
            MaxDimension = Math.Max(320, cmd.GetInt("max_dimension", 1920)),
            ShowsCursor = cmd.GetBool("shows_cursor", true),
            StatsIntervalS = Math.Max(0.5, cmd.GetDouble("stats_interval_s", 5.0)),
            MinFreeMB = cmd.GetDouble("min_free_mb", 512.0),
        };
    }
}

/// <summary>Mirrors ``HelperError``'s cases in main.swift and the error-code table in
/// ../../macos/PROTOCOL.md exactly — the Python side's ``ErrorInfo.to_exception()``
/// switches on ``code``, not a freeform message, so these strings are load-bearing.</summary>
internal sealed class HelperError : Exception
{
    public string Code { get; }
    public bool Fatal { get; }
    public string? Permission { get; }

    public HelperError(string code, string message, bool fatal = false, string? permission = null)
        : base(message)
    {
        Code = code;
        Fatal = fatal;
        Permission = permission;
    }

    public static HelperError PermissionDenied(string which) =>
        new("permission_denied",
            which == "screen" ? "Screen Recording permission is not granted" : "Microphone permission is not granted",
            fatal: true,
            permission: which);
}
