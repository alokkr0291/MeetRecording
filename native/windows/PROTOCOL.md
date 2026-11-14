# lmr-capture-helper protocol (Windows)

There is one protocol, not two. The authoritative reference is
[`../macos/PROTOCOL.md`](../macos/PROTOCOL.md) — this Windows helper implements it exactly
(same commands, same event shapes, same error codes, same JSON-lines transport), so that
`capture/protocols.py` and `capture/helper_client.py` need zero platform-specific code.

## Windows-specific notes (does not change the wire format)

- **Permissions are always reported `granted`.** Windows has no OS-level consent gate for
  an unpackaged desktop app calling DXGI Desktop Duplication or WASAPI, unlike macOS's TCC.
  `request-permissions` still exists as a command (for protocol symmetry) but is a no-op
  that reports the same unconditional `granted` status as `probe`. See `Permissions.cs`'s
  doc comment for the full reasoning and the one real gap this leaves (no mic/no audio
  device at all is still reported as a `start`-time `error`, not here).
- **`display_id`** is this helper process's own 0-based index into
  `System.Windows.Forms.Screen.AllScreens` for the duration of one invocation — not a
  persistent Windows display identifier. Stable within one helper lifetime (which only
  ever lives for one logical operation, same as macOS), not across relaunches or monitor
  reconfiguration.
- **Video codec is still H.264, audio is still AAC**, muxed into an MP4 container (`.mp4`
  by convention, though the protocol itself does not care about the file extension) —
  structurally the same "1 video + 2 audio tracks" shape as macOS's `.mov`, verified with
  `ffprobe` the same way (see `tests/capture/test_real_capture_windows.py`).

See `native/windows/README.md` for what is, and is not, actually verified about this
implementation — nothing here has run on real Windows hardware yet.
