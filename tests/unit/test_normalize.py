"""Normalisation turns what a page says into what a database can compare."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scrapewatch.models import RawRecord
from scrapewatch.pipeline.normalize import normalize

pytestmark = pytest.mark.unit


def _raw(**fields) -> RawRecord:
    return RawRecord(source="books", external_id="x1", fetched_at=datetime.now(UTC), url="https://b/x1", fields=fields)


def test_price_text_becomes_decimal_and_currency() -> None:
    rec = normalize(_raw(title="A", price="£51.77", availability="In stock (22 available)", rating="Three"))
    assert rec.fields["price"] == Decimal("51.77") and rec.fields["currency"] == "GBP"


def test_availability_text_becomes_a_flag() -> None:
    rec = normalize(_raw(title="A", price="£1.00", availability="In stock (22 available)", rating="One"))
    assert rec.fields["in_stock"] is True


def test_rating_words_become_integers() -> None:
    rec = normalize(_raw(title="A", price="£1.00", availability="In stock (1 available)", rating="Five"))
    assert rec.fields["rating"] == 5


def test_whitespace_and_odd_unicode_are_cleaned() -> None:
    rec = normalize(_raw(title="  A Title  ", price="£1.00", availability="In stock (1 available)", rating="One"))
    assert rec.fields["title"] == "A Title"


def test_a_record_that_cannot_be_normalised_says_which_field() -> None:
    with pytest.raises(ValueError, match="price"):
        normalize(_raw(title="A", price="free?", availability="In stock (1 available)", rating="One"))


def test_a_book_carries_no_stock_count_at_all() -> None:
    """Not even when the sentence happens to have a number in it.

    The listing pages this project walks say "In stock" and nothing more; only a
    book's own page ever says how many. A column that is a number on one page and
    null on the other thousand describes nothing, so books carry the flag alone —
    and the demo store, whose API hands back a real integer, keeps its count.
    """
    with_number = normalize(_raw(title="A", price="£1.00", availability="In stock (22 available)", rating="One"))
    without = normalize(_raw(title="A", price="£1.00", availability="In stock", rating="One"))
    assert "stock" not in with_number.fields and "stock" not in without.fields
    assert with_number.fields["in_stock"] is True and without.fields["in_stock"] is True


def test_out_of_stock_is_read_as_out_of_stock() -> None:
    rec = normalize(_raw(title="A", price="£1.00", availability="Out of stock", rating="One"))
    assert rec.fields["in_stock"] is False


def test_a_book_has_no_category_until_its_own_page_was_read() -> None:
    """`category` is the one field the detail pages add, so it is the one optional field."""
    listing = normalize(_raw(title="A", price="£1.00", availability="In stock", rating="One"))
    detailed = normalize(
        _raw(title="A", price="£1.00", availability="In stock (22 available)", rating="One", category="Poetry")
    )
    assert "category" not in listing.fields
    assert detailed.fields["category"] == "Poetry"


def test_the_demo_store_keeps_its_real_count() -> None:
    """Its API states a number for every product, so there is nothing to be unknown."""
    raw = RawRecord(
        source="demo",
        external_id="7",
        fetched_at=datetime.now(UTC),
        url="https://d/7",
        fields={"name": "Widget", "price": "$1.00", "in_stock": True, "stock": 3},
    )
    assert normalize(raw).fields["stock"] == 3


def test_a_blank_book_title_is_rejected() -> None:
    with pytest.raises(ValueError, match="title"):
        normalize(_raw(title="   ", price="£1.00", availability="In stock (1 available)", rating="One"))


def test_a_blank_quote_text_is_rejected() -> None:
    raw = RawRecord(
        source="quotes",
        external_id="q1",
        fetched_at=datetime.now(UTC),
        url="https://q/q1",
        fields={"text": "   ", "author": "Anon", "tags": ["life"]},
    )
    with pytest.raises(ValueError, match="text"):
        normalize(raw)
