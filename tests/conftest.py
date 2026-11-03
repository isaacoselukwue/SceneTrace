from __future__ import annotations

import os
from pathlib import Path

import pytest

from scenetrace.config import ROOT, Settings
from scenetrace.db import Database


@pytest.fixture(autouse=True)
def _no_live_key(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("live") is None:
        monkeypatch.delenv("MODEL_API_KEY", raising=False)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.load(ROOT, env={
        "SCENETRACE_DATA_DIR": str(tmp_path / "data"),
        "SCENETRACE_MODE": "fixture",
        "MODEL_API_KEY": "",
        "SCENETRACE_DEMO_ACCESS_CODE": "",
    })


@pytest.fixture
def db(settings: Settings) -> Database:
    database = Database(settings.data_dir / "db" / "scenetrace.sqlite3")
    yield database
    database.close()


def live_enabled() -> bool:
    return os.environ.get("SCENETRACE_RUN_LIVE_TESTS", "").lower() in ("1", "true", "yes")
