"""Settings come from the environment with the project's own prefix."""
from __future__ import annotations

import pytest

from scrapewatch.config import Settings

pytestmark = pytest.mark.unit


def test_defaults_need_no_environment(monkeypatch) -> None:
    for key in ("SCRAPEWATCH_DB_URL", "SCRAPEWATCH_RECORD_VIDEO"):
        monkeypatch.delenv(key, raising=False)
    s = Settings()
    assert s.db_url.startswith("sqlite:///") and s.record_video is False


def test_environment_overrides_use_the_prefix(monkeypatch) -> None:
    monkeypatch.setenv("SCRAPEWATCH_RECORD_VIDEO", "true")
    monkeypatch.setenv("SCRAPEWATCH_MIN_REQUEST_INTERVAL_S", "2.5")
    s = Settings()
    assert s.record_video is True and s.min_request_interval_s == 2.5
