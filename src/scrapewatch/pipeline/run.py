"""Tie the pipeline together: fetch, normalise, snapshot, diff, report — once per source.

Two layers of isolation, because a monitor that throws away a night's data over one
bad input is worse than useless:

- **Per record.** One malformed record must not discard the rest of a fetch — for a
  source scraping a thousand books, one bad listing throwing away 999 good ones (some
  of which may carry the very price change this tool exists to find) is the wrong
  default. `normalize()` failures are caught per record, counted as `parse_errors`,
  and the snapshot is built from whatever parsed. `skipped` is reserved for a source
  that produced nothing usable at all — either `fetch()` itself raised, or every
  record it yielded failed to normalise.

- **Per source.** Nothing beyond that — a broken `fetch()`, a storage failure while
  saving the snapshot or the diff — is allowed to abort the run: it is recorded as
  skipped with the failure's message, and the sources after it still get their turn.
  `run-stats.json` and the change report are written unconditionally, even if every
  source failed, because "the run produced two files that both say nothing succeeded"
  is a very different, and much more useful, outcome than "the run produced nothing".

A source that fetches cleanly but yields nothing is not skipped: it is a real, empty
snapshot, and diffing it against whatever came before correctly reports everything as
removed. That is the truth, not a bug to hide.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scrapewatch.config import Settings
from scrapewatch.models import RawRecord, Record
from scrapewatch.pipeline.diff import ChangeSet, diff
from scrapewatch.pipeline.normalize import normalize
from scrapewatch.pipeline.report import ChangeReport
from scrapewatch.sources.base import Source
from scrapewatch.storage import Run, Storage

#: Numeric stats every source reports, even a source that exposes none of its own —
#: the missing ones are 0, not absent, so `run-stats.json` has a stable shape.
_ZERO_STATS = {"records": 0, "pages": 0, "requests": 0, "retries": 0, "bytes": 0, "seconds": 0}

#: How many `normalize()` failure messages a source's stats keep verbatim. A source
#: with a systematically broken feed can produce thousands; the report needs enough
#: to diagnose the problem, not all of them.
_MAX_PARSE_ERROR_REASONS = 5

#: The `Source` protocol's members, named individually so a `TypeError` from
#: `_validate_source` can say exactly which ones a mis-implemented source is missing.
_SOURCE_PROTOCOL_ATTRS = ("name", "kind", "fetch", "stats")


@dataclass
class RunResult:
    """What one call to `run_sources` produced: the run handle, per-source stats, the report."""

    run: Run
    stats: dict[str, dict]
    report: ChangeReport


def _validate_source(source: object) -> None:
    """Refuse a source that does not satisfy the `Source` protocol, naming what's missing.

    Checked once per source before the run starts, not lazily as each is used: a
    mis-implemented source should fail at the door, not three calls into a run after
    it has already been credited with a fetch.
    """
    if isinstance(source, Source):
        return
    missing = [attr for attr in _SOURCE_PROTOCOL_ATTRS if not hasattr(source, attr)]
    raise TypeError(f"{source!r} does not satisfy the Source protocol: missing {missing}")


def _skipped_stats(
    source: Source, reason: str, *, parse_errors: int = 0, parse_error_reasons: list[str] | None = None
) -> dict[str, Any]:
    return {
        **_ZERO_STATS,
        "kind": source.kind,
        "skipped": True,
        "reason": reason,
        "parse_errors": parse_errors,
        "parse_error_reasons": list(parse_error_reasons or []),
    }


def _normalize_all(raw_records: list[RawRecord]) -> tuple[list[Record], list[str]]:
    """Normalise every raw record, isolating one bad record from the rest of the fetch.

    Returns what parsed, and up to `_MAX_PARSE_ERROR_REASONS` reasons for what didn't;
    the caller derives the failure count from `len(raw_records) - len(records)`, so
    every failure is counted even once the reasons list is full.
    """
    records: list[Record] = []
    reasons: list[str] = []
    for raw in raw_records:
        try:
            records.append(normalize(raw))
        except Exception as exc:  # one bad record must not discard the rest of the fetch
            if len(reasons) < _MAX_PARSE_ERROR_REASONS:
                reasons.append(str(exc))
    return records, reasons


def run_sources(sources: list[Source], storage: Storage, settings: Settings, out_dir: Path) -> RunResult:
    """Run every source, save what it saw, diff it against its last snapshot, and report.

    Writes `run-stats.json` and `change-report.json`/`.html` into `out_dir`, which is
    created if missing, unconditionally — even a run where every source failed writes
    both files, saying so. `settings` is accepted for the shape sources will need it
    for (a base URL, timeouts); nothing in this function reads it directly yet.
    """
    del settings  # not needed by this task's sources; kept for the interface's sake

    for source in sources:
        _validate_source(source)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    run = storage.start_run([source.name for source in sources])
    changesets: dict[str, ChangeSet] = {}
    stats: dict[str, dict] = {}

    for source in sources:
        try:
            raw_records = list(source.fetch())
        except Exception as exc:  # a broken source is reported, not fatal to the run
            stats[source.name] = _skipped_stats(source, str(exc))
            continue

        records, parse_error_reasons = _normalize_all(raw_records)
        parse_errors = len(raw_records) - len(records)

        if raw_records and not records:
            # Every fetched record failed to normalise: nothing usable came out of it,
            # which is a different claim from "fetched zero records" and is reported
            # as skipped, not as an honest empty snapshot.
            reason = f"all {parse_errors} fetched record(s) failed to parse: {'; '.join(parse_error_reasons)}"
            stats[source.name] = _skipped_stats(
                source, reason, parse_errors=parse_errors, parse_error_reasons=parse_error_reasons
            )
            continue

        try:
            storage.save_snapshot(run, source.name, records)
            snapshots = storage.latest_snapshots(source.name, n=2)
            latest = snapshots[0]
            previous = snapshots[1] if len(snapshots) > 1 else []
            changeset = diff(previous, latest)
            storage.save_changes(run, source.name, changeset)
        except Exception as exc:  # a storage failure costs this source, not the whole run
            stats[source.name] = _skipped_stats(
                source, str(exc), parse_errors=parse_errors, parse_error_reasons=parse_error_reasons
            )
            continue

        changesets[source.name] = changeset
        merged = {**_ZERO_STATS, **dict(source.stats)}
        stats[source.name] = {
            **merged,
            "kind": source.kind,
            "skipped": False,
            "reason": None,
            "parse_errors": parse_errors,
            "parse_error_reasons": parse_error_reasons,
        }

    storage.finish_run(run, stats)

    generated_at = datetime.now(UTC)
    report = ChangeReport.from_changesets(changesets, generated_at=generated_at)

    (out_dir / "run-stats.json").write_text(
        json.dumps({"generated_at": generated_at.isoformat(), "sources": stats}, indent=2), encoding="utf-8"
    )
    (out_dir / "change-report.json").write_text(report.to_json(), encoding="utf-8")
    (out_dir / "change-report.html").write_text(report.to_html(), encoding="utf-8")

    return RunResult(run=run, stats=stats, report=report)
