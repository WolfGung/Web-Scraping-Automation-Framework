"""scrapewatch's command line: scrape a source, rebuild the report, export a snapshot.

One process per invocation, no state kept between commands: every command builds its
own `Storage`/`PoliteClient`/sources from `Settings()` and the flags it was given,
runs once, and exits. `scrape` and `record-scroll` are the only commands that touch
the network or a browser; `report`, `export` and `stats` only ever read back what a
previous `scrape` already wrote.

`scrape`'s exit code follows one rule: 0 even when a source was skipped, because a
skip is a fact `run-stats.json` already states, not a reason to fail the invocation
that reported it — 1 only when `run_sources` itself raises, which is a run that
produced nothing at all to report.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime
from pathlib import Path

import typer
import uvicorn
from selectolax.parser import HTMLParser

from scrapewatch.browser import BrowserSession
from scrapewatch.config import Settings
from scrapewatch.demo_store.app import create_app
from scrapewatch.http import PoliteClient
from scrapewatch.pipeline.diff import diff
from scrapewatch.pipeline.report import ChangeReport
from scrapewatch.pipeline.run import run_sources
from scrapewatch.sources.base import Source
from scrapewatch.sources.books import BooksSource
from scrapewatch.sources.demo import DemoSource
from scrapewatch.sources.quotes import QuotesSource
from scrapewatch.storage import Storage

app = typer.Typer(help="ScrapeWatch: polite multi-source scraping with change detection.")

#: Every source this CLI knows about, and the only names `scrape`/`export` accept.
#: `scrape all` walks them in this order, which is also the order stats print in.
KNOWN_SOURCES: tuple[str, ...] = ("books", "quotes", "demo")


def _unknown_source_message(name: str) -> str:
    return f"unknown source {name!r}: known sources are {', '.join(KNOWN_SOURCES)}"


def _resolved_sources(source: str, *, skip_books: bool, skip_quotes: bool) -> list[str] | None:
    """The source names a `scrape` invocation should run, or `None` for an unknown name."""
    if source == "all":
        return [
            name
            for name in KNOWN_SOURCES
            if not (name == "books" and skip_books) and not (name == "quotes" and skip_quotes)
        ]
    if source in KNOWN_SOURCES:
        return [source]
    return None


def _browser_session_for(names: list[str], settings: Settings) -> AbstractContextManager[BrowserSession | None]:
    """A `BrowserSession` only when `quotes` is actually being run — nothing else needs one."""
    if "quotes" in names:
        return BrowserSession(settings)
    return nullcontext(None)


def _build_sources(
    names: list[str], client: PoliteClient, session: BrowserSession | None, *, max_pages: int | None, demo_url: str
) -> list[Source]:
    sources: list[Source] = []
    for name in names:
        if name == "books":
            sources.append(BooksSource(client, max_pages=max_pages))
        elif name == "quotes":
            sources.append(QuotesSource(session, client, max_pages=max_pages))
        elif name == "demo":
            sources.append(DemoSource(client, demo_url))
    return sources


def _changes_in(report: ChangeReport, name: str) -> int:
    changeset = report.changesets.get(name)
    if changeset is None:
        return 0
    return len(changeset.added) + len(changeset.removed) + len(changeset.changed)


@app.command()
def scrape(
    source: str = typer.Argument(..., help="books, quotes, demo, or all."),
    out: Path = typer.Option(Path("data/run"), "--out", help="Directory to write the run's outputs into."),
    db_url: str | None = typer.Option(None, "--db-url", help="Storage URL. Defaults to SCRAPEWATCH_DB_URL."),
    max_pages: int | None = typer.Option(None, "--max-pages", help="Cap on pages walked (books and quotes)."),
    demo_url: str | None = typer.Option(None, "--demo-url", help="Demo store URL. Defaults to SCRAPEWATCH_DEMO_URL."),
    skip_books: bool = typer.Option(False, "--skip-books", help="Drop books from 'all' (CI reachability probe)."),
    skip_quotes: bool = typer.Option(False, "--skip-quotes", help="Drop quotes from 'all' (CI reachability probe)."),
) -> None:
    """Fetch one source (or all of them), snapshot it, diff it against the last run, and report."""
    names = _resolved_sources(source, skip_books=skip_books, skip_quotes=skip_quotes)
    if names is None:
        typer.echo(_unknown_source_message(source))
        raise typer.Exit(code=2)

    try:
        # Built inside the guard, not above it: `Settings()` reads the environment,
        # and a malformed `SCRAPEWATCH_*` value — a `min_request_interval_s` that is
        # not a number, say — raises pydantic's own `ValidationError`. Constructed
        # outside, it would escape as a traceback, which is the one thing this
        # command's error handling exists to prevent.
        settings = Settings()
        resolved_db_url = db_url if db_url is not None else settings.db_url
        resolved_demo_url = demo_url if demo_url is not None else settings.demo_url

        storage = Storage(resolved_db_url)
        storage.open()
        with PoliteClient(settings) as client, _browser_session_for(names, settings) as session:
            sources = _build_sources(names, client, session, max_pages=max_pages, demo_url=resolved_demo_url)
            result = run_sources(sources, storage, settings, out)
    except Exception as exc:  # a bad --db-url or a run that never produced a result: report it, don't crash
        typer.echo(f"scrape failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    for name in names:
        stats = result.stats[name]
        if stats["skipped"]:
            typer.echo(f"{name}: skipped — {stats['reason']}")
            continue
        changes = _changes_in(result.report, name)
        typer.echo(
            f"{name}: {stats['records']} records, {stats['pages']} pages, {stats['requests']} requests, "
            f"{stats['seconds']:.2f}s, {changes} changes"
        )

    typer.echo(str(out / "run-stats.json"))
    typer.echo(str(out / "change-report.json"))
    typer.echo(str(out / "change-report.html"))


@app.command()
def report(
    db_url: str | None = typer.Option(None, "--db-url", help="Storage URL. Defaults to SCRAPEWATCH_DB_URL."),
    as_json: bool = typer.Option(True, "--json/--html", help="Output format. JSON by default."),
    out: Path | None = typer.Option(None, "--out", help="Write to this path instead of stdout."),
) -> None:
    """Rebuild the change report from storage: the last two snapshots of every known source."""
    try:
        # Inside the guard for the same reason as in `scrape`: a malformed
        # `SCRAPEWATCH_*` value is reported as "report failed: ...", not as a
        # pydantic traceback.
        settings = Settings()
        resolved_db_url = db_url if db_url is not None else settings.db_url

        storage = Storage(resolved_db_url)
        storage.open()

        changesets = {}
        for name in KNOWN_SOURCES:
            snapshots = storage.latest_snapshots(name, n=2)
            if len(snapshots) < 2:
                continue  # nothing to diff yet: fewer than two runs have snapshotted this source
            newer, older = snapshots[0], snapshots[1]
            changesets[name] = diff(older, newer)

        change_report = ChangeReport.from_changesets(changesets, generated_at=datetime.now(UTC))
        text = change_report.to_json() if as_json else change_report.to_html()

        if out is not None:
            Path(out).write_text(text, encoding="utf-8")
    except Exception as exc:  # a bad --db-url or an unwritable --out: report it, don't crash
        typer.echo(f"report failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if out is not None:
        typer.echo(str(out))
    else:
        typer.echo(text)


@app.command()
def export(
    source: str = typer.Argument(..., help="books, quotes, or demo."),
    fmt: str = typer.Option(..., "--format", help="csv or json."),
    out: Path = typer.Option(..., "--out", help="File to write the export to."),
    db_url: str | None = typer.Option(None, "--db-url", help="Storage URL. Defaults to SCRAPEWATCH_DB_URL."),
) -> None:
    """Export the latest snapshot of one source, via `Storage.export`."""
    if source not in KNOWN_SOURCES:
        typer.echo(_unknown_source_message(source))
        raise typer.Exit(code=2)
    if fmt not in ("csv", "json"):
        typer.echo(f"unknown format {fmt!r}: must be csv or json")
        raise typer.Exit(code=2)

    try:
        # Inside the guard, as in `scrape` and `report`.
        settings = Settings()
        resolved_db_url = db_url if db_url is not None else settings.db_url

        storage = Storage(resolved_db_url)
        storage.open()
        storage.export(source, fmt, out)
    except Exception as exc:  # a bad --db-url or an unwritable --out: report it, don't crash
        typer.echo(f"export failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(str(out))


@app.command(name="record-scroll")
def record_scroll(
    demo_url: str | None = typer.Option(None, "--demo-url", help="Demo store URL. Defaults to SCRAPEWATCH_DEMO_URL."),
    out: Path = typer.Option(Path("media"), "--out", help="Directory to write scroll.webm and scroll-trace.zip into."),
) -> None:
    """Record a browser walking the demo store's infinite scroll: one WebM and one trace.

    Nothing else in this project produces that recording: `scrape` drives a browser
    only for `quotes`, and `pytest -m live` drives none at all. The published page
    wants the scroll specifically — it is the one thing here a reader can watch and
    immediately understand — so this command exists to produce it, with fixed output
    names (`scroll.webm`, `scroll-trace.zip`) so that the page and the publish step
    never have to guess which file they mean.

    Playwright names the video itself and only writes it when the context closes, so
    the recording is made into a temporary directory and moved into place afterwards;
    a run that produced no video or no trace fails here rather than leaving the page
    to discover a missing file later.
    """
    try:
        settings = Settings()
        resolved_demo_url = (demo_url if demo_url is not None else settings.demo_url).rstrip("/")
        out_dir = Path(out)
        out_dir.mkdir(parents=True, exist_ok=True)
        video_target = out_dir / "scroll.webm"
        trace_target = out_dir / "scroll-trace.zip"

        with tempfile.TemporaryDirectory(prefix="scrapewatch-scroll-") as tmp:
            recording = Settings(
                record_video=True,
                video_dir=str(Path(tmp) / "videos"),
                trace_dir=str(Path(tmp) / "traces"),
            )
            with BrowserSession(recording, trace_name="scroll") as session:
                html = session.scroll_until(
                    f"{resolved_demo_url}/scroll", item_selector=".product", done_selector='[data-done="true"]'
                )
            cards = len(HTMLParser(html).css(".product"))

            videos = sorted(Path(recording.video_dir).glob("*.webm"))
            if not videos:
                raise RuntimeError(f"the browser wrote no recording into {recording.video_dir}")
            trace_source = Path(recording.trace_dir) / "scroll.zip"
            if not trace_source.is_file():
                raise RuntimeError(f"the browser wrote no trace at {trace_source}")
            shutil.move(str(videos[0]), video_target)
            shutil.move(str(trace_source), trace_target)
    except Exception as exc:  # a store that never answered, or a browser that recorded nothing
        typer.echo(f"record-scroll failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(str(video_target))
    typer.echo(str(trace_target))
    typer.echo(f"{cards} products scrolled")


@app.command(name="demo-store")
def demo_store(
    host: str = typer.Option("127.0.0.1", "--host", help="Interface to bind to."),
    port: int = typer.Option(8765, "--port", help="Port to listen on."),
) -> None:
    """Run the demo store for real, with uvicorn — what `scrape demo` and Compose's `demo-store` service scrape."""
    uvicorn.run(create_app(), host=host, port=port)


@app.command()
def stats(
    out: Path = typer.Option(Path("data/run"), "--out", help="Directory a previous 'scrape' wrote its outputs into."),
) -> None:
    """Print the last run's `run-stats.json` from `out`."""
    stats_path = Path(out) / "run-stats.json"
    if not stats_path.is_file():
        typer.echo(f"no run-stats.json in {out} — run 'scrapewatch scrape' first", err=True)
        raise typer.Exit(code=1)
    typer.echo(stats_path.read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover - exercised via the installed console script
    sys.exit(app())
