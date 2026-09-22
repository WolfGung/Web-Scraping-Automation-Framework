"""What a source says when it is not allowed to fetch — and which of two things happened.

The sentence a refused source raises is not an internal detail: `run_sources` records
it as that source's `reason`, the CLI prints it, and the published page states it to a
reader who has to decide whether a site said no or the night simply failed to ask.

Two very different events end in the same refusal. A `Disallow`, or a `robots.txt`
that could not be read, is the site refusing. A host that never answered at all has
refused nothing — there is no file, and naming one sends a reader looking for a rule
that does not exist. The policy is identical either way; only the sentence differs.
"""
from __future__ import annotations

import httpx
import pytest

from scrapewatch.config import Settings
from scrapewatch.http import PoliteClient
from scrapewatch.sources.books import BooksSource

pytestmark = pytest.mark.unit

BOOKS_URL = "https://books.test"


def _books(handler) -> BooksSource:
    settings = Settings(min_request_interval_s=0.0, max_retries=1)
    return BooksSource(PoliteClient(settings, transport=httpx.MockTransport(handler)), base_url=BOOKS_URL)


def test_a_disallow_is_a_refusal_by_robots_txt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="User-agent: *\nDisallow: /\n")

    with pytest.raises(RuntimeError, match="books: fetch refused by robots.txt") as refusal:
        list(_books(handler).fetch())
    assert "disallows" in str(refusal.value)


def test_a_robots_txt_that_could_not_be_read_is_also_a_refusal(monkeypatch) -> None:
    """The server answered and could not tell us the rules. We do not guess there are none."""
    monkeypatch.setattr("scrapewatch.http.time.sleep", lambda s: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(RuntimeError, match="books: fetch refused by robots.txt") as refusal:
        list(_books(handler).fetch())
    assert "returned HTTP 503 after retries" in str(refusal.value)


def test_a_host_that_never_answered_says_so_instead_of_blaming_robots_txt(monkeypatch) -> None:
    """A dead origin has refused nothing — there is no robots.txt to have been refused by."""
    monkeypatch.setattr("scrapewatch.http.time.sleep", lambda s: None)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 111] Connection refused", request=request)

    with pytest.raises(RuntimeError) as refusal:
        list(_books(handler).fetch())
    message = str(refusal.value)
    assert "did not answer" in message
    assert "refused by robots.txt" not in message
    assert BOOKS_URL in message
