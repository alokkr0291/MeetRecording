---
name: lmr-testing
description: Test layout, gating of hardware tests, fake helper usage, and the honesty rules for reporting results in LocalMeetingRecorder.
---

# Testing guidance

## Layout

- `tests/unit/` — no subprocesses except the fake helper script, no network, no Qt event loop beyond `QCoreApplication` where unavoidable. Must run in < 30 s.
- `tests/integration/` — may call ffmpeg/ffprobe and write to `tmp_path`.
- `tests/capture/` — real capture. Gated: skip unless `LMR_HW_TESTS=1`, helper built, and permissions granted. Each skip has an explicit reason string.
- `tests/packaging/` — gated by `LMR_PACKAGING_TESTS=1`.

## Fakes

- `capture/fake.py::FakeCaptureBackend` — in-process, scriptable (`fail_on_start`, `start_delay_s`, `crash_after_s`). Used by coordinator tests.
- `tests/unit/capture/fake_helper.py` — a Python script that speaks the helper JSON protocol. `HelperClient` tests launch it with `sys.executable` to test parsing, timeouts, crash detection, and SIGTERM handling without ScreenCaptureKit.

## Rules

- A test that cannot run must `pytest.skip` with the reason, never `pass`.
- Report results as: automated passed/failed counts, hardware tests run or skipped with reason, manual checklist items done.
- When a hardware test fails, keep the failing artifact path in the report and in `TESTING.md` results log.
- Use `pytest-timeout` (`timeout = 60` default) so a hung subprocess fails instead of blocking the suite.

## Commands

```bash
bash scripts/test.sh
pytest tests/unit -q
pytest tests/integration -q
LMR_HW_TESTS=1 pytest tests/capture -q -s
```
