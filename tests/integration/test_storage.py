"""Snapshots survive a process, diffs compare the right two, exports are the same data."""
from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from scrapewatch.models import Record
from scrapewatch.pipeline.diff import diff
from scrapewatch.storage import ChangeRow, Storage

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


def test_a_decimal_inside_a_list_round_trips_through_the_database(storage: Storage) -> None:
    """Finding 1: the tagged-Decimal round trip must reach through a list, not just top-level fields."""
    r1 = storage.start_run(["demo"])
    storage.save_snapshot(r1, "demo", [_rec("1", prices=[Decimal("1.50"), Decimal("2.75")])])
    (latest,) = storage.latest_snapshots("demo", n=1)
    assert latest[0].fields["prices"] == [Decimal("1.50"), Decimal("2.75")]


def test_a_decimal_inside_a_nested_dict_round_trips_through_the_database(storage: Storage) -> None:
    """Finding 1: and through a nested dict, alongside an ordinary string in the same dict."""
    r1 = storage.start_run(["demo"])
    storage.save_snapshot(r1, "demo", [_rec("1", meta={"price": Decimal("2.50"), "note": "sale"})])
    (latest,) = storage.latest_snapshots("demo", n=1)
    assert latest[0].fields["meta"] == {"price": Decimal("2.50"), "note": "sale"}


def test_a_field_colliding_with_the_reserved_decimal_tag_is_refused_loudly(storage: Storage) -> None:
    """Finding 1: a raw dict that already looks like a tagged decimal must not be silently misread."""
    r1 = storage.start_run(["demo"])
    with pytest.raises(ValueError, match="weird"):
        storage.save_snapshot(r1, "demo", [_rec("1", weird={"__decimal__": "99.99"})])


def test_changes_table_records_change_type_and_only_changed_rows_carry_a_payload(storage: Storage) -> None:
    """Finding 4: `change_type` disambiguates the row; added/removed store no field payload."""
    r1 = storage.start_run(["demo"])
    storage.save_snapshot(r1, "demo", [_rec("1", price=Decimal("10")), _rec("2", price=Decimal("5"))])
    r2 = storage.start_run(["demo"])
    storage.save_snapshot(r2, "demo", [_rec("1", price=Decimal("12")), _rec("3", price=Decimal("7"))])

    latest, previous = storage.latest_snapshots("demo", n=2)
    changeset = diff(previous, latest)
    storage.save_changes(r2, "demo", changeset)

    with Session(storage._engine) as session:
        rows = session.query(ChangeRow).filter(ChangeRow.run_id == r2.id).all()
    by_type = {row.change_type: row for row in rows}

    assert by_type["changed"].field == "price"
    assert json.loads(by_type["changed"].before_json) == {"__decimal__": "10"}
    assert json.loads(by_type["changed"].after_json) == {"__decimal__": "12"}

    assert by_type["added"].field is None
    assert by_type["added"].before_json is None
    assert by_type["added"].after_json is None

    assert by_type["removed"].field is None
    assert by_type["removed"].before_json is None
    assert by_type["removed"].after_json is None
