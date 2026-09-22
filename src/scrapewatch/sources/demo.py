"""The demo store's product catalogue, scraped over its JSON API.

`DemoSource` is the plain-HTTP shape `BooksSource` already set: check `allowed()`
first, walk pages with `PoliteClient.get()`, count what only the source can see. The
one difference is how it knows to stop — the demo store's `/api/products` never
404s past the last page, it just answers with an empty `items` list (see
`scrapewatch.demo_store.app`), so that emptiness, not a missing `next` link, is the
end-of-listing signal here.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from scrapewatch.http import PoliteClient
from scrapewatch.models import RawRecord
from scrapewatch.sources.base import SourceStats, fetch_refused


class DemoSource:
    """Walks the demo store's `/api/products` pages until one comes back with no items."""

    name = "demo"
    #: The door: the store ships in this repository and is served on loopback, so
    #: nothing here leaves the machine.
    kind = "local"

    def __init__(self, client: PoliteClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._source_stats = SourceStats()

    def fetch(self) -> Iterator[RawRecord]:
        """Walk `/api/products?page=n` from 1 until a page's `items` list is empty.

        Checks `allowed()` on the root URL first, the same contract every HTTP source
        here follows: a refusal becomes a `RuntimeError` naming the reason, which
        `run_sources` turns into a skipped source rather than a crash.
        """
        with self._source_stats.measuring(self._client.stats):
            root_url = f"{self._base_url}/"
            if not self._client.allowed(root_url):
                raise fetch_refused(self.name, self._client, root_url)

            page = 1
            pages_fetched = 0
            while True:
                response = self._client.get(f"{self._base_url}/api/products?page={page}")
                payload = response.json()
                items = payload["items"]
                if not items:
                    break

                pages_fetched += 1
                listing_url = f"{self._base_url}/?page={page}"
                for item in items:
                    self._source_stats.records += 1
                    yield RawRecord(
                        source=self.name,
                        external_id=str(item["id"]),
                        fetched_at=datetime.now(UTC),
                        fields={
                            "name": item["name"],
                            "price": item["price"],
                            "in_stock": item["in_stock"],
                            "stock": item["stock"],
                        },
                        url=listing_url,
                    )
                self._source_stats.pages = pages_fetched
                page += 1

    @property
    def stats(self) -> dict:
        """Pages and records this source counted, with its own share of the client's totals.

        Its own share, not the client's running totals: one client serves every
        source in a run, so the difference since this source started is the only
        honest answer to "what did collecting this cost".
        """
        return self._source_stats.as_dict(self._client.stats)
