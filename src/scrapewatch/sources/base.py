"""The `Source` protocol: what `run_sources` needs from anything it fetches.

This task only needs the shape, so `run_sources` and its tests have something
concrete to depend on: a name to key snapshots by, a kind for the stats block,
a `fetch()` that yields raw records, and a `stats` dict describing the fetch
(pages, requests, whatever the source knows). A later task fills in a real
source against this exact protocol rather than inventing its own.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

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
