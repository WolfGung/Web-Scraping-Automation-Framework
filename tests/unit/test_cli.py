"""scrapewatch's command line, exercised through Typer's own `CliRunner`.

Three tests, deliberately kept few: an unknown source is refused before anything
runs, a bad `--db-url` is reported instead of crashing, and a real (if tiny) `scrape
demo` writes both output files. Everything else the CLI does — `report`, `export`,
`stats`, `demo-store` — is a thin wrapper over `Storage`/`diff`/`ChangeReport`,
already proven in `tests/integration` and `tests/unit/test_report.py`; re-testing
their logic here through the CLI would only restate it.

`test_scrape_demo_writes_stats_and_report` starts a real HTTP server (`demo_store_url`,
shared from the top-level `tests/conftest.py`), so it carries its own `e2e` marker
instead of the module's `unit` one — there is no module-level `pytestmark` here for
exactly that reason: a blanket mark would tag this test `unit` too, and the suite's
network-free gate (`pytest -m "not live"`) would then need a server for something it
calls unit.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from scrapewatch.cli import app


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
def test_record_scroll_writes_the_recording_and_the_trace_the_page_publishes(tmp_path, demo_store_url) -> None:
    """Что: `record-scroll` против живого демо-магазина.

    Зачем: витрина публикует ровно два файла с фиксированными именами — запись
    прокрутки и trace к ней; если команда назовёт их иначе или не доведёт прокрутку
    до конца, страница молча опубликуется без видео.
    Как: гоняем команду через CliRunner и проверяем оба файла и число карточек —
    прокрутка обязана дойти до конца каталога, а не остановиться на первой странице.
    """
    result = CliRunner().invoke(
        app, ["record-scroll", "--demo-url", demo_store_url, "--out", str(tmp_path / "media")]
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "media" / "scroll.webm").stat().st_size > 0
    assert (tmp_path / "media" / "scroll-trace.zip").stat().st_size > 0
    assert "40 products scrolled" in result.output
