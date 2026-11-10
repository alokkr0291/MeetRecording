"""Shared fixtures. Tests never touch the real Application Support directory."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from local_meeting_recorder.config import Settings
from local_meeting_recorder.storage.paths import StoragePaths


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip LMR_* variables so the developer's shell cannot influence tests."""
    for key in list(os.environ):
        if key.startswith("LMR_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def paths(data_dir: Path) -> StoragePaths:
    storage = StoragePaths(root=data_dir)
    storage.ensure()
    return storage


@pytest.fixture
def settings(data_dir: Path) -> Iterator[Settings]:
    yield Settings(_env_file=None, data_dir=data_dir)  # type: ignore[call-arg]
