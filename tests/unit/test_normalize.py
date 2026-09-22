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


def test_availability_text_becomes_a_flag_and_a_count() -> None:
    rec = normalize(_raw(title="A", price="£1.00", availability="In stock (22 available)", rating="One"))
    assert rec.fields["in_stock"] is True and rec.fields["stock"] == 22


def test_rating_words_become_integers() -> None:
    rec = normalize(_raw(title="A", price="£1.00", availability="In stock (1 available)", rating="Five"))
    assert rec.fields["rating"] == 5


def test_whitespace_and_odd_unicode_are_cleaned() -> None:
    rec = normalize(_raw(title="  A Title  ", price="£1.00", availability="In stock (1 available)", rating="One"))
    assert rec.fields["title"] == "A Title"


def test_a_record_that_cannot_be_normalised_says_which_field() -> None:
    with pytest.raises(ValueError, match="price"):
        normalize(_raw(title="A", price="free?", availability="In stock (1 available)", rating="One"))


def test_in_stock_with_no_count_leaves_the_count_unknown() -> None:
    rec = normalize(_raw(title="A", price="£1.00", availability="In stock", rating="One"))
    assert rec.fields["in_stock"] is True and rec.fields["stock"] is None


def test_in_stock_with_a_count_still_reads_the_number() -> None:
    rec = normalize(_raw(title="A", price="£1.00", availability="In stock (22 available)", rating="One"))
    assert rec.fields["in_stock"] is True and rec.fields["stock"] == 22


def test_out_of_stock_also_leaves_the_count_unknown() -> None:
    rec = normalize(_raw(title="A", price="£1.00", availability="Out of stock", rating="One"))
    assert rec.fields["in_stock"] is False and rec.fields["stock"] is None


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
