"""The book catalogue at books.toscrape.com, scraped over plain HTTP.

A static site needs no browser, and choosing not to start one is an engineering
decision, not a shortcut: `BooksSource` walks the listing pages with `PoliteClient`
alone. Parsing lives in two pure functions (`parse_listing`, `parse_detail`) so the
gate can prove them against pages saved from the real site, with no network and no
flakiness from the site changing under a running test.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from scrapewatch.http import PoliteClient
from scrapewatch.models import RawRecord
from scrapewatch.sources.base import SourceStats, fetch_refused

DEFAULT_BASE_URL = "https://books.toscrape.com"


def _slug_from_href(href: str) -> str:
    """The stable identifier books.toscrape.com already gives every book.

    `catalogue/a-light-in-the-attic_1000/index.html` -> `a-light-in-the-attic_1000`:
    the same slug on every listing page that mentions the book and on every run,
    which is exactly what `external_id` needs to be.
    """
    parts = [p for p in href.split("/") if p]
    return parts[-2] if parts and parts[-1] == "index.html" and len(parts) > 1 else parts[-1]


def _resolve_catalogue_path(href: str) -> str:
    """Normalise a link seen on either the front page or a page already under `catalogue/`.

    The front page links to books and to page 2 with paths already rooted at
    `catalogue/` (e.g. `catalogue/page-2.html`); pages already inside `catalogue/`
    link to their neighbours with bare filenames (e.g. `page-3.html`). Both spellings
    mean the same path relative to the site root, so both are returned as one.
    """
    if href.startswith("catalogue/"):
        return href
    return f"catalogue/{href}"


def parse_listing(html: str) -> tuple[list[dict], str | None]:
    """Parse one listing page into its book records and the path to the next page.

    Each record carries exactly what `normalize()`'s `books` branch needs (`title`,
    `price`, `availability`, `rating`) plus `external_id` and `url` — no `category`,
    since the listing page never shows one; that only shows up on a detail page.
    `url` and the next-page path are both paths relative to the site root (see
    `_resolve_catalogue_path`); `BooksSource` is the one that knows the base URL and
    turns them into absolute URLs.

    Raises `ValueError` naming what's missing: `article.product_pod not found` when
    the page has no book at all, or, for a pod that has some fields but not all,
    the missing field named together with the pod's title when one could be read.
    """
    tree = HTMLParser(html)
    pods = tree.css("article.product_pod")
    if not pods:
        raise ValueError("article.product_pod not found")

    records: list[dict] = []
    for pod in pods:
        title_node = pod.css_first("h3 a")
        title = title_node.attributes.get("title") if title_node else None
        href = title_node.attributes.get("href") if title_node else None
        label = title or href or "<unknown book>"

        if not href:
            raise ValueError(f"h3 a[href]: missing (pod {label!r})")
        if not title:
            raise ValueError(f"h3 a[title]: missing (pod {label!r})")

        price_node = pod.css_first(".price_color")
        if price_node is None:
            raise ValueError(f".price_color: missing (pod {label!r})")

        availability_node = pod.css_first(".instock.availability")
        if availability_node is None:
            raise ValueError(f".instock.availability: missing (pod {label!r})")

        rating_node = pod.css_first("p.star-rating")
        if rating_node is None:
            raise ValueError(f"p.star-rating: missing (pod {label!r})")
        rating_classes = rating_node.attributes.get("class") or ""
        rating_words = [c for c in rating_classes.split() if c != "star-rating"]
        if not rating_words:
            raise ValueError(f"star-rating class: missing rating word (pod {label!r})")

        url_path = _resolve_catalogue_path(href)
        records.append(
            {
                "external_id": _slug_from_href(href),
                "title": title,
                "price": price_node.text(strip=True),
                "availability": availability_node.text(strip=True),
                "rating": rating_words[0],
                "url": url_path,
            }
        )

    next_node = tree.css_first("li.next a")
    next_href = next_node.attributes.get("href") if next_node else None
    next_path = _resolve_catalogue_path(next_href) if next_href else None
    return records, next_path


def parse_detail(html: str) -> dict:
    """Parse a detail page for the fields only it carries: category and description.

    `category` comes from the breadcrumb's third item (`Home / Books / <category> /
    <title>`); `availability` is read again here because the detail page states it
    more authoritatively than the listing, and `normalize()` turns it into the same
    `in_stock` flag either way — a stock *count* is not something this project
    records for books (see `_parse_availability`).
    """
    tree = HTMLParser(html)
    crumbs = tree.css("ul.breadcrumb li")
    if len(crumbs) < 3:
        raise ValueError("ul.breadcrumb li: expected at least 3 items (Home / Books / category)")
    category = crumbs[2].text(strip=True)

    availability_node = tree.css_first(".instock.availability")
    if availability_node is None:
        raise ValueError(".instock.availability: missing")

    detail: dict = {
        "category": category,
        "availability": availability_node.text(strip=True),
    }

    description_node = tree.css_first("#product_description ~ p")
    if description_node is not None:
        detail["description"] = description_node.text(strip=True)

    return detail


class BooksSource:
    """Walks the book catalogue's listing pages, one `PoliteClient.get()` at a time."""

    name = "books"
    #: The door, not the record type: this catalogue is read over plain HTTP.
    kind = "http"

    def __init__(
        self,
        client: PoliteClient,
        base_url: str = DEFAULT_BASE_URL,
        max_pages: int | None = None,
        with_details: bool = False,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._max_pages = max_pages
        self._with_details = with_details
        self._source_stats = SourceStats()

    def fetch(self) -> Iterator[RawRecord]:
        """Walk pages from `/` following `next` until none remain or `max_pages` is reached.

        Checks `allowed()` on the base URL first and raises a clear error naming the
        refusal reason when robots.txt forbids it or could not be read — `run_sources`
        turns that exception into a skipped source with this message. With
        `with_details=False` (the default) a record carries only what the listing
        page shows — title, price, availability text, rating — and no `category`;
        the detail page costs one more request per book, which is the caller's
        choice, not the default.
        """
        with self._source_stats.measuring(self._client.stats):
            root_url = f"{self._base_url}/"
            if not self._client.allowed(root_url):
                raise fetch_refused(self.name, self._client, root_url)

            path: str | None = None  # None means the front page itself
            pages_fetched = 0
            while True:
                url = root_url if path is None else urljoin(root_url, path)
                response = self._client.get(url)
                pages_fetched += 1
                records, next_path = parse_listing(response.text)

                for record in records:
                    fields = {
                        "title": record["title"],
                        "price": record["price"],
                        "availability": record["availability"],
                        "rating": record["rating"],
                    }
                    absolute_url = urljoin(root_url, record["url"])
                    if self._with_details:
                        detail_response = self._client.get(absolute_url)
                        detail = parse_detail(detail_response.text)
                        fields["category"] = detail["category"]
                        fields["availability"] = detail["availability"]
                    self._source_stats.records += 1
                    yield RawRecord(
                        source=self.name,
                        external_id=record["external_id"],
                        fetched_at=datetime.now(UTC),
                        fields=fields,
                        url=absolute_url,
                    )

                self._source_stats.pages = pages_fetched
                if next_path is None:
                    break
                if self._max_pages is not None and pages_fetched >= self._max_pages:
                    break
                path = next_path

    @property
    def stats(self) -> dict:
        """Pages and records this source counted, with its own share of the client's totals.

        Its own share, not the client's running totals: one client serves every
        source in a run, so the difference since this source started is the only
        honest answer to "what did collecting this cost".
        """
        return self._source_stats.as_dict(self._client.stats)
