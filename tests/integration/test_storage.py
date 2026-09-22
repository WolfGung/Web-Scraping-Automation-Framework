"""Snapshots survive a process, diffs compare the right two, exports are the same data."""
from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from scrapewatch.models import Record
from scrapewatch.pipeline.diff import diff
from scrapewatch.storage import Storage

pytestmark = pytest.mark.integration


def _rec(eid: str, **fields) -> Record:
    return Record(
        source="demo", kind="product", external_id=eid, fetched_at=datetime.now(UTC),
        url=f"https://d/{eid}", fields=fields,
    )


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    s.open()
    return s


def test_two_runs_give_two_snapshots_and_the_diff_sees_the_change(storage: Storage) -> None:
    r1 = storage.start_run(["demo"])
    storage.save_snapshot(r1, "demo", [_rec("1", price=Decimal("10"))])
    storage.finish_run(r1, {})
    r2 = storage.start_run(["demo"])
    storage.save_snapshot(r2, "demo", [_rec("1", price=Decimal("12"))])
    storage.finish_run(r2, {})

    latest, previous = storage.latest_snapshots("demo", n=2)
    cs = diff(previous, latest)
    assert [(c.field, c.before, c.after) for c in cs.changed] == [("price", Decimal("10"), Decimal("12"))]


def test_a_first_run_has_nothing_to_compare_against(storage: Storage) -> None:
    r1 = storage.start_run(["demo"])
    storage.save_snapshot(r1, "demo", [_rec("1", price=Decimal("10"))])
    assert len(storage.latest_snapshots("demo", n=2)) == 1


def test_export_csv_and_json_carry_the_same_rows(storage: Storage, tmp_path: Path) -> None:
    r1 = storage.start_run(["demo"])
    storage.save_snapshot(r1, "demo", [_rec("1", name="Widget", price=Decimal("10"))])
    storage.export("demo", "csv", tmp_path / "demo.csv")
    storage.export("demo", "json", tmp_path / "demo.json")
    rows = list(csv.DictReader((tmp_path / "demo.csv").open()))
    payload = json.loads((tmp_path / "demo.json").read_text())
    assert rows[0]["name"] == "Widget" and payload[0]["name"] == "Widget" and payload[0]["price"] == "10"


def test_decimal_and_datetime_round_trip_through_the_database(storage: Storage) -> None:
    r1 = storage.start_run(["demo"])
    storage.save_snapshot(r1, "demo", [_rec("1", price=Decimal("51.77"))])
    (latest,) = storage.latest_snapshots("demo", n=1)
    assert latest[0].fields["price"] == Decimal("51.77") and latest[0].fetched_at.tzinfo is not None


def test_an_unknown_stock_count_round_trips_as_none_not_zero(storage: Storage) -> None:
    """`stock=None` means "the page didn't say" — the database must not turn that into 0."""
    r1 = storage.start_run(["demo"])
    storage.save_snapshot(r1, "demo", [_rec("1", stock=None)])
    (latest,) = storage.latest_snapshots("demo", n=1)
    assert latest[0].fields["stock"] is None


def test_export_writes_an_empty_cell_for_a_none_field(storage: Storage, tmp_path: Path) -> None:
    r1 = storage.start_run(["demo"])
    storage.save_snapshot(r1, "demo", [_rec("1", name="Widget", stock=None)])
    storage.export("demo", "csv", tmp_path / "demo.csv")
    storage.export("demo", "json", tmp_path / "demo.json")
    rows = list(csv.DictReader((tmp_path / "demo.csv").open()))
    payload = json.loads((tmp_path / "demo.json").read_text())
    assert rows[0]["stock"] == "" and payload[0]["stock"] is None
