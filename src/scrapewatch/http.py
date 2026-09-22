"""One HTTP client for every source that goes over HTTP.

Politeness lives here so that no source can forget it: a minimum interval per host,
retries with exponential backoff on server errors and dropped connections, an honest
User-Agent, and a robots.txt check that honours what it finds.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib import robotparser
from urllib.parse import urlsplit

import httpx

from scrapewatch.config import Settings

RETRIABLE = {500, 502, 503, 504}


@dataclass
class FetchStats:
    requests: int = 0
    retries: int = 0
    bytes: int = 0
    seconds: float = 0.0
    hosts: dict[str, int] = field(default_factory=dict)


class PoliteClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings
        self._http = httpx.Client(
            headers={"User-Agent": settings.user_agent},
            timeout=settings.request_timeout_s,
            follow_redirects=True,
            transport=transport,
        )
        self._last_request_at: dict[str, float] = {}
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self.stats = FetchStats()

    # -- politeness -------------------------------------------------------

    def _wait_for_host(self, host: str) -> None:
        last = self._last_request_at.get(host)
        if last is not None:
            gap = self._settings.min_request_interval_s - (time.monotonic() - last)
            if gap > 0:
                time.sleep(gap)
        self._last_request_at[host] = time.monotonic()

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        parser = self._robots[origin]
        return True if parser is None else parser.can_fetch(self._settings.user_agent, url)

    def _load_robots(self, origin: str) -> robotparser.RobotFileParser | None:
        try:
            response = self._http.get(f"{origin}/robots.txt")
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None  # no robots.txt means no restrictions, which is what the practice sites have
        parser = robotparser.RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser

    # -- fetching ---------------------------------------------------------

    def get(self, url: str) -> httpx.Response:
        """Fetch ``url``, retrying a transport failure or a 5xx up to ``max_retries`` times.

        One rule: on a dropped connection or a retriable status, back off and try again
        while attempts remain; once they run out, raise whichever failure happened last
        (the transport error itself, or the retriable status via ``raise_for_status``).
        """
        host = urlsplit(url).netloc
        attempt = 0
        while True:
            self._wait_for_host(host)
            started = time.monotonic()
            self.stats.requests += 1
            self.stats.hosts[host] = self.stats.hosts.get(host, 0) + 1
            try:
                response = self._http.get(url)
            except httpx.TransportError:
                if attempt >= self._settings.max_retries:
                    raise
                attempt += 1
                self.stats.retries += 1
                time.sleep(min(2**attempt * 0.25, 5.0))
                continue

            self.stats.seconds += time.monotonic() - started
            self.stats.bytes += len(response.content)

            if response.status_code in RETRIABLE and attempt < self._settings.max_retries:
                attempt += 1
                self.stats.retries += 1
                time.sleep(min(2**attempt * 0.25, 5.0))
                continue

            response.raise_for_status()
            return response

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
