"""The page states the run it was built from, not numbers typed by hand.

Every figure on the published page is read out of one run's `run-stats.json`,
`change-report.json` and Allure results. The page is public and sits beside the
report it describes, so a number that disagrees with that report is worse than no
page at all — and a page that promises a recording it does not have, or shows a
broken image where a diagram should be, has already lost the reader.

Each test here assembles an artificial run in a temporary directory, builds the page
from it, and checks both the counting and what the page then says about it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from showcase.build import MARKERS, _fill, build_site, read_collection, summarise

pytestmark = pytest.mark.unit


# -- the run's artefacts, as a run writes them ----------------------------------


def _result(
    tmp: Path,
    name: str,
    status: str,
    package: str,
    tags: list[str],
    *,
    attempt: str = "",
    history: str | None = None,
    stop: int = 1_758_400_000_000,
    parameters: dict[str, str] | None = None,
) -> None:
    """One Allure result file, which is one *attempt* at a test.

    A rerun writes a second file for the same test, carrying the same ``historyId``
    and a later ``stop``. ``attempt`` only names the file, so a test can write more
    than one without overwriting itself.
    """
    body: dict = {
        "name": name,
        "fullName": f"{package}#{name}",
        "status": status,
        "stop": stop,
        "labels": [{"name": "package", "value": package}]
        + [{"name": "tag", "value": tag} for tag in tags],
    }
    if history is not None:
        body["historyId"] = history
    if parameters is not None:
        body["parameters"] = [{"name": k, "value": v} for k, v in parameters.items()]
    (tmp / f"{name}{attempt}-result.json").write_text(json.dumps(body), encoding="utf-8")


def _source_stats(**overrides) -> dict:
    """One source's block of `run-stats.json`, with the shape `run_sources` writes."""
    body = {
        "records": 0,
        "pages": 0,
        "requests": 0,
        "retries": 0,
        "bytes": 0,
        "seconds": 0.0,
        "kind": "local",
        "skipped": False,
        "reason": None,
        "parse_errors": 0,
        "parse_error_reasons": [],
    }
    body.update(overrides)
    return body


def _write_run(
    directory: Path,
    stats: dict | None = None,
    changes: dict | None = None,
    *,
    html: bool = True,
) -> tuple[Path, Path]:
    """`run-stats.json`, `change-report.json` and `change-report.html` in one place."""
    directory.mkdir(parents=True, exist_ok=True)
    stats_body = {
        "generated_at": "2026-09-22T05:12:00+00:00",
        "sources": stats
        if stats is not None
        else {
            "books": _source_stats(kind="http", records=1000, pages=50, requests=51, seconds=12.5),
            "quotes": _source_stats(kind="browser", records=100, pages=10, requests=1, seconds=8.0),
            "demo": _source_stats(kind="local", records=40, pages=4, requests=5, seconds=0.4),
        },
    }
    changes_body = {
        "generated_at": "2026-09-22T05:12:00+00:00",
        "sources": changes
        if changes is not None
        else {
            "books": {"added": 1, "removed": 0, "changed": 2, "changes": []},
            "quotes": {"added": 0, "removed": 0, "changed": 0, "changes": []},
            "demo": {"added": 0, "removed": 0, "changed": 7, "changes": []},
        },
    }
    stats_path = directory / "run-stats.json"
    changes_path = directory / "change-report.json"
    stats_path.write_text(json.dumps(stats_body), encoding="utf-8")
    changes_path.write_text(json.dumps(changes_body), encoding="utf-8")
    if html:
        (directory / "change-report.html").write_text(
            "<!doctype html><html><body>changes</body></html>", encoding="utf-8"
        )
    return stats_path, changes_path


@pytest.fixture
def results(tmp_path: Path) -> Path:
    """The Allure results of a night: the gate, the browser job and the live checks."""
    directory = tmp_path / "results"
    directory.mkdir()
    _result(directory, "a", "passed", "tests.unit.test_diff", ["unit"])
    _result(directory, "b", "passed", "tests.parsers.test_books_parser", ["parsers"])
    _result(directory, "c", "passed", "tests.integration.test_run", ["integration"])
    _result(directory, "d", "passed", "tests.e2e.test_browser_engine", ["e2e"])
    _result(directory, "e", "passed", "tests.live.test_books_live", ["live"])
    (directory / "unrelated.txt").write_text("ignored", encoding="utf-8")
    return directory


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    _write_run(tmp_path / "run")
    return tmp_path / "run"


def _prose(page: str) -> str:
    """The page's text with its line breaks collapsed, for asserting sentences."""
    return re.sub(r"\s+", " ", page)


def _page(results: Path, run_dir: Path, tmp_path: Path, **kwargs) -> str:
    out = kwargs.pop("out_dir", tmp_path / "site")
    build_site(
        run_dir / "run-stats.json",
        run_dir / "change-report.json",
        results,
        out,
        revision=kwargs.pop("revision", "0123456789abcdef"),
        run_url=kwargs.pop("run_url", ""),
        media_dir=kwargs.pop("media_dir", tmp_path / "no-media"),
        data_dir=kwargs.pop("data_dir", tmp_path / "no-data"),
        assets_dir=kwargs.pop("assets_dir", tmp_path / "no-assets"),
        **kwargs,
    )
    return (out / "index.html").read_text(encoding="utf-8")


# -- what was collected: every headline figure comes from the run ---------------


def test_the_headline_figures_are_the_collection_not_the_tests(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    page = _page(results, run_dir, tmp_path)
    assert "<b>1140</b><span>records collected</span>" in page
    assert "<b>64</b><span>pages fetched</span>" in page
    assert "<b>57</b><span>requests made</span>" in page
    assert "<b>10</b><span>changes detected</span>" in page


def test_every_source_is_a_row_with_its_own_numbers(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    prose = _prose(_page(results, run_dir, tmp_path))
    assert "books.toscrape.com</th><td>http</td><td>1000</td><td>50</td><td>51</td>" in prose
    assert "the demo store</th><td>local</td><td>40</td><td>4</td><td>5</td>" in prose


def test_each_source_says_which_door_it_went_through(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    """The door is the decision this project makes a point of, so the table states it.

    Printed as the run recorded it rather than translated on the way to the page:
    `run-stats.json` carries the word the source itself declares, and a second
    vocabulary here would be a second thing to keep in step.
    """
    prose = _prose(_page(results, run_dir, tmp_path))
    assert "quotes.toscrape.com</th><td>browser</td>" in prose
    assert '<th scope="col">Door</th>' in prose


def test_a_source_that_recorded_no_door_says_so_rather_than_guessing_one(
    results: Path, tmp_path: Path
) -> None:
    stats = _source_stats(records=40, pages=4, requests=5)
    del stats["kind"]
    _write_run(
        tmp_path / "run",
        stats={"demo": stats},
        changes={"demo": {"added": 0, "removed": 0, "changed": 0, "changes": []}},
    )
    assert "the demo store</th><td>—</td>" in _prose(_page(results, tmp_path / "run", tmp_path))


def test_a_skipped_source_still_shows_the_door_it_would_have_used(
    results: Path, tmp_path: Path
) -> None:
    """The table has the same number of cells on every row, including a row that is a
    sentence: a skipped source was going to go through a door, and saying which one
    costs nothing and keeps the column honest."""
    _write_run(
        tmp_path / "run",
        stats={
            "books": _source_stats(kind="http", skipped=True, reason="books.toscrape.com answered HTTP 503"),
            "demo": _source_stats(records=40, pages=4, requests=5),
        },
        changes={"demo": {"added": 0, "removed": 0, "changed": 0, "changes": []}},
    )
    prose = _prose(_page(results, tmp_path / "run", tmp_path))
    assert 'books.toscrape.com</th><td>http</td><td colspan="5">not collected' in prose


def test_a_run_with_no_sources_is_an_error_not_a_page_of_zeroes(tmp_path: Path) -> None:
    stats_path, changes_path = _write_run(tmp_path / "run", stats={})
    with pytest.raises(ValueError, match="no sources"):
        read_collection(stats_path, changes_path)


def test_a_missing_change_report_stops_the_build(tmp_path: Path) -> None:
    """The page leads with "changes detected"; without the report there is no
    figure to lead with, and publishing a zero would be a claim about the sites."""
    stats_path, changes_path = _write_run(tmp_path / "run")
    changes_path.unlink()
    with pytest.raises(ValueError, match="no change report"):
        read_collection(stats_path, changes_path)


def test_a_skipped_source_is_named_with_the_reason_the_run_recorded(
    results: Path, tmp_path: Path
) -> None:
    _write_run(
        tmp_path / "run",
        stats={
            "books": _source_stats(skipped=True, reason="books.toscrape.com answered HTTP 503"),
            "demo": _source_stats(records=40, pages=4, requests=5),
        },
        changes={"demo": {"added": 0, "removed": 0, "changed": 7, "changes": []}},
    )
    prose = _prose(_page(results, tmp_path / "run", tmp_path))
    assert "books.toscrape.com was not collected: books.toscrape.com answered HTTP 503." in prose
    assert "not collected — books.toscrape.com answered HTTP 503" in prose


def test_the_lede_counts_the_sources_that_were_collected(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    """It used to say "records from three sources" whatever happened, which is a
    typed claim about a night in which one of them may not have answered at all."""
    prose = _prose(_page(results, run_dir, tmp_path))
    assert "collected from all 3 sources this project scrapes" in prose
    assert "of the 3 sources this project scrapes — whatever did not answer" not in prose


def test_the_lede_says_so_when_a_source_did_not_answer(
    results: Path, tmp_path: Path
) -> None:
    _write_run(
        tmp_path / "run",
        stats={
            "books": _source_stats(skipped=True, reason="HTTP 503"),
            "quotes": _source_stats(records=100, pages=10),
            "demo": _source_stats(records=40, pages=4, requests=5),
        },
        changes={"demo": {"added": 0, "removed": 0, "changed": 7, "changes": []}},
    )
    prose = _prose(_page(results, tmp_path / "run", tmp_path))
    assert "collected from 2 of the 3 sources this project scrapes" in prose
    assert "whatever did not answer is named, with its reason, below" in prose


def test_the_lede_does_not_call_a_partial_run_all_the_sources(
    results: Path, tmp_path: Path
) -> None:
    """"All N sources" is a claim about every source this project scrapes, so it is
    only true when the run carries all of them. A `run-stats.json` with two of the
    three and nothing skipped used to be published as "all 2 sources", which reads
    as a complete night and is not one."""
    _write_run(
        tmp_path / "run",
        stats={
            "quotes": _source_stats(records=100, pages=10),
            "demo": _source_stats(records=40, pages=4, requests=5),
        },
        changes={"demo": {"added": 0, "removed": 0, "changed": 7, "changes": []}},
    )
    prose = _prose(_page(results, tmp_path / "run", tmp_path))
    assert "collected from 2 of the 3 sources this project scrapes" in prose
    assert "all 2 sources" not in prose


def test_a_run_with_nothing_skipped_says_nothing_about_skipping(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    """The guard above must not be a way of always printing the paragraph."""
    assert "was not collected" not in _prose(_page(results, run_dir, tmp_path))


def test_parse_errors_are_stated_rather_than_folded_into_the_totals(
    results: Path, tmp_path: Path
) -> None:
    _write_run(
        tmp_path / "run",
        stats={"demo": _source_stats(records=39, pages=4, requests=5, parse_errors=1)},
        changes={"demo": {"added": 0, "removed": 0, "changed": 0, "changes": []}},
    )
    prose = _prose(_page(results, tmp_path / "run", tmp_path))
    assert "1 records were fetched but could not be parsed" in prose


# -- what moved: the demo's changes are labelled as the demo's ------------------


def test_the_demo_stores_changes_are_attributed_to_it_in_the_same_sentence(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    """A reader who took the demo's daily price rotation for movement on somebody
    else's live site would have been misled by this page, so the number and the
    attribution are never in two different sentences."""
    prose = _prose(_page(results, run_dir, tmp_path))
    demo_sentence = next(
        line for line in prose.split("<li>") if line.startswith("the demo store")
    )
    assert "rotate daily by a seeded rule" in demo_sentence
    assert "accounts for 7 of them" in demo_sentence


def test_a_source_that_did_not_move_says_so_rather_than_being_left_out(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    assert "quotes.toscrape.com: nothing moved since the previous snapshot." in _prose(
        _page(results, run_dir, tmp_path)
    )


def test_a_run_with_no_changes_at_all_states_it(results: Path, tmp_path: Path) -> None:
    _write_run(
        tmp_path / "run",
        stats={"demo": _source_stats(records=40, pages=4, requests=5)},
        changes={"demo": {"added": 0, "removed": 0, "changed": 0, "changes": []}},
    )
    prose = _prose(_page(results, tmp_path / "run", tmp_path))
    assert "Nothing moved between the last two snapshots." in prose


def test_a_source_with_no_comparison_is_not_reported_as_unchanged(
    results: Path, tmp_path: Path
) -> None:
    """`change-report.json` carries a changeset only for a source that was diffed.
    "0 changes" and "never compared" are different statements, and the second one
    must not be published as the first."""
    _write_run(
        tmp_path / "run",
        stats={"demo": _source_stats(records=40, pages=4, requests=5)},
        changes={},
    )
    prose = _prose(_page(results, tmp_path / "run", tmp_path))
    assert "this run produced no comparison for it" in prose
    assert "nothing moved since the previous snapshot" not in prose


def test_the_change_report_is_published_beside_the_page(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    out = tmp_path / "site"
    page = _page(results, run_dir, tmp_path, out_dir=out)
    assert (out / "changes.html").is_file() and (out / "changes.json").is_file()
    assert 'href="changes.html"' in page and 'href="changes.json"' in page


def test_a_run_without_the_html_report_does_not_link_it(
    results: Path, tmp_path: Path
) -> None:
    _write_run(tmp_path / "run", html=False)
    page = _page(results, tmp_path / "run", tmp_path)
    assert 'href="changes.html"' not in page


# -- the data files: linked exactly, and only, when they are published ----------


def test_the_data_files_are_published_and_linked_with_their_sizes(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    data = tmp_path / "exports"
    data.mkdir()
    (data / "books.csv").write_bytes(b"x" * 2048)
    (data / "demo.json").write_bytes(b"y" * 100)
    out = tmp_path / "site"
    page = _page(results, run_dir, tmp_path, data_dir=data, out_dir=out)
    assert (out / "data" / "books.csv").read_bytes() == b"x" * 2048
    assert 'href="data/books.csv"' in page and "(2 KB)" in page
    assert 'href="data/demo.json"' in page and "(100 bytes)" in page
    assert 'href="data/quotes.json"' not in page


def test_a_file_nobody_asked_to_publish_is_not_published(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    """The page links exactly what it copied and copies exactly what it links; a
    stray file in the exports directory is neither."""
    data = tmp_path / "exports"
    data.mkdir()
    (data / "books.csv").write_bytes(b"x" * 2048)
    (data / "secrets.env").write_text("TOKEN=hunter2", encoding="utf-8")
    out = tmp_path / "site"
    page = _page(results, run_dir, tmp_path, data_dir=data, out_dir=out)
    assert not (out / "data" / "secrets.env").exists()
    assert "secrets.env" not in page


def test_the_published_database_is_named_as_part_of_the_published_data(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    """It is what the next run pulls back to have a yesterday to diff against, so
    the page says what it is rather than listing a file with an odd extension."""
    data = tmp_path / "exports"
    data.mkdir()
    (data / "scrapewatch.sqlite3").write_bytes(b"s" * 4096)
    prose = _prose(_page(results, run_dir, tmp_path, data_dir=data))
    assert 'href="data/scrapewatch.sqlite3"' in prose
    assert "the same SQLite file the next run pulls back" in prose


def test_a_page_without_data_files_does_not_offer_them(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    page = _page(results, run_dir, tmp_path)
    assert 'href="data/' not in page
    assert 'href="#data"' not in page


# -- the recording and the trace -----------------------------------------------


def test_a_missing_recording_is_said_in_words_not_shown_as_a_black_box(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    page = _page(results, run_dir, tmp_path, media_dir=tmp_path / "nothing")
    assert "<video" not in page
    assert "recording is produced by a browser" in page


def test_a_recording_that_exists_is_published_beside_the_page(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    (media / "scroll.webm").write_bytes(b"x" * 20_000)
    out = tmp_path / "site"
    page = _page(results, run_dir, tmp_path, media_dir=media, out_dir=out)
    assert "<video" in page
    assert (out / "media" / "scroll.webm").read_bytes() == b"x" * 20_000


def test_a_truncated_recording_is_not_published(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    """10 KiB is the floor: below it the file is a header, not a video."""
    media = tmp_path / "media"
    media.mkdir()
    (media / "scroll.webm").write_bytes(b"x" * 10_240)
    assert "<video" not in _page(results, run_dir, tmp_path, media_dir=media)


def test_a_trace_that_exists_is_published_and_linked_both_ways(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    """The trace is offered twice on purpose: through the hosted viewer, which
    depends on this site allowing a cross-origin read, and as a file, which depends
    on nothing. A reader must never be left with only the link that can stop
    working for a reason outside this project."""
    media = tmp_path / "media"
    media.mkdir()
    (media / "scroll-trace.zip").write_bytes(b"x" * 20_000)
    out = tmp_path / "site"
    page = _page(results, run_dir, tmp_path, media_dir=media, out_dir=out)
    assert "trace.playwright.dev/?trace=" in page
    assert 'href="media/scroll-trace.zip"' in page
    assert (out / "media" / "scroll-trace.zip").read_bytes() == b"x" * 20_000


def test_a_missing_trace_leaves_no_link_to_a_file_that_is_not_there(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    page = _page(results, run_dir, tmp_path, media_dir=tmp_path / "nothing")
    assert "scroll-trace.zip" not in page
    assert "trace.playwright.dev" not in page


def test_the_linked_trace_and_the_published_file_are_the_same_one() -> None:
    """The viewer link has to be absolute, so it is the one link on the page that
    cannot be checked by following it relative to the file beside it. It is built
    from the same constant the file is published under, and this is what keeps the
    two spellings from drifting."""
    from showcase.build import PUBLISHED_TRACE_URL, TRACE_NAME, TRACE_VIEWER_URL

    assert PUBLISHED_TRACE_URL.endswith(f"/media/{TRACE_NAME}")
    assert TRACE_VIEWER_URL == f"https://trace.playwright.dev/?trace={PUBLISHED_TRACE_URL}"


def test_an_artefact_already_in_place_is_used_and_left_alone(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    """The publish step may copy artefacts in before this runs — the data files
    arrive exactly that way."""
    out = tmp_path / "site"
    (out / "media").mkdir(parents=True)
    (out / "media" / "scroll.webm").write_bytes(b"already here")
    page = _page(results, run_dir, tmp_path, media_dir=tmp_path / "nothing", out_dir=out)
    assert "<video" in page
    assert (out / "media" / "scroll.webm").read_bytes() == b"already here"


# -- the diagrams, which arrive from their own step ----------------------------


def test_diagrams_that_exist_are_published_beside_the_page(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    for name in ("architecture", "pipeline"):
        (assets / f"{name}.svg").write_text(f"<svg>{name}</svg>", encoding="utf-8")
    out = tmp_path / "site"
    page = _page(results, run_dir, tmp_path, assets_dir=assets, out_dir=out)
    assert 'src="assets/architecture.svg"' in page
    assert 'src="assets/pipeline.svg"' in page
    assert (out / "assets" / "pipeline.svg").read_text(encoding="utf-8")


def test_missing_diagrams_are_said_in_words_not_shown_as_broken_images(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    page = _page(results, run_dir, tmp_path, assets_dir=tmp_path / "nothing")
    assert 'src="assets/' not in page
    assert "diagrams ship with the published build" in page


@pytest.mark.parametrize(
    "present, absent",
    [("architecture", "pipeline"), ("pipeline", "architecture")],
)
def test_one_diagram_can_arrive_without_the_other(
    results: Path, run_dir: Path, tmp_path: Path, present: str, absent: str
) -> None:
    """The two figures are drawn by the same step but are two files, and a publish
    can carry one without the other. The page must then show the one it has and say
    nothing about the one it does not, rather than falling back to the note that
    claims neither shipped."""
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / f"{present}.svg").write_text(f"<svg>{present}</svg>", encoding="utf-8")
    out = tmp_path / "site"
    page = _page(results, run_dir, tmp_path, assets_dir=assets, out_dir=out)
    assert f'src="assets/{present}.svg"' in page
    assert f'src="assets/{absent}.svg"' not in page
    assert "diagrams ship with the published build" not in page
    assert not (out / "assets" / f"{absent}.svg").exists()


# -- the tests of the same run --------------------------------------------------


def test_counts_by_status(results: Path) -> None:
    _result(results, "f", "failed", "tests.live.test_quotes_live", ["live"])
    summary = summarise(results)
    assert (summary.suite.total, summary.suite.passed, summary.suite.failed) == (6, 5, 1)


def test_counts_by_marker(results: Path) -> None:
    assert summarise(results).by_marker == {
        "unit": 1,
        "parsers": 1,
        "integration": 1,
        "e2e": 1,
        "live": 1,
    }


def test_empty_results_are_an_error_not_a_zero(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no Allure results"):
        summarise(tmp_path)


def test_the_split_by_marker_is_on_the_page(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    prose = _prose(_page(results, run_dir, tmp_path))
    assert "5 checks ran in the same pipeline" in prose
    assert "1 of pure logic, 1 of parsing saved pages" in prose
    assert "1 against the real practice sites" in prose


def test_the_suite_figures_are_on_the_page(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    """A page that holds a number and never states it is a number nobody checked."""
    _result(results, "f", "failed", "tests.live.test_quotes_live", ["live"])
    prose = _prose(_page(results, run_dir, tmp_path))
    assert "6 checks ran in the same pipeline" in prose
    assert "5 of them passed" in prose
    assert "1 did not" in prose


def test_a_clean_run_does_not_announce_failures(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    prose = _prose(_page(results, run_dir, tmp_path))
    assert "5 of them passed" in prose
    assert "1 did not," not in prose


def test_a_red_live_check_is_stated_beside_the_report_button(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    """A live failure is the drift signal this project exists to produce. Hiding it
    to keep the page green would remove the one thing the page is evidence of."""
    _result(results, "f", "failed", "tests.live.test_quotes_live", ["live"])
    prose = _prose(_page(results, run_dir, tmp_path))
    assert "1 of the 2 checks against the real practice sites failed in this run" in prose
    assert "still collected and still published" in prose


def test_a_green_live_run_says_the_markup_still_matches(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    """One live check, so the sentence written for a plural would read as "The 1
    checks" — each count gets prose that is true of it."""
    prose = _prose(_page(results, run_dir, tmp_path))
    assert "The one check against the real practice sites passed in this run" in prose
    assert "did not pass in this run" not in prose


def test_more_than_one_green_live_check_is_described_in_the_plural(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    """The guard above must not be a way of always saying "the one check"."""
    _result(results, "f", "passed", "tests.live.test_quotes_live", ["live"])
    prose = _prose(_page(results, run_dir, tmp_path))
    assert "All 2 checks against the real practice sites passed in this run" in prose
    assert "The one check" not in prose


def test_results_without_any_live_check_do_not_claim_one(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    _result(results, "a", "passed", "tests.unit.test_diff", ["unit"])
    _write_run(tmp_path / "run")
    prose = _prose(_page(results, tmp_path / "run", tmp_path))
    assert "No checks against the real practice sites are counted" in prose
    assert "did not pass in this run" not in prose


def test_a_skipped_live_check_is_not_reported_as_a_red_one(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    """A live check that did not run says nothing about the site. Counting it as
    "did not pass" would announce drift on a night when nothing drifted — and the
    whole point of the live leg is that a red one means something."""
    _result(results, "f", "skipped", "tests.live.test_quotes_live", ["live"])
    summary = summarise(results)
    assert (summary.live.failed, summary.live.skipped, summary.live.not_passed) == (0, 1, 1)
    prose = _prose(_page(results, run_dir, tmp_path))
    assert "failed in this run" not in prose
    assert "1 of the 2 checks against the real practice sites did not run" in prose
    assert "none of the ones that did failed" in prose


def test_the_one_live_check_that_did_not_run_is_said_in_the_singular(
    run_dir: Path, tmp_path: Path
) -> None:
    """The sentence written for a plural reads as nonsense over a 1 — "1 of the 1
    checks did not run" — and a publication whose only live check was skipped is
    exactly the night a reader needs told plainly."""
    results = tmp_path / "results"
    results.mkdir()
    _result(results, "a", "passed", "tests.unit.test_diff", ["unit"])
    _result(results, "e", "skipped", "tests.live.test_books_live", ["live"])
    prose = _prose(_page(results, run_dir, tmp_path))
    assert "The one check against the real practice sites did not run" in prose
    assert "1 of the 1 checks" not in prose
    assert "failed in this run" not in prose


def test_a_broken_live_check_is_red_like_a_failed_one(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    """`broken` is what Allure records for a connection error or a Playwright
    timeout, which is most of what a live check dies of."""
    _result(results, "f", "broken", "tests.live.test_books_live", ["live"])
    assert summarise(results).live.failed == 1
    assert "failed in this run" in _prose(_page(results, run_dir, tmp_path))


def test_a_skipped_check_is_counted_and_said_on_the_page(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    _result(results, "f", "skipped", "tests.live.test_quotes_live", ["live"])
    summary = summarise(results)
    assert (summary.suite.total, summary.suite.skipped) == (6, 1)
    assert (
        summary.suite.passed + summary.suite.failed + summary.suite.skipped + summary.suite.unknown
    ) == summary.suite.total
    assert "1 did not run, which is the suite declining" in _prose(
        _page(results, run_dir, tmp_path)
    )


def test_a_broken_result_is_counted_with_the_failures(results: Path) -> None:
    """`broken` is what Allure records when a test died of an exception rather than
    of an assertion, which is every Playwright timeout and every connection error.
    If it did not reach the failed figure, a run against a site that was down would
    publish a green page beside a report full of red."""
    _result(results, "f", "broken", "tests.live.test_quotes_live", ["live"])
    summary = summarise(results)
    assert (summary.suite.total, summary.suite.failed) == (6, 1)
    assert summary.live.not_passed == 1


def test_a_result_with_no_verdict_is_counted_as_unknown(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    _result(results, "f", "unknown", "tests.e2e.test_demo_store", ["e2e"])
    summary = summarise(results)
    assert summary.suite.unknown == 1
    assert (
        summary.suite.passed + summary.suite.failed + summary.suite.skipped + summary.suite.unknown
    ) == summary.suite.total
    assert "finished with no status the report could read" in _prose(
        _page(results, run_dir, tmp_path)
    )


def test_a_status_nobody_planned_for_stops_the_build(results: Path) -> None:
    _result(results, "f", "pending", "tests.unit.test_diff", ["unit"])
    with pytest.raises(ValueError, match="unrecognised Allure status"):
        summarise(results)


def test_a_test_with_two_markers_stops_the_build_instead_of_being_counted_twice(
    results: Path,
) -> None:
    _result(results, "f", "passed", "tests.unit.test_diff", ["unit", "e2e"])
    with pytest.raises(ValueError, match="more than one marker"):
        summarise(results)


def test_a_test_that_lost_its_marker_stops_the_build(results: Path) -> None:
    """No leg of this pipeline selects an unmarked test — `-m "unit or parsers or
    integration"`, `-m e2e` and `-m live` between them cover the whole suite — so it
    would stop running with nothing failing anywhere."""
    _result(results, "f", "passed", "tests.integration.test_storage", [])
    with pytest.raises(ValueError, match="tests/integration"):
        summarise(results)


def test_the_page_states_the_retention_the_cli_applies() -> None:
    """The page tells a reader how much history the published database holds. The
    number that actually holds it is the CLI's `--keep-snapshots` default, and the
    page's copy is a constant in the builder — pinned here, because a page that
    promises thirty nights of a file that keeps seven is a lie nothing else
    catches."""
    from scrapewatch.cli import DEFAULT_KEEP_SNAPSHOTS
    from showcase.build import RETENTION_NIGHTS

    assert RETENTION_NIGHTS == DEFAULT_KEEP_SNAPSHOTS


def test_the_retention_is_stated_on_the_page(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    from showcase.build import RETENTION_NIGHTS

    data = tmp_path / "exports"
    data.mkdir()
    (data / "scrapewatch.sqlite3").write_bytes(b"s" * 4096)
    prose = _prose(_page(results, run_dir, tmp_path, data_dir=data))
    assert f"It keeps the last {RETENTION_NIGHTS} nights per source" in prose


def test_the_builder_knows_every_marker_the_suite_registers() -> None:
    """A marker added to `pyproject.toml` and not to this module would vanish from
    the split on the page, silently. Fail here instead, where the fix is one line
    and the message says which name is new."""
    import tomllib

    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    registered = {
        entry.split(":", 1)[0].strip()
        for entry in config["tool"]["pytest"]["ini_options"]["markers"]
    }
    assert registered, "no markers registered; the extraction is reading the wrong key"
    assert registered == set(MARKERS)


# -- a rerun is one test, not two ----------------------------------------------


@pytest.fixture
def results_with_a_rerun(results: Path) -> Path:
    """Result `d` failed, was rerun, and passed the second time."""
    _result(
        results, "d", "passed", "tests.e2e.test_browser_engine", ["e2e"],
        attempt="-retry", history="d-history", stop=1_758_400_060_000,
    )
    _result(
        results, "d", "failed", "tests.e2e.test_browser_engine", ["e2e"],
        history="d-history",
    )
    return results


def test_a_rerun_is_one_test_not_two(results_with_a_rerun: Path) -> None:
    summary = summarise(results_with_a_rerun)
    assert (summary.suite.total, summary.suite.passed, summary.suite.failed) == (5, 5, 0)


def test_the_marker_split_counts_a_rerun_once(results_with_a_rerun: Path) -> None:
    assert summarise(results_with_a_rerun).by_marker["e2e"] == 1


def test_the_verdict_is_the_attempt_the_report_shows(results_with_a_rerun: Path) -> None:
    """Allure counts the last attempt; a page that disagreed would be wrong."""
    summary = summarise(results_with_a_rerun)
    assert (summary.suite.passed, summary.suite.failed) == (5, 0)


def test_a_test_that_only_passed_on_a_rerun_is_named_on_the_page(
    results_with_a_rerun: Path, run_dir: Path, tmp_path: Path
) -> None:
    assert summarise(results_with_a_rerun).suite.flaky == 1
    assert "1 passed only on a second attempt" in _prose(
        _page(results_with_a_rerun, run_dir, tmp_path)
    )


def test_a_clean_run_says_nothing_about_flakes(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    assert summarise(results).suite.flaky == 0
    assert "second attempt" not in _prose(_page(results, run_dir, tmp_path))


def test_attempts_group_by_name_and_parameters_when_there_is_no_history_id(
    tmp_path: Path,
) -> None:
    results = tmp_path / "results"
    results.mkdir()
    for attempt, status in (("-1", "failed"), ("-2", "passed")):
        _result(
            results, "x", status, "tests.e2e.test_demo_store", ["e2e"],
            attempt=attempt, stop=1_758_400_000_000 + int(attempt[-1]),
            parameters={"page": "1"},
        )
    summary = summarise(results)
    assert (summary.suite.total, summary.suite.passed) == (1, 1)


def test_two_parameters_of_one_test_stay_two_tests(tmp_path: Path) -> None:
    """The grouping must not swallow a parametrised case into its sibling."""
    results = tmp_path / "results"
    results.mkdir()
    for page_number in ("1", "2"):
        _result(
            results, "x", "passed", "tests.e2e.test_demo_store", ["e2e"],
            attempt=f"-{page_number}", parameters={"page": page_number},
        )
    assert summarise(results).suite.total == 2


# -- nothing that reaches the template is markup --------------------------------


def test_no_placeholder_survives_the_build(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    assert "{{" not in _page(results, run_dir, tmp_path)


def test_a_value_cannot_close_an_attribute_and_open_a_script(
    results: Path, tmp_path: Path
) -> None:
    """The skip reason is whichever message the failure carried — somebody else's
    error text, in practice — and the revision and run URL arrive from CI."""
    _write_run(
        tmp_path / "run",
        stats={
            "books": _source_stats(skipped=True, reason='<script>alert("reason")</script>'),
            "demo": _source_stats(records=40, pages=4, requests=5),
        },
        changes={"demo": {"added": 0, "removed": 0, "changed": 1, "changes": []}},
    )
    page = _page(
        results,
        tmp_path / "run",
        tmp_path,
        revision='abc1234"><script>alert(1)</script>',
        run_url='https://example.com/"><script>alert(2)</script>',
    )
    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_a_value_that_looks_like_a_placeholder_is_not_read_as_one(
    results: Path, tmp_path: Path
) -> None:
    """Data is substituted in, never substituted into: a skip reason quoting
    `{{RECORDS}}` must reach the page as those characters."""
    _write_run(
        tmp_path / "run",
        stats={
            "books": _source_stats(skipped=True, reason="the site answered {{RECORDS}}"),
            "demo": _source_stats(records=40, pages=4, requests=5),
        },
        changes={"demo": {"added": 0, "removed": 0, "changed": 1, "changes": []}},
    )
    assert "the site answered {{RECORDS}}" in _page(results, tmp_path / "run", tmp_path)


def test_a_placeholder_the_build_does_not_produce_stops_the_build() -> None:
    """A mistyped name in the template is a visible `{{RECORD}}` on a published
    page — the kind of thing nobody notices until somebody else does."""
    with pytest.raises(ValueError, match="RECORD"):
        _fill("<p>{{RECORD}}</p>", {"RECORDS": "1"})


def test_a_run_url_that_is_not_a_link_is_dropped_rather_than_rendered(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    page = _page(results, run_dir, tmp_path, run_url="javascript:alert(1)")
    assert "javascript:" not in page
    assert "that produced this page is public" not in page  # the whole block is gone


def test_a_missing_run_url_leaves_no_empty_link(
    results: Path, run_dir: Path, tmp_path: Path
) -> None:
    assert 'href=""' not in _page(results, run_dir, tmp_path)
