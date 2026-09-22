"""The `Source` protocol: what `run_sources` needs from anything it fetches.

A source has a name to key snapshots by, a `kind` saying which door it goes
through, a `fetch()` that yields raw records, and a `stats` dict describing the
fetch (pages, records, and its share of what the shared HTTP client counted).
`run_sources` needs nothing else, which is why adding a fourth source is one file.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

from scrapewatch.models import RawRecord

#: What the shared `PoliteClient` counts and a source may claim its share of.
#: `FetchStats.hosts` is deliberately not among them: it is a dict, and
#: `run-stats.json` is read by a page that needs every source's entry to have the
#: same keys whether the source ran or was skipped before it started (a skipped
#: entry is built from numbers alone — see `scrapewatch.cli._ZERO_SOURCE_STATS`).
#: The client keeps counting per host either way; it is simply not part of the
#: per-source block.
CLIENT_COUNTERS = ("requests", "retries", "bytes", "seconds")


@runtime_checkable
class Source(Protocol):
    """Anything `run_sources` can drive: fetch, and describe what fetching cost."""

    name: str
    #: Which door this source goes through — `http`, `browser` or `local`. Not what
    #: its records are (`Record.kind` says book, quote or product): two sources can
    #: collect the same kind of thing through different doors, and the door is the
    #: decision this project is making a point of.
    kind: str

    def fetch(self) -> Iterator[RawRecord]:
        """Yield one `RawRecord` per thing seen. May raise; the caller decides what that means."""
        ...

    @property
    def stats(self) -> dict:
        """Whatever the source knows about its own fetch: pages, records, and so on."""
        ...


def _client_counters(fetch_stats: Any) -> dict[str, float]:
    """`CLIENT_COUNTERS` read off a `FetchStats`-shaped object or a plain dict."""
    body = dict(fetch_stats) if isinstance(fetch_stats, dict) else asdict(fetch_stats)
    return {name: body.get(name, 0) for name in CLIENT_COUNTERS}


@dataclass
class SourceStats:
    """What a source knows about its own fetch, plus its share of the client's totals.

    One `PoliteClient` serves every HTTP source in a run, on purpose: the per-host
    clock and the robots cache are only worth anything if every source shares them.
    Its `FetchStats` is therefore a running total across the whole run, and a source
    that simply merged it would report the traffic of every source before it as its
    own — which is what the published page used to do, adding up to nearly three
    times the requests the run actually made.

    So a source wraps its fetch in `measuring()`, which notes where the client's
    counters stood on the way in and again on the way out, and `as_dict()` reports the
    difference: the requests, retries, bytes and seconds that happened while this
    source was the one fetching. Both ends matter — reading "the counters now" would
    make a finished source's numbers keep growing as the sources after it fetch — and
    a source that never fetched reports zeroes rather than the whole run's.
    """

    pages: int = 0
    records: int = 0
    #: Where the client's counters stood when this source started, and where they
    #: stood when it stopped. Neither is reported; the difference between them is.
    #: `None` means that end has not happened yet.
    baseline: dict[str, float] | None = field(default=None, repr=False)
    finished: dict[str, float] | None = field(default=None, repr=False)

    @contextmanager
    def measuring(self, fetch_stats: Any) -> Iterator[None]:
        """Bracket a fetch, so what the shared client counted during it can be told apart.

        Entered at the very top of `fetch()`, before the robots.txt check: that
        request is made because this source is about to fetch, so it is this source's
        cost. The closing snapshot is taken in a `finally`, which covers every way a
        fetch can end — running out of pages, raising, or being abandoned part-way by
        a caller that stopped iterating.
        """
        self.begin(fetch_stats)
        try:
            yield
        finally:
            self.finish(fetch_stats)

    def begin(self, fetch_stats: Any) -> None:
        """Note where the shared client's counters stood as this source started."""
        self.baseline = _client_counters(fetch_stats)
        self.finished = None

    def finish(self, fetch_stats: Any) -> None:
        """Note where they stood when it stopped, freezing this source's share."""
        self.finished = _client_counters(fetch_stats)

    def as_dict(self, fetch_stats: Any) -> dict:
        """Pages and records this source counted, with its own share of the client's totals."""
        merged: dict[str, Any] = {"pages": self.pages, "records": self.records}
        if self.baseline is None:
            # Never fetched: this source's share of the client's totals is nothing,
            # not all of it.
            merged.update(dict.fromkeys(CLIENT_COUNTERS, 0))
            return merged
        # Mid-fetch — `stats` read from inside the loop — has no closing snapshot yet,
        # so the counters as they stand are the best answer there is.
        now = self.finished if self.finished is not None else _client_counters(fetch_stats)
        merged.update({name: now[name] - self.baseline[name] for name in CLIENT_COUNTERS})
        return merged


def fetch_refused(name: str, client: Any, url: str) -> RuntimeError:
    """The exception a source raises when it may not fetch `url`, worded for the case.

    Every source asks `client.allowed()` before its first fetch and turns a `False`
    into this, which `run_sources` records as a skipped source with the message as
    its reason — so this sentence is what the CLI prints and what the published page
    states. Two different things end up here and they are not the same news:

    - the site answered and said no (a `Disallow`), or answered and could not tell us
      what the rules are (a 5xx that survived the retries): *refused by robots.txt*;
    - the host never answered at all: *did not answer*. Calling that "refused by
      robots.txt" names a file nobody ever read and sends a reader looking for a rule
      that does not exist, when what actually happened is that the origin was down or
      unreachable from here.

    The policy is the same either way — a scraper that cannot read the rules does not
    get to assume there are none — only the sentence differs.
    """
    reason = client.robots_refusal_reason or f"robots.txt disallows {url}"
    if getattr(client, "robots_origin_unreachable", False):
        return RuntimeError(f"{name}: fetch skipped: {reason}")
    return RuntimeError(f"{name}: fetch refused by robots.txt: {reason}")
