"""The report is the same facts twice: once for machines, once for people."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scrapewatch.models import Record
from scrapewatch.pipeline.diff import diff
from scrapewatch.pipeline.report import ChangeReport

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


def test_json_carries_counts_and_changes() -> None:
    cs = diff([_rec("1", price=Decimal("1"))], [_rec("1", price=Decimal("2")), _rec("3", price=Decimal("1"))])
    report = ChangeReport.from_changesets({"demo": cs}, generated_at=datetime(2026, 9, 22, tzinfo=UTC))
    payload = json.loads(report.to_json())
    assert payload["sources"]["demo"]["changed"] == 1 and payload["sources"]["demo"]["added"] == 1
    expected_change = {"external_id": "1", "field": "price", "before": "1", "after": "2"}
    assert payload["sources"]["demo"]["changes"][0] == expected_change


def test_html_escapes_what_the_site_said() -> None:
    cs = diff([], [_rec("1", title="<script>x</script>")])
    html = ChangeReport.from_changesets({"demo": cs}, generated_at=datetime.now(UTC)).to_html()
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_a_source_with_no_changes_is_stated_not_omitted() -> None:
    report = ChangeReport.from_changesets({"books": diff([], [])}, generated_at=datetime.now(UTC))
    assert json.loads(report.to_json())["sources"]["books"] == {"added": 0, "removed": 0, "changed": 0, "changes": []}


def test_html_shows_an_absent_side_as_a_placeholder_not_the_word_none() -> None:
    cs = diff([_rec("1", stock=None)], [_rec("1", stock=22)])
    html = ChangeReport.from_changesets({"demo": cs}, generated_at=datetime.now(UTC)).to_html()
    assert "(absent)" in html and "None" not in html
