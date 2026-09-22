"""A diff names what changed, field by field, and never invents a change."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scrapewatch.models import Record
from scrapewatch.pipeline.diff import diff

pytestmark = pytest.mark.unit


def _rec(eid: str, **fields) -> Record:
    return Record(
        source="demo",
        kind="product",
        external_id=eid,
        fetched_at=datetime.now(UTC),
        url=f"https://d/{eid}",
        fields=fields,
    )


def test_identical_snapshots_produce_no_changes() -> None:
    a = [_rec("1", price=Decimal("10"), in_stock=True)]
    cs = diff(a, [_rec("1", price=Decimal("10"), in_stock=True)])
    assert not cs.added and not cs.removed and not cs.changed


def test_a_changed_field_is_reported_with_before_and_after() -> None:
    cs = diff([_rec("1", price=Decimal("10"))], [_rec("1", price=Decimal("12"))])
    changes = [(c.external_id, c.field, c.before, c.after) for c in cs.changed]
    assert changes == [("1", "price", Decimal("10"), Decimal("12"))]


def test_added_and_removed_are_keyed_by_external_id() -> None:
    cs = diff([_rec("1", price=Decimal("1"))], [_rec("2", price=Decimal("1"))])
    assert [r.external_id for r in cs.added] == ["2"] and [r.external_id for r in cs.removed] == ["1"]


def test_fetched_at_is_not_a_change() -> None:
    cs = diff([_rec("1", price=Decimal("1"))], [_rec("1", price=Decimal("1"))])
    assert not cs.changed
