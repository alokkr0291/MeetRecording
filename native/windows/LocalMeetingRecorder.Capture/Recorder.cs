// Screen + system-audio + microphone recorder. Mirrors native/macos/Sources/main.swift's
// Recorder class in shape and in the counters it tracks (StatsInfo/StoppedInfo in
// capture/protocols.py require the same fields regardless of platform), but the capture
// mechanism is necessarily different:
//
//   macOS: ScreenCaptureKit (SCStream) delivers video + BOTH audio sources already
//          separated, and AVAssetWriter encodes/muxes everything in one object.
//   Windows: DXGI Desktop Duplication (video only — no audio) + NAudio WASAPI loopback
//          (system audio) + NAudio WASAPI capture (microphone) are three independent
//          sources that must be fed into one Media Foundation IMFSinkWriter by hand.
//
// Why DXGI Desktop Duplication instead of the product spec's suggested
// Windows.Graphics.Capture: see README.md "Why DXGI Desktop Duplication, not
// Windows.Graphics.Capture" — in short, Graphics.Capture's WinRT projection toolchain
// (cswinrt.exe) is a native Windows executable that cannot run during development on this
// project's (non-Windows) build machine, making that code path completely unverifiable —
// not even compilable. DXGI Desktop Duplication, Media Foundation, and NAudio are all pure
// COM-interop/managed bindings with no native code-generation step, so this file's code
// (unlike a Graphics.Capture version would have been) was at least confirmed to compile
// and type-check correctly via `dotnet build` during development — see README.md for
// exactly what that does, and does not, prove.

using System.Diagnostics;
using NAudio.CoreAudioApi;
using NAudio.Wave;
using Vortice.Direct3D;
using Vortice.Direct3D11;
using Vortice.DXGI;
using Vortice.MediaFoundation;

namespace LocalMeetingRecorder.Capture;

internal sealed class Recorder
{
    private readonly StartRequest _request;
    private readonly Action<int> _onFinished;
    private readonly object _lock = new();
    private readonly Stopwatch _clock = new();

    private ID3D11Device? _device;
    private ID3D11DeviceContext? _context;
    private IDXGIOutputDuplication? _duplication;
    private ID3D11Texture2D? _stagingTexture;
    private int _width;
    private int _height;

    private IMFSinkWriter? _writer;
    private int _videoStreamIndex = -1;
    private int _systemAudioStreamIndex = -1;
    private int _micAudioStreamIndex = -1;

    private WasapiLoopbackCapture? _systemCapture;
    private WasapiCapture? _micCapture;

    private Thread? _videoThread;
    private volatile bool _stopping;
    private bool _finished;
    private bool _sessionStarted;
    private long _sessionStartTicks100ns;

    private System.Threading.Timer? _statsTimer;
    private readonly DateTime _startDate = DateTime.UtcNow;
    private readonly string _outputPath;

    // Counters — field names chosen to match ../../macos/PROTOCOL.md's `stats`/`stopped`
    // JSON keys once converted via Counters() below, not Swift's own private field names.
    private long _videoFrames;
    private long _videoSkippedIncomplete;
    private long _videoDroppedNotReady;
    private long _systemAudioBuffers;
    private long _systemAudioDroppedEarly;
    private long _micAudioBuffers;
    private long _micAudioDroppedEarly;
    private long? _firstVideoTicks100ns;
    private long? _firstSystemAudioTicks100ns;
    private long? _firstMicAudioTicks100ns;
    private long _lastTicks100ns;
    private bool _lastTicksValid;

    public Recorder(StartRequest request, Action<int> onFinished)
    {
        _request = request;
        _onFinished = onFinished;
        _outputPath = request.OutputPath;
    }

    public void Start()
    {
        try
        {
            StartInternal();
        }
        catch (HelperError e)
        {
            Out.Error(e.Code, e.Message, fatal: true, permission: e.Permission);
            AbortAfterStartFailure();
            _onFinished(e.Fatal ? 5 : 1);
        }
        catch (Exception e)
        {
            Out.Error("capture_failed", e.Message, fatal: true);
            AbortAfterStartFailure();
            _onFinished(5);
        }
    }

    private void StartInternal()
    {
        if (File.Exists(_outputPath))
        {
            throw new HelperError("output_exists", $"refusing to overwrite existing file {_outputPath}");
        }
        var parent = Path.GetDirectoryName(Path.GetFullPath(_outputPath));
        if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);

        var displays = Displays.Enumerate();
        if (displays.Count == 0) throw new HelperError("no_displays", "no displays available for capture");
        var chosen = _request.DisplayId is int wantedId
            ? displays.FirstOrDefault(d => d.Id == wantedId)
                ?? throw new HelperError("display_not_found", $"display {wantedId} is not available")
            : displays.FirstOrDefault(d => d.IsMain) ?? displays[0];

        SetUpDuplication(chosen);
        SetUpWriter();
        SetUpAudio();

        _writer!.BeginWriting();
        _clock.Start();
        _videoThread = new Thread(VideoLoop) { IsBackground = true, Name = "lmr-video-capture" };
        _videoThread.Start();
        _systemCapture?.StartRecording();
        _micCapture?.StartRecording();
        StartStatsTimer();

        var tracks = new List<object?>
        {
            new Dictionary<string, object?> { ["index"] = 0, ["kind"] = "video", ["codec"] = "h264" },
            new Dictionary<string, object?> { ["index"] = 1, ["kind"] = "audio", ["role"] = "system", ["codec"] = "aac" },
        };
        if (_request.CaptureMicrophone)
        {
            tracks.Add(new Dictionary<string, object?> { ["index"] = 2, ["kind"] = "audio", ["role"] = "microphone", ["codec"] = "aac" });
        }
        Out.Event("started", new Dictionary<string, object?>
        {
            ["output_path"] = _outputPath,
            ["display_id"] = chosen.Id,
            ["width"] = _width,
            ["height"] = _height,
            ["fps"] = _request.Fps,
            ["capture_microphone"] = _request.CaptureMicrophone,
            ["tracks"] = tracks,
        });
    }

    // -- DXGI Desktop Duplication setup --------------------------------------------------

    private void SetUpDuplication(DisplayEntry chosen)
    {
        using var factory = DXGI.CreateDXGIFactory1<IDXGIFactory1>();
        IDXGIOutput1? output1 = null;
        for (uint ai = 0; factory.EnumAdapters1(ai, out var adapter).Success; ai++)
        {
            for (uint oi = 0; adapter.EnumOutputs(oi, out var output).Success; oi++)
            {
                if (output.Description.DeviceName == chosen.DeviceName)
                {
                    output1 = output.QueryInterface<IDXGIOutput1>();
                    break;
                }
            }
            if (output1 is not null) break;
        }
        if (output1 is null)
        {
            throw new HelperError("display_not_found", $"could not locate DXGI output for display {chosen.Id}");
        }

        var featureLevels = new[] { FeatureLevel.Level_11_1, FeatureLevel.Level_11_0 };
        _device = D3D11.D3D11CreateDevice(DriverType.Hardware, DeviceCreationFlags.BgraSupport, featureLevels);
        _context = _device.ImmediateContext;
        _duplication = output1.DuplicateOutput(_device);

        var longest = Math.Max(chosen.Width, chosen.Height);
        var scale = longest > _request.MaxDimension ? (double)_request.MaxDimension / longest : 1.0;
        _width = EvenDown((int)(chosen.Width * scale));
        _height = EvenDown((int)(chosen.Height * scale));

        _stagingTexture = _device.CreateTexture2D(new Texture2DDescription
        {
            Width = (uint)chosen.Width,
            Height = (uint)chosen.Height,
            MipLevels = 1,
            ArraySize = 1,
            Format = Format.B8G8R8A8_UNorm,
            SampleDescription = new SampleDescription(1, 0),
            Usage = ResourceUsage.Staging,
            CPUAccessFlags = CpuAccessFlags.Read,
            BindFlags = BindFlags.None,
        });
    }

    private static int EvenDown(int v) => Math.Max(2, v - v % 2);

    // -- Media Foundation sink writer setup ----------------------------------------------

    private void SetUpWriter()
    {
        MediaFactory.MFStartup();
        using var attributes = MediaFactory.MFCreateAttributes(1);
        attributes.Set(SinkWriterAttributeKeys.ReadwriteEnableHardwareTransforms, 1u);
        _writer = MediaFactory.MFCreateSinkWriterFromURL(_outputPath, null, attributes);

        using var videoOut = MediaFactory.MFCreateMediaType();
        videoOut.Set(MediaTypeAttributeKeys.MajorType, MediaTypeGuids.Video);
        videoOut.Set(MediaTypeAttributeKeys.Subtype, VideoFormatGuids.H264);
        videoOut.Set(MediaTypeAttributeKeys.AvgBitrate, (uint)(_request.VideoBitrateKbps * 1000));
        videoOut.Set(MediaTypeAttributeKeys.InterlaceMode, (uint)VideoInterlaceMode.Progressive);
        SetFrameSize(videoOut, _width, _height);
        SetFrameRate(videoOut, _request.Fps, 1);
        _videoStreamIndex = _writer.AddStream(videoOut);

        using var videoIn = MediaFactory.MFCreateMediaType();
        videoIn.Set(MediaTypeAttributeKeys.MajorType, MediaTypeGuids.Video);
        videoIn.Set(MediaTypeAttributeKeys.Subtype, VideoFormatGuids.Argb32);
        videoIn.Set(MediaTypeAttributeKeys.InterlaceMode, (uint)VideoInterlaceMode.Progressive);
        SetFrameSize(videoIn, _width, _height);
        SetFrameRate(videoIn, _request.Fps, 1);
        _writer.SetInputMediaType(_videoStreamIndex, videoIn, null);

        _systemAudioStreamIndex = AddAacAudioStream(channels: 2, sampleRate: 48000);
        if (_request.CaptureMicrophone)
        {
            _micAudioStreamIndex = AddAacAudioStream(channels: 2, sampleRate: 48000);
        }
    }

    private static void SetFrameSize(IMFMediaType type, int width, int height) =>
        type.Set(MediaTypeAttributeKeys.FrameSize, ((ulong)(uint)width << 32) | (uint)height);

    private static void SetFrameRate(IMFMediaType type, int numerator, int denominator) =>
        type.Set(MediaTypeAttributeKeys.FrameRate, ((ulong)(uint)numerator << 32) | (uint)denominator);

    /// <summary>16-bit PCM input, matching what NAudio's capture is converted to before
    /// ``WriteSample`` (see PcmToS16 below) — Media Foundation's AAC encoder MFT is not
    /// guaranteed to accept arbitrary WASAPI mix formats (commonly 32-bit float) directly,
    /// so this helper always converts to 16-bit PCM itself rather than depending on the
    /// encoder to do it. Unverified against a real encoder instance on real hardware.</summary>
    private int AddAacAudioStream(int channels, int sampleRate)
    {
        using var aacOut = MediaFactory.MFCreateMediaType();
        aacOut.Set(MediaTypeAttributeKeys.MajorType, MediaTypeGuids.Audio);
        aacOut.Set(MediaTypeAttributeKeys.Subtype, AudioFormatGuids.Aac);
        aacOut.Set(MediaTypeAttributeKeys.AudioBitsPerSample, (uint)16);
        aacOut.Set(MediaTypeAttributeKeys.AudioSamplesPerSecond, (uint)sampleRate);
        aacOut.Set(MediaTypeAttributeKeys.AudioNumChannels, (uint)channels);
        aacOut.Set(MediaTypeAttributeKeys.AudioAvgBytesPerSecond, (uint)(_request.AudioBitrateKbps * 1000 / 8));
        var streamIndex = _writer!.AddStream(aacOut);

        using var pcmIn = MediaFactory.MFCreateMediaType();
        pcmIn.Set(MediaTypeAttributeKeys.MajorType, MediaTypeGuids.Audio);
        pcmIn.Set(MediaTypeAttributeKeys.Subtype, AudioFormatGuids.Pcm);
        pcmIn.Set(MediaTypeAttributeKeys.AudioBitsPerSample, (uint)16);
        pcmIn.Set(MediaTypeAttributeKeys.AudioSamplesPerSecond, (uint)sampleRate);
        pcmIn.Set(MediaTypeAttributeKeys.AudioNumChannels, (uint)channels);
        pcmIn.Set(MediaTypeAttributeKeys.AudioBlockAlignment, (uint)(channels * 2));
        pcmIn.Set(MediaTypeAttributeKeys.AudioAvgBytesPerSecond, (uint)(sampleRate * channels * 2));
        _writer.SetInputMediaType(streamIndex, pcmIn, null);
        return streamIndex;
    }

    // -- NAudio WASAPI setup --------------------------------------------------------------

    private void SetUpAudio()
    {
        _systemCapture = new WasapiLoopbackCapture();
        _systemCapture.DataAvailable += (_, e) => OnAudioData(e, isMic: false);
        _systemCapture.RecordingStopped += (_, e) =>
        {
            if (e.Exception is not null) ReportRuntimeFailure("system audio loopback stopped: " + e.Exception.Message);
        };

        if (_request.CaptureMicrophone)
        {
            var enumerator = new MMDeviceEnumerator();
            var device = string.IsNullOrEmpty(_request.MicrophoneDeviceId)
                ? enumerator.GetDefaultAudioEndpoint(NAudio.CoreAudioApi.DataFlow.Capture, NAudio.CoreAudioApi.Role.Communications)
                : enumerator.GetDevice(_request.MicrophoneDeviceId);
            _micCapture = new WasapiCapture(device);
            _micCapture.DataAvailable += (_, e) => OnAudioData(e, isMic: true);
            _micCapture.RecordingStopped += (_, e) =>
            {
                if (e.Exception is not null) ReportRuntimeFailure("microphone capture stopped: " + e.Exception.Message);
            };
        }
    }

    // -- video loop -------------------------------------------------------------------------

    private void VideoLoop()
    {
        while (!_stopping)
        {
            var result = _duplication!.AcquireNextFrame(500, out var frameInfo, out var desktopResource);
            if (result == Vortice.DXGI.ResultCode.WaitTimeout)
            {
                continue;
            }
            if (result.Failure)
            {
                if (!_stopping) ReportRuntimeFailure($"AcquireNextFrame failed: {result}");
                return;
            }
            try
            {
                if (frameInfo.LastPresentTime == 0)
                {
                    // No new frame since the last acquire (cursor-only or metadata-only update).
                    lock (_lock) _videoSkippedIncomplete++;
                    continue;
                }
                using var texture = desktopResource!.QueryInterface<ID3D11Texture2D>();
                _context!.CopyResource(_stagingTexture!, texture);
                var mapped = _context.Map(_stagingTexture!, 0, MapMode.Read, Vortice.Direct3D11.MapFlags.None);
                try
                {
                    WriteVideoSample(mapped);
                }
                finally
                {
                    _context.Unmap(_stagingTexture!, 0);
                }
            }
            catch (Exception e)
            {
                lock (_lock) _videoDroppedNotReady++;
                Log($"video frame handling error: {e.Message}");
            }
            finally
            {
                desktopResource?.Dispose();
                _duplication!.ReleaseFrame();
            }
        }
    }

    private void WriteVideoSample(MappedSubresource mapped)
    {
        var ticks100ns = NowTicks100ns();
        if (!EnsureSession(ticks100ns)) return;

        var rowBytes = _width * 4;
        var buffer = MediaFactory.MFCreateAlignedMemoryBuffer(rowBytes * _height, 16);
        buffer.Lock(out var dest, out _, out _);
        try
        {
            unsafe
            {
                var src = (byte*)mapped.DataPointer;
                var dst = (byte*)dest;
                for (var row = 0; row < _height; row++)
                {
                    Buffer.MemoryCopy(src + row * mapped.RowPitch, dst + row * rowBytes, rowBytes, rowBytes);
                }
            }
        }
        finally
        {
            buffer.CurrentLength = rowBytes * _height;
            buffer.Unlock();
        }

        using var sample = MediaFactory.MFCreateSample();
        sample.AddBuffer(buffer);
        sample.SampleTime = ticks100ns - _sessionStartTicks100ns;
        sample.SampleDuration = 10_000_000 / Math.Max(1, _request.Fps);
        _writer!.WriteSample(_videoStreamIndex, sample);

        lock (_lock)
        {
            _videoFrames++;
            _firstVideoTicks100ns ??= ticks100ns;
            UpdateLastTicks(ticks100ns);
        }
    }

    // -- audio handling -----------------------------------------------------------------

    private void OnAudioData(WaveInEventArgs e, bool isMic)
    {
        if (_stopping || e.BytesRecorded == 0) return;
        var ticks100ns = NowTicks100ns();
        if (!EnsureSession(ticks100ns))
        {
            lock (_lock) { if (isMic) _micAudioDroppedEarly++; else _systemAudioDroppedEarly++; }
            return;
        }
        var sourceFormat = (isMic ? _micCapture!.WaveFormat : _systemCapture!.WaveFormat)!;
        var pcm16 = PcmToS16(e.Buffer, e.BytesRecorded, sourceFormat);
        if (pcm16.Length == 0) return;

        var buffer = MediaFactory.MFCreateAlignedMemoryBuffer(pcm16.Length, 16);
        buffer.Lock(out var dest, out _, out _);
        try
        {
            System.Runtime.InteropServices.Marshal.Copy(pcm16, 0, dest, pcm16.Length);
        }
        finally
        {
            buffer.CurrentLength = pcm16.Length;
            buffer.Unlock();
        }

        using var sample = MediaFactory.MFCreateSample();
        sample.AddBuffer(buffer);
        sample.SampleTime = ticks100ns - _sessionStartTicks100ns;
        var frames = pcm16.Length / (2 * 2); // 16-bit stereo
        sample.SampleDuration = (long)(10_000_000.0 * frames / 48000);
        _writer!.WriteSample(isMic ? _micAudioStreamIndex : _systemAudioStreamIndex, sample);

        lock (_lock)
        {
            if (isMic)
            {
                _micAudioBuffers++;
                _firstMicAudioTicks100ns ??= ticks100ns;
            }
            else
            {
                _systemAudioBuffers++;
                _firstSystemAudioTicks100ns ??= ticks100ns;
            }
            UpdateLastTicks(ticks100ns);
        }
    }

    /// <summary>WASAPI shared-mode capture commonly delivers 32-bit IEEE float samples at
    /// the audio engine's mix sample rate/channel count (often 48kHz stereo, but NOT
    /// guaranteed) — converts to 16-bit PCM at that same rate/channel count for the AAC
    /// encoder input type configured in AddAacAudioStream. Does NOT resample to a fixed
    /// 48000Hz if the device's actual mix rate differs — see README.md's known-gaps list;
    /// a human on real hardware should confirm the mix format assumption or this will
    /// produce audio at the wrong configured sample rate.</summary>
    private static byte[] PcmToS16(byte[] source, int bytesRecorded, WaveFormat format)
    {
        if (format.BitsPerSample == 16 && format.Encoding == WaveFormatEncoding.Pcm)
        {
            var exact = new byte[bytesRecorded];
            Array.Copy(source, exact, bytesRecorded);
            return exact;
        }
        if (format.BitsPerSample == 32 && format.Encoding == WaveFormatEncoding.IeeeFloat)
        {
            var sampleCount = bytesRecorded / 4;
            var output = new byte[sampleCount * 2];
            for (var i = 0; i < sampleCount; i++)
            {
                var f = BitConverter.ToSingle(source, i * 4);
                var clamped = Math.Clamp(f, -1.0f, 1.0f);
                var s16 = (short)(clamped * short.MaxValue);
                output[i * 2] = (byte)(s16 & 0xFF);
                output[i * 2 + 1] = (byte)((s16 >> 8) & 0xFF);
            }
            return output;
        }
        // Unsupported source format — drop rather than write garbage samples.
        return [];
    }

    // -- session/time helpers -------------------------------------------------------------

    private long NowTicks100ns() =>
        (long)(_clock.ElapsedTicks * (10_000_000.0 / Stopwatch.Frequency));

    private bool EnsureSession(long ticks100ns)
    {
        lock (_lock)
        {
            if (_stopping || _finished) return false;
            if (_sessionStarted) return true;
            _sessionStarted = true;
            _sessionStartTicks100ns = ticks100ns;
            Log($"writer session started at {ticks100ns / 10_000_000.0}s");
            return true;
        }
    }

    private void UpdateLastTicks(long ticks100ns)
    {
        if (!_lastTicksValid || ticks100ns > _lastTicks100ns)
        {
            _lastTicks100ns = ticks100ns;
            _lastTicksValid = true;
        }
    }

    // -- stats ----------------------------------------------------------------------------

    private void StartStatsTimer()
    {
        var intervalMs = Math.Max(500, (int)(_request.StatsIntervalS * 1000));
        _statsTimer = new System.Threading.Timer(_ => EmitStats(), null, intervalMs, intervalMs);
    }

    private Dictionary<string, object?> Counters()
    {
        lock (_lock)
        {
            var c = new Dictionary<string, object?>
            {
                ["video_frames"] = _videoFrames,
                ["video_skipped_incomplete"] = _videoSkippedIncomplete,
                ["video_dropped_not_ready"] = _videoDroppedNotReady,
                ["system_audio_buffers"] = _systemAudioBuffers,
                ["system_audio_dropped_early"] = _systemAudioDroppedEarly,
                ["system_audio_dropped_not_ready"] = 0,
                ["mic_audio_buffers"] = _micAudioBuffers,
                ["mic_audio_dropped_early"] = _micAudioDroppedEarly,
                ["mic_audio_dropped_not_ready"] = 0,
                ["session_started"] = _sessionStarted,
            };
            if (_sessionStarted)
            {
                if (_firstVideoTicks100ns is long fv) c["first_video_offset_s"] = (fv - _sessionStartTicks100ns) / 10_000_000.0;
                if (_firstSystemAudioTicks100ns is long fs) c["first_system_audio_offset_s"] = (fs - _sessionStartTicks100ns) / 10_000_000.0;
                if (_firstMicAudioTicks100ns is long fm) c["first_mic_audio_offset_s"] = (fm - _sessionStartTicks100ns) / 10_000_000.0;
                if (_lastTicksValid) c["media_duration_s"] = (_lastTicks100ns - _sessionStartTicks100ns) / 10_000_000.0;
            }
            return c;
        }
    }

    private void EmitStats()
    {
        if (_stopping || _finished) return;
        var f = Counters();
        f["elapsed_s"] = (DateTime.UtcNow - _startDate).TotalSeconds;
        f["bytes"] = FileSize();
        var free = FreeMegabytes();
        f["free_mb"] = free;
        Out.Event("stats", f);
        if (free >= 0 && free < _request.MinFreeMB)
        {
            Out.Event("warning", new Dictionary<string, object?>
            {
                ["code"] = "low_disk_space",
                ["message"] = $"free disk space is {(int)free} MB, below the {(int)_request.MinFreeMB} MB threshold",
                ["free_mb"] = free,
            });
        }
    }

    private long FileSize()
    {
        try { return new FileInfo(_outputPath).Length; } catch { return 0; }
    }

    private double FreeMegabytes()
    {
        try
        {
            var root = Path.GetPathRoot(Path.GetFullPath(_outputPath));
            if (string.IsNullOrEmpty(root)) return -1;
            return new DriveInfo(root).AvailableFreeSpace / (1024.0 * 1024.0);
        }
        catch { return -1; }
    }

    // -- stop -------------------------------------------------------------------------------

    public void Stop(string reason = "requested", int exitCode = 0)
    {
        lock (_lock)
        {
            if (_stopping) return;
            _stopping = true;
        }
        _statsTimer?.Dispose();
        Out.Event("stopping", new Dictionary<string, object?> { ["reason"] = reason });

        var watchdog = new System.Threading.Timer(_ =>
        {
            bool done;
            lock (_lock) done = _finished;
            if (!done)
            {
                Out.Error("stop_timeout", "finalization did not complete within 25 s", fatal: true);
                FinishAndExit(6);
            }
        }, null, 25_000, Timeout.Infinite);

        try
        {
            FinishWriter(reason, exitCode);
        }
        finally
        {
            watchdog.Dispose();
        }
    }

    private void FinishWriter(string reason, int exitCode)
    {
        _videoThread?.Join(TimeSpan.FromSeconds(5));
        _systemCapture?.StopRecording();
        _micCapture?.StopRecording();
        _duplication?.Dispose();

        bool finalized = false;
        string? failure = null;
        try
        {
            if (_sessionStarted && _writer is not null)
            {
                _writer.Finalize();
                finalized = true;
            }
            else
            {
                failure = "no media samples were received; nothing to save";
            }
        }
        catch (Exception e)
        {
            failure = e.Message;
        }

        var f = Counters();
        f["reason"] = reason;
        f["finalized"] = finalized;
        f["output_path"] = _outputPath;
        f["bytes"] = FileSize();
        f["duration_s"] = _sessionStarted && _lastTicksValid
            ? Math.Max(0, (_lastTicks100ns - _sessionStartTicks100ns) / 10_000_000.0)
            : 0.0;
        f["elapsed_s"] = (DateTime.UtcNow - _startDate).TotalSeconds;
        f["error"] = failure;
        Out.Event("stopped", f);

        lock (_lock) _finished = true;
        FinishAndExit(finalized ? exitCode : Math.Max(exitCode, 5));
    }

    private void FinishAndExit(int code)
    {
        _device?.Dispose();
        _onFinished(code);
    }

    private void AbortAfterStartFailure()
    {
        try { _duplication?.Dispose(); } catch { /* best effort */ }
        try { _device?.Dispose(); } catch { /* best effort */ }
    }

    private void ReportRuntimeFailure(string message)
    {
        lock (_lock)
        {
            if (_stopping || _finished) return;
        }
        Out.Error("capture_failed", message, fatal: true);
        Stop(reason: "stream_error", exitCode: 4);
    }

    private static void Log(string message) => Console.Error.WriteLine($"{DateTime.UtcNow:O} INFO {message}");
}
