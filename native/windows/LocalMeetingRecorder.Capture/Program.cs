// Entry point + stdin command loop. Mirrors native/macos/Sources/main.swift's `Helper`
// class and its bottom-of-file entry point section line-for-line in *behavior* (same
// commands, same one-shot args, same stdin-EOF-stops-the-recording semantics) even though
// the concurrency primitives are necessarily different (GCD dispatch queues vs. a plain
// background thread + lock here).

using System.Runtime.InteropServices;
using LocalMeetingRecorder.Capture;

const string HelperVersion = "0.1.0";
const int ProtocolVersion = 1;

if (args.Length > 0 && (args[0] == "--version" || args[0] == "-v"))
{
    Console.WriteLine($"lmr-capture-helper {HelperVersion} (protocol {ProtocolVersion})");
    return 0;
}
if (args.Length > 0 && (args[0] == "--help" || args[0] == "-h"))
{
    Console.WriteLine($"""
        lmr-capture-helper {HelperVersion}
        Usage:
          lmr-capture-helper.exe                 run the JSON-lines command loop on stdin/stdout
          lmr-capture-helper.exe probe            print permissions and version, then exit
          lmr-capture-helper.exe list-displays    print available displays, then exit
          lmr-capture-helper.exe request-permissions  (reports permissions; Windows has no consent prompt to trigger)
          lmr-capture-helper.exe --version
        """);
    return 0;
}
if (args.Length > 0 && args[0] is "probe" or "list-displays" or "request-permissions")
{
    switch (args[0])
    {
        case "probe":
            Out.Event("probe", new Dictionary<string, object?>
            {
                ["version"] = HelperVersion,
                ["protocol"] = ProtocolVersion,
                ["windows"] = Environment.OSVersion.VersionString,
                ["permissions"] = Permissions.Snapshot(),
            });
            break;
        case "list-displays":
            Out.Event("displays", new Dictionary<string, object?>
            {
                ["displays"] = Displays.Enumerate().Select(Displays.ToJson).ToList(),
            });
            break;
        default:
            Out.Event("permissions", Permissions.Request());
            break;
    }
    return 0;
}

var helper = new HelperCommandLoop(HelperVersion, ProtocolVersion);
helper.Run();
return helper.ExitCode;

internal sealed class HelperCommandLoop
{
    private readonly string _version;
    private readonly int _protocolVersion;
    private Recorder? _recorder;
    private readonly object _lock = new();
    private bool _exiting;
    private readonly ManualResetEventSlim _done = new(false);

    public int ExitCode { get; private set; }

    public HelperCommandLoop(string version, int protocolVersion)
    {
        _version = version;
        _protocolVersion = protocolVersion;
    }

    public void Run()
    {
        Out.Event("ready", new Dictionary<string, object?>
        {
            ["version"] = _version,
            ["protocol"] = _protocolVersion,
            ["pid"] = Environment.ProcessId,
            ["windows"] = Environment.OSVersion.VersionString,
        });

        AppDomain.CurrentDomain.ProcessExit += (_, _) => StopOnSignal();
        Console.CancelKeyPress += (_, e) =>
        {
            e.Cancel = true; // we handle shutdown ourselves, same as SIGINT on macOS
            StopOnSignal();
        };

        var reader = new Thread(ReadLoop) { IsBackground = true, Name = "stdin-reader" };
        reader.Start();
        _done.Wait();
    }

    private void ReadLoop()
    {
        string? line;
        while ((line = Console.In.ReadLine()) is not null)
        {
            var trimmed = line.Trim();
            if (trimmed.Length == 0) continue;
            Handle(trimmed);
        }
        HandleEof();
    }

    private void Handle(string line)
    {
        var cmd = Command.TryParse(line);
        if (cmd is null)
        {
            Out.Error("bad_command", "expected a JSON object with a string \"cmd\" field");
            return;
        }
        switch (cmd.Name)
        {
            case "ping":
                Out.Event("pong");
                break;
            case "probe":
                Out.Event("probe", new Dictionary<string, object?>
                {
                    ["version"] = _version,
                    ["protocol"] = _protocolVersion,
                    ["windows"] = Environment.OSVersion.VersionString,
                    ["permissions"] = Permissions.Snapshot(),
                });
                break;
            case "request-permissions":
                Out.Event("permissions", Permissions.Request());
                break;
            case "list-displays":
                Out.Event("displays", new Dictionary<string, object?>
                {
                    ["displays"] = Displays.Enumerate().Select(Displays.ToJson).ToList(),
                });
                break;
            case "start":
                StartRecording(cmd);
                break;
            case "stop":
                StopRecording();
                break;
            default:
                Out.Error("unknown_command", $"unknown cmd \"{cmd.Name}\"");
                break;
        }
    }

    private void StartRecording(Command cmd)
    {
        lock (_lock)
        {
            if (_recorder is not null)
            {
                Out.Error("already_recording", "a recording is already active in this helper");
                return;
            }
            StartRequest request;
            try
            {
                request = StartRequest.Parse(cmd);
            }
            catch (HelperError e)
            {
                Out.Error(e.Code, e.Message, fatal: e.Fatal, permission: e.Permission);
                return;
            }
            _recorder = new Recorder(request, Exit);
        }
        _recorder.Start();
    }

    private void StopRecording()
    {
        Recorder? r;
        lock (_lock) r = _recorder;
        if (r is null)
        {
            Out.Error("not_recording", "no active recording");
            return;
        }
        r.Stop();
    }

    private void HandleEof()
    {
        Recorder? r;
        lock (_lock) r = _recorder;
        if (r is not null)
        {
            r.Stop(reason: "stdin_eof");
        }
        else
        {
            Exit(0);
        }
    }

    private void StopOnSignal()
    {
        Recorder? r;
        lock (_lock) r = _recorder;
        if (r is not null)
        {
            r.Stop(reason: "signal");
        }
        else
        {
            Exit(0);
        }
    }

    private void Exit(int code)
    {
        lock (_lock)
        {
            if (_exiting) return;
            _exiting = true;
            ExitCode = code;
        }
        Console.Error.WriteLine($"{DateTime.UtcNow:O} INFO exiting with code {code}");
        _done.Set();
    }
}
