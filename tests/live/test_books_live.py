"""Against the real practice site. Opt-in: pytest -m live."""
from __future__ import annotations

import pytest

from scrapewatch.config import Settings
from scrapewatch.http import PoliteClient
from scrapewatch.sources.books import BooksSource

pytestmark = pytest.mark.live


def test_the_first_two_pages_yield_forty_books_politely() -> None:
    with PoliteClient(Settings()) as client:
        records = list(BooksSource(client, max_pages=2).fetch())
    assert len(records) == 40 and client.stats.requests >= 2
