"""quotes.toscrape.com's `/js/` page, scraped through a browser.

The site's plain HTML page (`/`) is easy — it needs no browser at all, `BooksSource`
already proved that shape works. `/js/` is the deliberately awkward twin: it ships its
quotes as a JSON array inside a `<script>` block and renders them into `div.quote`
markup with client-side JavaScript, so anything that only speaks HTTP sees an empty
page. `parse_quotes` is proof of exactly that (see `tests/parsers/test_quotes_parser.py`):
run it on the raw fetched page and it finds nothing; run it on what a browser renders
and it finds every quote. `QuotesSource` is the shape `BooksSource` set, with a
`BrowserSession` standing in for `PoliteClient.get()` as the way pages are fetched.
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from scrapewatch.browser import BrowserSession
from scrapewatch.http import PoliteClient
from scrapewatch.models import RawRecord
from scrapewatch.sources.base import SourceStats, fetch_refused

DEFAULT_BASE_URL = "https://quotes.toscrape.com"
#: Typographic quotes the site wraps every `text` in — on the rendered page and in the
#: JSON API alike — and that neither `RawRecord.fields["text"]` nor a diff should carry.
_CURLY_QUOTES = "“”"


def _clean_quote_text(text: str) -> str:
    return text.strip().strip(_CURLY_QUOTES).strip()


def parse_quotes(html: str) -> list[dict]:
    """Parse every `div.quote` in already-rendered HTML into `{text, author, tags}`.

    Returns `[]` on a page with no `div.quote` at all — the raw `/js/` page, before a
    browser has run its script, is exactly that page, and proving this function
    returns nothing on it is the whole argument for `BrowserSession` existing.
    """
    tree = HTMLParser(html)
    quotes: list[dict] = []
    for node in tree.css("div.quote"):
        text_node = node.css_first("span.text")
        author_node = node.css_first("small.author")
        tags = [tag.text(strip=True) for tag in node.css("a.tag")]
        quotes.append(
            {
                "text": _clean_quote_text(text_node.text(strip=True)) if text_node else "",
                "author": author_node.text(strip=True) if author_node else "",
                "tags": tags,
            }
        )
    return quotes


class QuotesSource:
    """Walks quotes.toscrape.com's `/js/` pages through a `BrowserSession`.

    `session` renders the pages `client` alone cannot read; `client` still does the
    robots.txt check and the one plain HTTP call `cross_check()` needs. A browser page
    load is not a `PoliteClient` request — it shares no rate limit or retry budget
    with the HTTP sources — so the two are counted in different places and reported
    together: `requests` stays what the client saw (robots.txt, and the API call when
    `cross_check()` ran), while `pages` counts the pages Chromium actually rendered
    and `seconds` includes how long those renders took.

    Without that last part this source reports "one request, a tenth of a second" for
    a night in which it drove a browser through ten page loads, which is the opposite
    of what the run cost. The client cannot time a render — it never sees one — so the
    source times it.
    """

    name = "quotes"
    #: The door, not the record type: this page is only readable once a browser has
    #: run its script.
    kind = "browser"

    def __init__(
        self,
        session: BrowserSession,
        client: PoliteClient,
        base_url: str = DEFAULT_BASE_URL,
        max_pages: int | None = None,
    ) -> None:
        self._session = session
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._max_pages = max_pages
        self._source_stats = SourceStats()
        #: Wall-clock seconds spent inside `BrowserSession.render`, which nothing else
        #: counts: the polite client never sees a browser page load.
        self._render_seconds = 0.0

    def fetch(self) -> Iterator[RawRecord]:
        """Render `/js/` and follow `li.next a[href]` until none remain or `max_pages` is hit.

        Checks `allowed()` on the base URL first, the same contract every source here
        follows: a refusal becomes a `RuntimeError` naming the reason, which
        `run_sources` turns into a skipped source rather than a crash.
        """
        with self._source_stats.measuring(self._client.stats):
            root_url = f"{self._base_url}/"
            if not self._client.allowed(root_url):
                raise fetch_refused(self.name, self._client, root_url)

            page_url = f"{self._base_url}/js/"
            pages_fetched = 0
            while True:
                html = self._render(page_url)
                pages_fetched += 1
                for quote in parse_quotes(html):
                    external_id = hashlib.sha1((quote["text"] + quote["author"]).encode()).hexdigest()[:16]
                    self._source_stats.records += 1
                    yield RawRecord(
                        source=self.name,
                        external_id=external_id,
                        fetched_at=datetime.now(UTC),
                        fields={"text": quote["text"], "author": quote["author"], "tags": quote["tags"]},
                        url=page_url,
                    )
                self._source_stats.pages = pages_fetched

                next_node = HTMLParser(html).css_first("li.next a[href]")
                if next_node is None:
                    break
                if self._max_pages is not None and pages_fetched >= self._max_pages:
                    break
                page_url = urljoin(page_url, next_node.attributes["href"])

    def _render(self, url: str) -> str:
        """`BrowserSession.render`, with the wall-clock time it took added to `seconds`.

        Timed in a `finally` so a render that raised still costs what it cost: a page
        load that timed out is exactly the one whose duration a reader wants to see.
        """
        started = time.monotonic()
        try:
            return self._session.render(url, wait_for=".quote")
        finally:
            self._render_seconds += time.monotonic() - started

    @property
    def stats(self) -> dict:
        """This source's own pages, records, HTTP traffic and time.

        `seconds` is the client's share plus the browser renders: the two halves of
        what collecting this source actually took, in one number, since the page
        states one duration per source.
        """
        stats = self._source_stats.as_dict(self._client.stats)
        stats["seconds"] = stats["seconds"] + self._render_seconds
        return stats

    def cross_check(self) -> int:
        """Compare the rendered `/js/` page's first ten quotes against the JSON API's.

        Renders the first page only, fetches `/api/quotes?page=1` through `client`, and
        compares `text`/`author` pairwise (the API's `text` carries the same curly
        quotes the rendered page does, stripped the same way). Returns how many of the
        first ten agreed — a live check that the browser-rendered markup and the site's
        own API describe the same quotes, not a fetch loop of its own.
        """
        html = self._render(f"{self._base_url}/js/")
        rendered = parse_quotes(html)[:10]

        response = self._client.get(f"{self._base_url}/api/quotes?page=1")
        api_quotes = response.json()["quotes"][:10]

        matched = 0
        for rendered_quote, api_quote in zip(rendered, api_quotes, strict=False):
            api_text = _clean_quote_text(api_quote["text"])
            api_author = api_quote["author"]["name"]
            if rendered_quote["text"] == api_text and rendered_quote["author"] == api_author:
                matched += 1
        return matched
