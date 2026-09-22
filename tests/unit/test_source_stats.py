"""A source reports the traffic it caused, not the traffic the run has caused so far.

Every HTTP source in a run shares one `PoliteClient`, deliberately: the per-host
clock and the robots cache are worth nothing if each source brings its own. That
makes `client.stats` a running total across the whole run, and a source that merged
it wholesale would report every earlier source's requests as its own — which is
exactly what the published page did, announcing 161 requests for a night that made
58.

These run two real sources through one client, against a stub transport, and check
that each one's block of `run-stats.json` describes only itself.
"""
from __future__ import annotations

import time

import httpx
import pytest

from scrapewatch.config import Settings
from scrapewatch.http import PoliteClient
from scrapewatch.sources.books import BooksSource
from scrapewatch.sources.demo import DemoSource
from scrapewatch.sources.quotes import QuotesSource

pytestmark = pytest.mark.unit

BOOKS_URL = "https://books.test"
DEMO_URL = "https://demo.test"

#: One listing page with one book on it, and no next page: enough for the source to
#: walk the catalogue to its end in a single request.
_LISTING = """
<html><body>
  <article class="product_pod">
    <h3><a title="A Book" href="catalogue/a-book_1/index.html">A Book</a></h3>
    <p class="price_color">£10.00</p>
    <p class="instock availability">In stock</p>
    <p class="star-rating Three"></p>
  </article>
</body></html>
"""

_PRODUCTS = {
    "1": {"page": 1, "per_page": 2, "total": 2, "items": [
        {"id": 1, "name": "Widget", "price": "$1.00", "in_stock": True, "stock": 3},
        {"id": 2, "name": "Gadget", "price": "$2.00", "in_stock": False, "stock": 0},
    ]},
    "2": {"page": 2, "per_page": 2, "total": 2, "items": []},
}


def _handler(request: httpx.Request) -> httpx.Response:
    """Both sites, from one transport: a permissive robots.txt and one catalogue each."""
    if request.url.path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nAllow: /\n")
    if request.url.host == "books.test":
        return httpx.Response(200, text=_LISTING)
    if request.url.path == "/api/products":
        return httpx.Response(200, json=_PRODUCTS[request.url.params.get("page", "1")])
    return httpx.Response(404)


@pytest.fixture
def two_sources_on_one_client() -> tuple[dict, dict, PoliteClient]:
    """Books then demo, fetched in that order through a single client."""
    settings = Settings(min_request_interval_s=0.0)
    with PoliteClient(settings, transport=httpx.MockTransport(_handler)) as client:
        books = BooksSource(client, base_url=BOOKS_URL)
        demo = DemoSource(client, DEMO_URL)
        list(books.fetch())
        list(demo.fetch())
        return books.stats, demo.stats, client


def test_each_source_reports_only_the_requests_it_made(two_sources_on_one_client) -> None:
    """Books: its robots.txt and its one listing page. Demo: its robots.txt and two API pages."""
    books, demo, client = two_sources_on_one_client
    assert books["requests"] == 2
    assert demo["requests"] == 3
    assert client.stats.requests == 5, "the client still counts the whole run, as it should"


def test_the_second_source_does_not_inherit_the_first_ones_bytes(two_sources_on_one_client) -> None:
    """The bug this pins was additive: the second block carried the first's totals too."""
    books, demo, client = two_sources_on_one_client
    assert books["bytes"] > 0 and demo["bytes"] > 0
    assert books["bytes"] + demo["bytes"] == client.stats.bytes


def test_the_per_source_blocks_add_up_to_the_run(two_sources_on_one_client) -> None:
    """The page sums these blocks, so their sum has to be the traffic that happened."""
    books, demo, client = two_sources_on_one_client
    assert books["requests"] + demo["requests"] == client.stats.requests
    assert books["retries"] + demo["retries"] == client.stats.retries


def test_a_source_block_carries_no_per_host_breakdown(two_sources_on_one_client) -> None:
    """`hosts` is a dict, and every entry in `run-stats.json` has to read the same way
    whether the source ran or was skipped before it started — a skipped one is built
    from numbers alone. The client still keeps the breakdown for its own tests."""
    books, demo, client = two_sources_on_one_client
    assert "hosts" not in books and "hosts" not in demo
    assert client.stats.hosts == {"books.test": 2, "demo.test": 3}


def test_a_source_that_never_fetched_reports_nothing_rather_than_the_whole_run() -> None:
    """Reading `stats` off a source that never ran must not hand back the client's totals."""
    settings = Settings(min_request_interval_s=0.0)
    with PoliteClient(settings, transport=httpx.MockTransport(_handler)) as client:
        list(BooksSource(client, base_url=BOOKS_URL).fetch())
        never_ran = DemoSource(client, DEMO_URL)
        assert never_ran.stats["requests"] == 0 and never_ran.stats["bytes"] == 0
        assert client.stats.requests == 2


# -- the browser source's own two halves -----------------------------------------

_RENDERED = """
<html><body>
  <div class="quote"><span class="text">“Words.”</span><small class="author">Anon</small></div>
</body></html>
"""


class _StubSession:
    """A `BrowserSession` that renders a fixed page, and says how often it was asked to."""

    def __init__(self) -> None:
        self.renders = 0

    def render(self, url: str, wait_for: str) -> str:
        self.renders += 1
        time.sleep(0.01)  # a page load takes time; the point is that somebody counts it
        return _RENDERED


def test_the_browser_source_counts_its_page_loads_and_times_them() -> None:
    """A browser page load is not an HTTP request, so the client cannot see it at all.

    Left to the client alone this source would report "one request, no time" for a
    night spent driving Chromium: the one request is robots.txt. The renders are the
    work, so the source counts them as pages and adds their wall-clock time to its
    seconds.
    """
    settings = Settings(min_request_interval_s=0.0)
    session = _StubSession()
    with PoliteClient(settings, transport=httpx.MockTransport(_handler)) as client:
        source = QuotesSource(session, client, base_url="https://quotes.test")
        records = list(source.fetch())

    stats = source.stats
    assert len(records) == 1
    assert session.renders == 1
    assert stats["pages"] == 1
    assert stats["requests"] == 1, "robots.txt, and nothing else: the page loads were the browser's"
    assert stats["seconds"] >= 0.01, "the render is the work, and it is in the number"
