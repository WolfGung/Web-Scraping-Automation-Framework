"""run_sources ties fetch -> normalize -> snapshot -> diff -> report together."""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scrapewatch.config import Settings
from scrapewatch.models import RawRecord
from scrapewatch.pipeline.run import run_sources
from scrapewatch.storage import Storage

pytestmark = pytest.mark.integration


class _FakeSource:
    """A `Source` that needs no network: two demo records, or a failure, on demand."""

    name = "demo"
    kind = "product"

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.stats = {"records": 2, "pages": 1, "requests": 3, "retries": 1, "bytes": 512, "seconds": 0.25}

    def fetch(self) -> Iterator[RawRecord]:
        if self._fail:
            raise RuntimeError("demo site is down")
        now = datetime.now(UTC)
        yield RawRecord(
            source="demo", external_id="1", fetched_at=now, url="https://d/1",
            fields={"name": "Widget", "price": "$10", "in_stock": True, "stock": 5},
        )
        yield RawRecord(
            source="demo", external_id="2", fetched_at=now, url="https://d/2",
            fields={"name": "Gadget", "price": "$20", "in_stock": False, "stock": 0},
        )


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(f"sqlite:///{tmp_path / 'run.sqlite3'}")
    s.open()
    return s


def test_run_sources_writes_stats_and_a_report_naming_the_sources_own_numbers(
    storage: Storage, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"

    result = run_sources([_FakeSource()], storage, Settings(), out_dir)

    stats_path = out_dir / "run-stats.json"
    report_json_path = out_dir / "change-report.json"
    report_html_path = out_dir / "change-report.html"
    assert stats_path.exists() and report_json_path.exists() and report_html_path.exists()

    stats = json.loads(stats_path.read_text())["sources"]["demo"]
    assert stats == {
        "records": 2,
        "pages": 1,
        "requests": 3,
        "retries": 1,
        "bytes": 512,
        "seconds": 0.25,
        "kind": "product",
        "skipped": False,
        "reason": None,
    }
    assert result.stats["demo"] == stats

    # A first run has nothing to compare against, so everything is reported as added.
    report = json.loads(report_json_path.read_text())
    assert report["sources"]["demo"]["added"] == 2


def test_a_source_that_raises_is_skipped_and_the_run_continues(storage: Storage, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"

    result = run_sources([_FakeSource(fail=True)], storage, Settings(), out_dir)

    stats = json.loads((out_dir / "run-stats.json").read_text())["sources"]["demo"]
    assert stats["skipped"] is True
    assert stats["reason"] == "demo site is down"
    assert stats["records"] == 0
    assert result.stats["demo"]["skipped"] is True

    # No snapshot was ever saved for a source that never fetched, so it has no place
    # in the change report — a failure is not the same claim as "nothing changed".
    report = json.loads((out_dir / "change-report.json").read_text())
    assert "demo" not in report["sources"]
