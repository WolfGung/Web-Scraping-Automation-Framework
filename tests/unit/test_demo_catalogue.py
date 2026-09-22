"""The demo store changes by date on purpose, and the same date always gives the same store."""
from __future__ import annotations

from datetime import date

import pytest

from scrapewatch.demo_store.catalogue import catalogue_for

pytestmark = pytest.mark.unit


def test_the_same_day_gives_the_same_catalogue() -> None:
    assert catalogue_for(date(2026, 9, 22)) == catalogue_for(date(2026, 9, 22))


def test_consecutive_days_differ_in_a_few_prices_and_stock_flags_not_everything() -> None:
    a = {p["id"]: p for p in catalogue_for(date(2026, 9, 22))}
    b = {p["id"]: p for p in catalogue_for(date(2026, 9, 23))}
    assert a.keys() == b.keys()
    changed = [k for k in a if a[k]["price"] != b[k]["price"] or a[k]["in_stock"] != b[k]["in_stock"]]
    assert 3 <= len(changed) <= 12, f"{len(changed)} of {len(a)} changed — the demo should move a little, not churn"
