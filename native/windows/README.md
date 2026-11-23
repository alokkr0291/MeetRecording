# lmr-capture-helper (Windows)

Windows counterpart to `native/macos/Sources/main.swift`: a small, standalone executable
that captures the screen, system audio, and microphone into one media file, driven by the
Python app over the same JSON-lines protocol as the macOS helper. See
[`PROTOCOL.md`](./PROTOCOL.md) for the wire format (shared with macOS — there is only ever
one protocol, documented once).

## Status: written and compiled, never run

**This code has never been executed.** It was developed entirely on a macOS machine with
no Windows hardware available at any point. Every claim below about what is "verified" or
"confirmed" means *compiles correctly against the real .NET/WinRT/COM-interop API surface
via `dotnet build`* — never that it has captured a single real frame or audio sample. The
project's own standing rule (`CLAUDE.md`: "Never claim capture works because code
compiles") applies with full force here; this file exists specifically so nobody mistakes
"it builds" for "it works."

A human with a real Windows 10 (build 19041+) or Windows 11 machine needs to run
`scripts/build_helper_windows.ps1`, then exercise the helper for real — ideally via a
Windows equivalent of `tests/capture/test_real_capture.py` (see
`tests/capture/test_real_capture_windows.py`, gated the same way by `LMR_HW_TESTS=1` and
`sys.platform == "win32"`) — before this is trusted for an actual meeting recording.

## Why DXGI Desktop Duplication, not Windows.Graphics.Capture

The product spec that kicked off this phase suggested `Windows.Graphics.Capture` (the
modern WinRT screen-capture API). That was tried first. It does not work from this
project's actual development environment: **`Microsoft.Windows.CsWinRT`'s build-time code
generator (`cswinrt.exe`) is itself a native Windows PE executable**, and running
`dotnet build` against a WinRT-projected API on macOS fails with "cannot execute binary
file" — not a missing-package error, a genuine cross-compilation dead end. That would have
meant writing the entire capture path with *zero* compiler feedback of any kind, not even
syntax checking.

DXGI Desktop Duplication (`IDXGIOutputDuplication`, available since Windows 8) has no such
problem: it's accessed via plain COM-interop bindings ([Vortice.DXGI](https://github.com/amerkoleci/Vortice.Windows)/[Vortice.Direct3D11](https://github.com/amerkoleci/Vortice.Windows)),
which are ordinary managed NuGet packages with no native code-generation step — confirmed
by actually restoring and building against them from this Mac. Media Foundation's
`IMFSinkWriter` (encoding/muxing) is the same story via
[Vortice.MediaFoundation](https://github.com/amerkoleci/Vortice.Windows). Trading a newer,
Microsoft-recommended API for an older, extremely well-established, equally-capable one
(OBS Studio and most other Windows screen recorders use Desktop Duplication) in exchange
for actually being able to compile-check the code felt like the right trade given the
constraints — not a drive-by downgrade.

One real consequence: capturing a whole **display** (not just a window) via
`GraphicsCaptureItem.TryCreateFromDisplayId` needs Windows 10 build 19041 (version 2004)+
even with Graphics.Capture, so the "1903+" floor in the original spec was never fully
achievable for whole-display capture regardless of which API got used. This project
targets **19041+**.

## Architecture

| Concern | Implementation |
|---|---|
| Screen capture | DXGI Desktop Duplication (`IDXGIOutputDuplication.AcquireNextFrame`), polled on a dedicated thread, texture copied to a CPU-readable staging texture each frame |
| System audio ("what you hear") | NAudio `WasapiLoopbackCapture` on the default render device |
| Microphone | NAudio `WasapiCapture` on the default (or selected) capture device |
| Encoding + muxing | Media Foundation `IMFSinkWriter` — H.264 video, AAC audio ×2, one `.mp4` with the same "1 video + 2 audio tracks" shape as macOS's `.mov` |
| Display enumeration | `System.Windows.Forms.Screen.AllScreens` (simpler and more reliable than DXGI/WinRT enumeration for this purpose; correlated to the matching DXGI output by `DeviceName` when a display is actually selected for capture) |
| Permissions | Unconditionally "granted" — Windows has no OS-level consent gate for an unpackaged desktop app calling these APIs; see `Permissions.cs` for the full reasoning |

Source layout mirrors `main.swift`'s sections: `Protocol.cs` (JSON-lines I/O),
`StartRequest.cs` (command parsing + error codes), `Permissions.cs`, `Displays.cs`,
`Recorder.cs` (the capture/encode/stats/stop state machine — the bulk of the logic),
`Program.cs` (entry point + stdin command loop).

## Known gaps / things a human on real hardware must check

0. ~~**WASAPI loopback delivers nothing during render silence.**~~ **Confirmed on real
   hardware and fixed (2026-11-16).** The first real run of the hardware test showed the
   system-audio track starting at the first played sound (3.17s in), not at recording
   start — WASAPI loopback emits no packets while nothing is rendering. `Recorder.cs`
   now keeps the system track continuous: a 100ms pump timer writes zeroed 16-bit PCM
   from the track's current end position up to ~150ms behind the session clock whenever
   loopback is starved (serialized with real buffers via `_systemAudioFillLock`;
   real buffers fill any remaining hole and stamp monotonically past the filled
   silence). The `stats` event gains a Windows-only `system_audio_silence_buffers`
   diagnostic counter.
1. **WASAPI mix-format assumption.** `Recorder.cs` configures the AAC encoder's input
   media type as fixed 48kHz 16-bit stereo and converts whatever NAudio actually delivers
   (commonly 32-bit float, at the audio engine's *actual* mix sample rate, which is not
   guaranteed to be 48kHz) down to 16-bit samples — but does **not** resample if the real
   device mix rate differs from 48kHz. If a real machine's default audio device runs at a
   different rate, audio will play at the wrong speed/pitch. Needs a real-hardware check
   and very possibly a proper resampler (e.g. `NAudio.Wave.MediaFoundationResampler`) added.
2. **Multi-monitor / mixed-DPI pixel dimensions.** `Displays.cs` reports
   `pixel_width`/`pixel_height` identical to the DPI-scaled `width`/`height` from
   `Screen.Bounds`. Unverified against a real multi-monitor setup with different
   per-monitor DPI scale factors.
3. **DXGI output ↔ GDI screen correlation.** `Recorder.cs` matches a chosen display to its
   DXGI output by comparing `DeviceName` strings (e.g. `\\.\DISPLAY1`) between
   `Screen.AllScreens` and `IDXGIOutput.Description.DeviceName`. This is a standard,
   documented approach but has not been exercised against a real multi-GPU or
   multi-monitor configuration.
4. **DXGI Desktop Duplication's known rough edges** (none of which this code works around,
   since none can be verified without hardware): it can fail or need re-acquisition across
   a display mode change, a UAC secure-desktop transition, or when the output is lost
   (laptop lid close/reopen, RDP session, GPU driver reset). `VideoLoop` currently treats
   any `AcquireNextFrame` failure as fatal and stops the recording — a production-grade
   implementation would likely want to retry/re-duplicate in some of those cases, but
   getting that right requires triggering the real conditions on real hardware first.
5. **Admin/elevated windows and secure desktop content** are excluded from Desktop
   Duplication output by Windows itself (by design, for security) — not a bug in this
   code, but worth knowing: the recording will show black/skipped frames for elevated
   windows and things like the UAC prompt itself.
6. **AAC encoder MFT availability.** Media Foundation's AAC encoder ships with Windows, but
   its exact set of supported input formats/bitrates can vary by Windows build. The bitrate
   math in `AddAacAudioStream` is a reasonable default, not verified against the real
   encoder's accepted configuration list.
7. **No Windows packaging yet.** This phase is capture-only, per the product spec's scope.
   `storage/paths.py::resolve_helper_binary()` has a Windows branch for the dev build
   directory and a forward-looking (currently inert) `Contents/Helpers`-equivalent path
   for a future frozen build, but no PyInstaller/NSIS/MSI work was done — see PLAN.md's
   Phase 7 section for the existing inventory of that remaining work.

## Building

```powershell
# Requires the .NET 9 SDK on a real Windows machine (or for a cross-compile sanity check
# only, on macOS/Linux with the SDK installed — the output will not run there).
powershell -File scripts\build_helper_windows.ps1
```

Produces `native/windows/build/lmr-capture-helper.exe` plus its managed dependency DLLs
(NAudio, Vortice.*) in the same directory — `storage.paths.resolve_helper_binary()` looks
for the `.exe` there in dev mode, matching `native/macos/build/lmr-capture-helper`'s role
exactly.
