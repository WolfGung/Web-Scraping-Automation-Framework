"""Tie the pipeline together: fetch, normalise, snapshot, diff, report — once per source.

A source that raises out of `fetch()` does not stop the run: it is recorded in the
stats as skipped, with the exception's message as the reason, and the sources after it
still get their turn — the practical answer to a practice site being down for one
source out of several. A source that fetches cleanly but yields nothing is not
skipped: it is a real, empty snapshot, and diffing it against whatever came before
correctly reports everything as removed. That is the truth, not a bug to hide.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from scrapewatch.config import Settings
from scrapewatch.pipeline.diff import ChangeSet, diff
from scrapewatch.pipeline.normalize import normalize
from scrapewatch.pipeline.report import ChangeReport
from scrapewatch.sources.base import Source
from scrapewatch.storage import Run, Storage

#: Numeric stats every source reports, even a source that exposes none of its own —
#: the missing ones are 0, not absent, so `run-stats.json` has a stable shape.
_ZERO_STATS = {"records": 0, "pages": 0, "requests": 0, "retries": 0, "bytes": 0, "seconds": 0}


@dataclass
class RunResult:
    """What one call to `run_sources` produced: the run handle, per-source stats, the report."""

    run: Run
    stats: dict[str, dict]
    report: ChangeReport


def run_sources(sources: list[Source], storage: Storage, settings: Settings, out_dir: Path) -> RunResult:
    """Run every source, save what it saw, diff it against its last snapshot, and report.

    Writes `run-stats.json` and `change-report.json`/`.html` into `out_dir`, which is
    created if missing. `settings` is accepted for the shape sources will need it for
    (a base URL, timeouts); nothing in this function reads it directly yet.
    """
    del settings  # not needed by this task's sources; kept for the interface's sake

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    run = storage.start_run([source.name for source in sources])
    changesets: dict[str, ChangeSet] = {}
    stats: dict[str, dict] = {}

    for source in sources:
        try:
            records = [normalize(raw) for raw in source.fetch()]
        except Exception as exc:  # a broken source is reported, not fatal to the run
            stats[source.name] = {**_ZERO_STATS, "kind": source.kind, "skipped": True, "reason": str(exc)}
            continue

        storage.save_snapshot(run, source.name, records)
        snapshots = storage.latest_snapshots(source.name, n=2)
        latest = snapshots[0]
        previous = snapshots[1] if len(snapshots) > 1 else []

        changeset = diff(previous, latest)
        storage.save_changes(run, source.name, changeset)
        changesets[source.name] = changeset

        merged = {**_ZERO_STATS, **dict(source.stats)}
        stats[source.name] = {**merged, "kind": source.kind, "skipped": False, "reason": None}

    storage.finish_run(run, stats)

    generated_at = datetime.now(UTC)
    report = ChangeReport.from_changesets(changesets, generated_at=generated_at)

    (out_dir / "run-stats.json").write_text(
        json.dumps({"generated_at": generated_at.isoformat(), "sources": stats}, indent=2), encoding="utf-8"
    )
    (out_dir / "change-report.json").write_text(report.to_json(), encoding="utf-8")
    (out_dir / "change-report.html").write_text(report.to_html(), encoding="utf-8")

    return RunResult(run=run, stats=stats, report=report)
