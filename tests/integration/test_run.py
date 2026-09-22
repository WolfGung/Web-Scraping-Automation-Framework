"""run_sources ties fetch -> normalize -> snapshot -> diff -> report together.

Two isolation boundaries are under test here: a single bad record must not discard an
otherwise-good fetch (Finding 3), and a failure anywhere in one source's handling —
fetch, or storage — must not stop the sources after it, and the two output files must
still be written even when every source failed (Finding 2). A source that does not
satisfy the `Source` protocol is refused before any of that runs (Finding 5).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scrapewatch.models import RawRecord, Record
from scrapewatch.pipeline.diff import ChangeSet
from scrapewatch.pipeline.run import _ZERO_STATS, run_sources
from scrapewatch.storage import Run, Storage

pytestmark = pytest.mark.integration


class _FakeSource:
    """A `Source` that needs no network: good records, bad ones, or an outright failure.

    `RawRecord.source` is always `"demo"` because `normalize()` only recognises
    `"books"`/`"quotes"`/`"demo"` — but `Source.name` (the storage/report key) is
    whatever the caller passes, so two fakes with different names can share that
    dispatch. Nothing in `run_sources` requires the two to match.
    """

    def __init__(
        self,
        name: str = "demo",
        *,
        kind: str = "product",
        fail: bool = False,
        bad_records: int = 0,
        good_records: int = 2,
    ) -> None:
        self.name = name
        self.kind = kind
        self._fail = fail
        self._bad_records = bad_records
        self._good_records = good_records
        self.stats = {
            "records": good_records, "pages": 1, "requests": 3, "retries": 1, "bytes": 512, "seconds": 0.25,
        }

    def fetch(self) -> Iterator[RawRecord]:
        if self._fail:
            raise RuntimeError(f"{self.name} site is down")
        now = datetime.now(UTC)
        for i in range(self._bad_records):
            eid = f"bad-{i}"
            yield RawRecord(
                source="demo", external_id=eid, fetched_at=now, url=f"https://d/{eid}",
                fields={"price": "$10", "in_stock": True, "stock": 5},  # missing "name" -> normalize() raises
            )
        for i in range(self._good_records):
            eid = f"good-{i}"
            yield RawRecord(
                source="demo", external_id=eid, fetched_at=now, url=f"https://d/{eid}",
                fields={"name": f"Item {eid}", "price": "$10", "in_stock": True, "stock": 5},
            )


class _StorageThatFailsFor:
    """Wraps a real `Storage`, but raises out of `save_snapshot` for one named source.

    Simulates a storage outage that should cost only that source, not the sources
    after it and not the run's output files.
    """

    def __init__(self, storage: Storage, failing_source: str) -> None:
        self._storage = storage
        self._failing_source = failing_source

    def start_run(self, sources: list[str]) -> Run:
        return self._storage.start_run(sources)

    def save_snapshot(self, run: Run, source: str, records: list[Record]):
        if source == self._failing_source:
            raise RuntimeError(f"storage is unreachable for {source}")
        return self._storage.save_snapshot(run, source, records)

    def latest_snapshots(self, source: str, n: int = 2) -> list[list[Record]]:
        return self._storage.latest_snapshots(source, n=n)

    def save_changes(self, run: Run, source: str, changeset: ChangeSet) -> None:
        self._storage.save_changes(run, source, changeset)

    def finish_run(self, run: Run, stats: dict) -> None:
        self._storage.finish_run(run, stats)

    def export(self, source: str, fmt: Any, path: Any) -> None:
        self._storage.export(source, fmt, path)


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(f"sqlite:///{tmp_path / 'run.sqlite3'}")
    s.open()
    return s


def test_run_sources_writes_stats_and_a_report_naming_the_sources_own_numbers(
    storage: Storage, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"

    result = run_sources([_FakeSource()], storage, out_dir)

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
        "parse_errors": 0,
        "parse_error_reasons": [],
    }
    assert result.stats["demo"] == stats

    # A first run has nothing to compare against, so everything is reported as added.
    report = json.loads(report_json_path.read_text())
    assert report["sources"]["demo"]["added"] == 2


def test_a_collected_entry_has_the_same_keys_as_a_skipped_one(storage: Storage, tmp_path: Path) -> None:
    """The page reads every source's entry the same way, so every entry has one shape.

    A source that ran and one that never started are written by different code paths —
    the first from what the source counted, the second from zeroes — and the published
    page walks both. A key present in one and missing from the other is a page that
    reads a number off one source and nothing off the next; a key of a different
    *kind* (`hosts` used to arrive as a dict) is worse, because it survives into the
    published JSON and the table quietly stops lining up.
    """
    out_dir = tmp_path / "out"

    result = run_sources([_FakeSource(name="ok"), _FakeSource(name="down", fail=True)], storage, out_dir)

    described = {"kind", "skipped", "reason", "parse_errors", "parse_error_reasons"}
    assert set(result.stats["ok"]) == set(_ZERO_STATS) | described
    assert set(result.stats["ok"]) == set(result.stats["down"])
    assert all(isinstance(result.stats["ok"][name], (int, float)) for name in _ZERO_STATS)


def test_a_source_whose_fetch_raises_is_skipped_and_the_run_continues(storage: Storage, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"

    result = run_sources([_FakeSource(fail=True)], storage, out_dir)

    stats = json.loads((out_dir / "run-stats.json").read_text())["sources"]["demo"]
    assert stats["skipped"] is True
    assert stats["reason"] == "demo site is down"
    assert stats["records"] == 0
    assert result.stats["demo"]["skipped"] is True

    # No snapshot was ever saved for a source that never fetched, so it has no place
    # in the change report — a failure is not the same claim as "nothing changed".
    report = json.loads((out_dir / "change-report.json").read_text())
    assert "demo" not in report["sources"]


def test_one_bad_record_is_isolated_the_source_is_not_skipped(storage: Storage, tmp_path: Path) -> None:
    """Finding 3: one unparsable record out of three still yields a snapshot of two."""
    out_dir = tmp_path / "out"
    source = _FakeSource(bad_records=1, good_records=2)

    result = run_sources([source], storage, out_dir)

    stats = result.stats["demo"]
    assert stats["skipped"] is False
    assert stats["parse_errors"] == 1
    assert len(stats["parse_error_reasons"]) == 1
    assert "name" in stats["parse_error_reasons"][0]

    (latest,) = storage.latest_snapshots("demo", n=1)
    assert len(latest) == 2


def test_a_source_whose_every_record_fails_to_parse_is_skipped(storage: Storage, tmp_path: Path) -> None:
    """Finding 3's other half: "skipped" is reserved for nothing usable at all."""
    out_dir = tmp_path / "out"
    source = _FakeSource(bad_records=2, good_records=0)

    result = run_sources([source], storage, out_dir)

    stats = result.stats["demo"]
    assert stats["skipped"] is True
    assert stats["parse_errors"] == 2
    assert len(stats["parse_error_reasons"]) == 2
    assert storage.latest_snapshots("demo", n=1) == []


def test_a_storage_failure_on_one_source_does_not_stop_the_run(storage: Storage, tmp_path: Path) -> None:
    """Finding 2, reproduced the way the reviewer did: storage breaks for source one."""
    out_dir = tmp_path / "out"
    failing_storage = _StorageThatFailsFor(storage, failing_source="broken")
    sources = [_FakeSource(name="broken"), _FakeSource(name="healthy")]

    result = run_sources(sources, failing_storage, out_dir)

    assert result.stats["broken"]["skipped"] is True
    assert "storage is unreachable" in result.stats["broken"]["reason"]
    assert result.stats["healthy"]["skipped"] is False

    assert (out_dir / "run-stats.json").exists()
    assert (out_dir / "change-report.json").exists()
    assert (out_dir / "change-report.html").exists()

    # The second source's snapshot really was saved, despite the first one failing.
    (latest,) = storage.latest_snapshots("healthy", n=1)
    assert len(latest) == 2


def test_every_source_failing_still_writes_both_output_files(storage: Storage, tmp_path: Path) -> None:
    """Finding 2's stronger claim: the files are unconditional, not just "usually there"."""
    out_dir = tmp_path / "out"
    sources = [_FakeSource(name="a", fail=True), _FakeSource(name="b", fail=True)]

    result = run_sources(sources, storage, out_dir)

    assert (out_dir / "run-stats.json").exists()
    assert (out_dir / "change-report.json").exists()
    assert result.stats["a"]["skipped"] is True
    assert result.stats["b"]["skipped"] is True


def test_a_source_missing_a_protocol_member_is_refused_at_the_door(storage: Storage, tmp_path: Path) -> None:
    """Finding 5: an invalid Source is a TypeError before anything runs, not partway through."""

    class _MissingStats:
        name = "demo"
        kind = "product"

        def fetch(self) -> Iterator[RawRecord]:
            return iter([])

    with pytest.raises(TypeError, match="stats"):
        run_sources([_MissingStats()], storage, tmp_path / "out")

    # Nothing was written: the source was refused before the run even started.
    assert not (tmp_path / "out").exists()
