"""The `Source` protocol: what `run_sources` needs from anything it fetches.

This task only needs the shape, so `run_sources` and its tests have something
concrete to depend on: a name to key snapshots by, a kind for the stats block,
a `fetch()` that yields raw records, and a `stats` dict describing the fetch
(pages, requests, whatever the source knows). A later task fills in a real
source against this exact protocol rather than inventing its own.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass
from typing import Any, Protocol, runtime_checkable

from scrapewatch.models import RawRecord


@runtime_checkable
class Source(Protocol):
    """Anything `run_sources` can drive: fetch, and describe what fetching cost."""

    name: str
    kind: str

    def fetch(self) -> Iterator[RawRecord]:
        """Yield one `RawRecord` per thing seen. May raise; the caller decides what that means."""
        ...

    @property
    def stats(self) -> dict:
        """Whatever the source knows about its own fetch: pages, records, and so on."""
        ...


@dataclass
class SourceStats:
    """What an HTTP-backed source knows about its own fetch, beyond the client's own numbers.

    A source counts what only it can see — pages walked, records yielded — and merges
    in whatever the `PoliteClient` it used already counted (requests, retries, bytes,
    seconds), so `run_sources` gets one flat dict with every key it expects.
    """

    pages: int = 0
    records: int = 0

    def as_dict(self, fetch_stats: Any) -> dict:
        """Merge these counts with a `FetchStats`-shaped object's fields into one dict."""
        merged = asdict(self)
        merged.update(asdict(fetch_stats) if not isinstance(fetch_stats, dict) else dict(fetch_stats))
        return merged
