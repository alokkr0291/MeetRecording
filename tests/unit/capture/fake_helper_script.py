"""Source for a tiny stand-in helper process, run via ``python -c`` in tests.

Speaks just enough of the real JSON-lines protocol (see ``native/macos/PROTOCOL.md``) to
exercise ``HelperClient`` without the real Swift binary or any macOS permission. Not a
protocol validator — it trusts its own test callers to send well-formed commands, except
where a test deliberately asks it to misbehave (``garbage``, ``crash``, ``hang``).
"""

from __future__ import annotations

FAKE_HELPER_SOURCE = r"""
import json, signal, sys, time

def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

emit({"event": "ready", "version": "0.0.0-fake", "protocol": 1, "pid": 0, "macos": "test"})

state = {"ignore_term": False}

def handle_term(signum, frame):
    if state["ignore_term"]:
        return
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_term)

for raw in sys.stdin:
    line = raw.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        continue
    cmd = obj.get("cmd")
    if cmd == "probe":
        emit({
            "event": "probe", "version": "0.0.0-fake", "protocol": 1, "macos": "test",
            "permissions": {"screen": "granted", "screen_preflight": True, "microphone": "granted"},
        })
    elif cmd == "list-displays":
        emit({"event": "displays", "displays": [
            {"id": 1, "name": "Fake Display", "width": 1920, "height": 1080,
             "pixel_width": 1920, "pixel_height": 1080, "is_main": True},
        ]})
    elif cmd == "start":
        emit({
            "event": "started", "output_path": obj.get("output_path", ""), "display_id": 1,
            "width": 1920, "height": 1080, "fps": obj.get("fps", 30),
            "capture_microphone": obj.get("capture_microphone", True),
            "tracks": [
                {"index": 0, "kind": "video", "codec": "h264"},
                {"index": 1, "kind": "audio", "role": "system", "codec": "aac"},
            ],
        })
    elif cmd == "stop":
        emit({"event": "stopping", "reason": "requested"})
        time.sleep(0.05)
        emit({
            "event": "stopped", "reason": "requested", "finalized": True,
            "output_path": obj.get("output_path", ""), "bytes": 1024, "duration_s": 1.0,
            "elapsed_s": 1.0,
        })
        sys.exit(0)
    elif cmd == "garbage":
        sys.stdout.write("not json at all\n")
        sys.stdout.flush()
    elif cmd == "crash":
        sys.exit(9)
    elif cmd == "hang":
        time.sleep(30)
    elif cmd == "hang-ignore-term":
        state["ignore_term"] = True
        time.sleep(30)
    elif cmd == "quit":
        sys.exit(0)
    else:
        emit({
            "event": "error", "code": "bad_command", "message": "unknown command",
            "fatal": False,
        })
sys.exit(0)
"""
