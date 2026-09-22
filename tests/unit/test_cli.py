"""scrapewatch's command line, exercised through Typer's own `CliRunner`.

Deliberately few, and each about something only the CLI can get wrong: an unknown
source is refused before anything runs, a bad `--db-url` is reported instead of
crashing, a real (if tiny) `scrape demo` writes both output files, a source the
caller skipped still reaches `run-stats.json` with its reason, `--export-dir`
exports what the run collected and nothing else, and `record-scroll` produces the
two files the published page shows. Everything else the CLI does — `report`,
`export`, `stats`, `demo-store` — is a thin wrapper over
`Storage`/`diff`/`ChangeReport`, already proven in `tests/integration` and
`tests/unit/test_report.py`; re-testing their logic here would only restate it.

`test_scrape_demo_writes_stats_and_report` starts a real HTTP server (`demo_store_url`,
shared from the top-level `tests/conftest.py`), so it carries its own `e2e` marker
instead of the module's `unit` one — there is no module-level `pytestmark` here for
exactly that reason: a blanket mark would tag this test `unit` too, and the suite's
network-free gate (`pytest -m "not live"`) would then need a server for something it
calls unit.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from scrapewatch.cli import app
from scrapewatch.storage import SnapshotRow


@pytest.mark.unit
def test_scrape_names_the_sources_it_knows() -> None:
    result = CliRunner().invoke(app, ["scrape", "nowhere"])
    assert result.exit_code != 0 and "books" in result.output and "quotes" in result.output and "demo" in result.output


@pytest.mark.unit
def test_scrape_reports_a_bad_db_url_instead_of_a_traceback() -> None:
    """`Storage.open()` fails on a scheme SQLAlchemy has no dialect for — `scrape`
    must report that itself, not let it escape as an unhandled traceback. No network
    is touched: storage opens before anything reaches `--demo-url` at all.
    """
    result = CliRunner().invoke(
        app,
        ["scrape", "demo", "--db-url", "not-a-real-scheme://nope", "--demo-url", "http://127.0.0.1:1"],
    )
    assert result.exit_code == 1
    assert "scrape failed" in result.output
    assert "Traceback" not in result.output


@pytest.mark.e2e
def test_scrape_demo_writes_stats_and_report(tmp_path, demo_store_url) -> None:
    result = CliRunner().invoke(
        app,
        [
            "scrape",
            "demo",
            "--out",
            str(tmp_path),
            "--demo-url",
            demo_store_url,
            "--db-url",
            f"sqlite:///{tmp_path}/db.sqlite3",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "run-stats.json").is_file() and (tmp_path / "change-report.json").is_file()


@pytest.mark.e2e
def test_a_source_the_caller_skipped_reaches_the_run_statistics_with_its_reason(
    tmp_path, demo_store_url
) -> None:
    """A source dropped before the run never reaches `run_sources`, so nothing in
    the pipeline can write it down — and the published page could not then say
    "books was not collected, and here is what the site answered". The CLI knows the
    reason, because it was given it, so the CLI is where the entry is added.
    """
    result = CliRunner().invoke(
        app,
        [
            "scrape", "all",
            "--skip-books", "--skip-reason-books", "HTTP 503",
            "--skip-quotes",
            "--out", str(tmp_path),
            "--demo-url", demo_store_url,
            "--db-url", f"sqlite:///{tmp_path}/db.sqlite3",
        ],
    )
    assert result.exit_code == 0, result.output

    sources = json.loads((tmp_path / "run-stats.json").read_text(encoding="utf-8"))["sources"]
    assert list(sources) == ["books", "quotes", "demo"]  # the order the page's table follows
    assert sources["books"]["skipped"] is True
    assert sources["books"]["reason"] == "HTTP 503"
    # `kind` is the door, so a source that never ran still says which one it would
    # have gone through — the page's table has a column for it.
    assert sources["books"]["records"] == 0 and sources["books"]["kind"] == "http"
    assert sources["quotes"]["kind"] == "browser" and sources["demo"]["kind"] == "local"
    # A skip with no reason given still says something a reader can act on.
    assert sources["quotes"]["reason"] == "skipped by --skip-quotes"
    assert sources["demo"]["skipped"] is False

    assert "books: skipped — HTTP 503" in result.output
    assert "quotes: skipped — skipped by --skip-quotes" in result.output


@pytest.mark.e2e
def test_export_dir_writes_the_sources_the_run_actually_collected(tmp_path, demo_store_url) -> None:
    """Exporting on the probe's word rather than the run's would publish yesterday's
    snapshot of a source that answered but yielded nothing, under tonight's date,
    while the page said that source was not collected.
    """
    result = CliRunner().invoke(
        app,
        [
            "scrape", "all",
            "--skip-books", "--skip-quotes",
            "--out", str(tmp_path),
            "--export-dir", str(tmp_path / "exports"),
            "--demo-url", demo_store_url,
            "--db-url", f"sqlite:///{tmp_path}/db.sqlite3",
        ],
    )
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in (tmp_path / "exports").iterdir()) == ["demo.json"]
    assert str(tmp_path / "exports" / "demo.json") in result.output


@pytest.mark.e2e
def test_the_database_keeps_only_the_snapshots_the_caller_asked_for(tmp_path, demo_store_url) -> None:
    """The database is published every night, so its history is bounded on purpose."""
    for _ in range(3):
        result = CliRunner().invoke(
            app,
            [
                "scrape", "demo",
                "--out", str(tmp_path),
                "--keep-snapshots", "2",
                "--demo-url", demo_store_url,
                "--db-url", f"sqlite:///{tmp_path}/db.sqlite3",
            ],
        )
        assert result.exit_code == 0, result.output

    with Session(create_engine(f"sqlite:///{tmp_path}/db.sqlite3")) as session:
        assert session.scalar(select(func.count()).select_from(SnapshotRow)) == 2
    assert "pruned 1 snapshot(s) beyond the last 2 per source" in result.output


@pytest.mark.e2e
def test_record_scroll_writes_the_recording_and_the_trace_the_page_publishes(tmp_path, demo_store_url) -> None:
    """`record-scroll` against a live demo store, end to end.

    The published page shows exactly two files, by name: the recording of the scroll
    and the Playwright trace of it. A command that named them differently, or that
    stopped scrolling at the first page, would leave the page to publish itself
    without a video and say nothing about why — so both files and the number of
    cards the scroll actually reached are asserted here.
    """
    result = CliRunner().invoke(
        app, ["record-scroll", "--demo-url", demo_store_url, "--out", str(tmp_path / "media")]
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "media" / "scroll.webm").stat().st_size > 0
    assert (tmp_path / "media" / "scroll-trace.zip").stat().st_size > 0
    assert "40 products scrolled" in result.output
