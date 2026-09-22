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
    """What one client has counted since it was built: a running total, not one run's.

    A single client serves every HTTP source in a run — the per-host clock and the
    robots cache only work if it does — so these numbers keep climbing from one
    source to the next. A source that wants to report *its own* traffic snapshots
    these counters before it starts and again when it finishes, and reports the
    difference; that is what `scrapewatch.sources.base.SourceStats` does, and why
    the page's "requests made" is the traffic this project actually caused rather
    than each source repeating the ones before it.
    """

    requests: int = 0
    retries: int = 0
    bytes: int = 0
    seconds: float = 0.0
    hosts: dict[str, int] = field(default_factory=dict)


@dataclass
class _RobotsState:
    """What we know about one origin's robots.txt.

    ``unreadable_reason`` is set only when the file's rules could not be determined at
    all (a 5xx that survived retries, or a transport failure) — never for an ordinary
    404, which just means there is no file and nothing is restricted.

    ``origin_answered`` separates the two cases that both end in a refusal, because
    they are not the same news: a 5xx is a server that answered and could not give us
    the rules, a transport failure is a host that said nothing at all. The policy is
    identical — a scraper that cannot read the rules does not assume there are none —
    but a reader deciding what to do about it needs to know which happened, so the
    sentence a source ends up printing says so (see
    ``scrapewatch.sources.base.fetch_refused``).
    """

    parser: robotparser.RobotFileParser | None
    unreadable_reason: str | None = None
    origin_answered: bool = True


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
        self._robots: dict[str, _RobotsState] = {}
        self.stats = FetchStats()
        #: Why the most recent `allowed()` call returned False because robots.txt could
        #: not be read — None when it was allowed, or refused by an actual Disallow rule.
        self.robots_refusal_reason: str | None = None
        #: Whether that refusal was a host that never answered at all, rather than one
        #: that answered and could not be read. False unless the last `allowed()` call
        #: hit a transport failure fetching robots.txt.
        self.robots_origin_unreachable: bool = False

    # -- politeness -------------------------------------------------------

    def _wait_for_host(self, host: str) -> None:
        last = self._last_request_at.get(host)
        if last is not None:
            gap = self._settings.min_request_interval_s - (time.monotonic() - last)
            if gap > 0:
                time.sleep(gap)
        self._last_request_at[host] = time.monotonic()

    def allowed(self, url: str) -> bool:
        """Whether ``url`` may be fetched under its origin's robots.txt.

        A site with no robots.txt (404, or any other 4xx) allows everything. A site
        whose robots.txt could not be read at all — a 5xx that survived retries, or a
        dropped connection — is treated as disallowed: a polite scraper that cannot
        read the rules does not guess that there are none. See `robots_refusal_reason`
        for why, in that case.
        """
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        state = self._robots[origin]
        if state.unreadable_reason is not None:
            self.robots_refusal_reason = state.unreadable_reason
            self.robots_origin_unreachable = not state.origin_answered
            return False
        self.robots_refusal_reason = None
        self.robots_origin_unreachable = False
        if state.parser is None:
            return True
        return state.parser.can_fetch(self._settings.user_agent, url)

    def _load_robots(self, origin: str) -> _RobotsState:
        # Goes through `get()`, not the raw transport: the robots.txt fetch is a
        # request like any other, so it waits its turn, counts against stats, and
        # gets the same retries on a server error.
        try:
            response = self.get(f"{origin}/robots.txt")
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if 400 <= status < 500:
                return _RobotsState(parser=None)  # no robots.txt: nothing restricts us
            return _RobotsState(
                parser=None,
                unreadable_reason=f"robots.txt at {origin} returned HTTP {status} after retries",
            )
        except httpx.TransportError as exc:
            return _RobotsState(
                parser=None,
                unreadable_reason=f"{origin} did not answer when asked for robots.txt: {exc}",
                origin_answered=False,
            )
        parser = robotparser.RobotFileParser()
        parser.parse(response.text.splitlines())
        return _RobotsState(parser=parser)

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
