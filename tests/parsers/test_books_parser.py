"""The parser is exercised on pages saved from the real site, so the gate needs no network."""
from __future__ import annotations

from pathlib import Path

import pytest

from scrapewatch.sources.books import parse_detail, parse_listing

pytestmark = pytest.mark.parsers
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "books"


def test_a_listing_page_yields_twenty_books_and_the_next_page() -> None:
    records, next_path = parse_listing((FIXTURES / "index.html").read_text(encoding="utf-8"))
    assert len(records) == 20 and next_path == "catalogue/page-2.html"


def test_each_listing_record_carries_the_fields_normalisation_needs() -> None:
    records, _ = parse_listing((FIXTURES / "index.html").read_text(encoding="utf-8"))
    first = records[0]
    assert set(first) >= {"external_id", "title", "price", "availability", "rating", "url"}
    assert first["price"].startswith("£") and first["rating"] in {"One", "Two", "Three", "Four", "Five"}


def test_the_last_page_has_no_next_link() -> None:
    html = (FIXTURES / "page-2.html").read_text(encoding="utf-8").replace('class="next"', 'class="gone"')
    _, next_path = parse_listing(html)
    assert next_path is None


def test_a_detail_page_adds_category_and_stock_count() -> None:
    detail = parse_detail((FIXTURES / "detail.html").read_text(encoding="utf-8"))
    assert detail["category"] == "Poetry" and "available" in detail["availability"]


def test_a_page_without_the_expected_markup_names_what_is_missing() -> None:
    with pytest.raises(ValueError, match="article.product_pod"):
        parse_listing("<html><body><p>nothing here</p></body></html>")
