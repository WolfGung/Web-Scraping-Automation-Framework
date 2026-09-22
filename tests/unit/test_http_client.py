"""The client is polite by construction: it waits, it retries, it says who it is, it asks robots.txt."""
from __future__ import annotations

import httpx
import pytest

from scrapewatch.config import Settings
from scrapewatch.http import PoliteClient

pytestmark = pytest.mark.unit


def _client(handler, **overrides) -> PoliteClient:
    defaults = {"min_request_interval_s": 0.0, "max_retries": 2}
    defaults.update(overrides)
    settings = Settings(**defaults)
    return PoliteClient(settings, transport=httpx.MockTransport(handler))


def test_it_identifies_itself() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, text="ok")

    _client(handler).get("https://example.test/")
    assert "scrapewatch" in seen["ua"] and "github.com/WolfGung" in seen["ua"]


def test_it_retries_a_5xx_and_counts_the_retry() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 1 else httpx.Response(200, text="ok")

    client = _client(handler)
    assert client.get("https://example.test/").status_code == 200
    assert client.stats.requests == 2 and client.stats.retries == 1


def test_it_gives_up_after_max_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(httpx.HTTPStatusError):
        _client(handler).get("https://example.test/")


def test_it_does_not_retry_a_4xx() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    with pytest.raises(httpx.HTTPStatusError):
        _client(handler).get("https://example.test/missing")
    assert calls["n"] == 1


def test_it_waits_between_requests_to_the_same_host(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("scrapewatch.http.time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr("scrapewatch.http.time.monotonic", lambda: 100.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    client = _client(handler, min_request_interval_s=0.5)
    client.get("https://example.test/a")
    client.get("https://example.test/b")
    assert sleeps and abs(sleeps[0] - 0.5) < 1e-6


def test_it_honours_a_disallow_in_robots_txt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return httpx.Response(200, text="ok")

    client = _client(handler)
    assert client.allowed("https://example.test/public/") is True
    assert client.allowed("https://example.test/private/x") is False


def test_a_missing_robots_txt_allows_everything() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(200, text="ok")

    assert _client(handler).allowed("https://example.test/anything") is True
