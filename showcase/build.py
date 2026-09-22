"""Build the showcase page from the artefacts of a single nightly run.

The page leads with what was collected, so the figures it leads with come from
`run-stats.json` and `change-report.json` — the two files `run_sources` writes at
the end of every run. The test figures come from the Allure results of the same
run, merged from every job that produced any. Nothing here is written by hand,
because a page that disagrees with its own data is worse than no page.

Three artefacts are optional: the recording of the demo store's infinite scroll,
the Playwright trace of the same scroll, and the two diagrams. Each is produced
by a different step and may simply not be there — the recording needs a browser,
the diagrams are drawn separately. When one is missing the page says so in words,
rather than offering a broken image or a black video frame; a showcase that shows
a broken box has already lost the reader.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

#: The suite's markers, one per test. A test says which door it goes through —
#: pure logic, a saved page, SQLite, a browser, or the real sites — and it says it
#: once: `pyproject.toml` registers exactly these five and
#: `test_the_builder_knows_every_marker_the_suite_registers` fails if that list and
#: this one ever part company.
MARKERS = ("unit", "parsers", "integration", "e2e", "live")

#: The marker whose failures are a statement about the practice sites rather than
#: about this project. A red one is the drift signal the project exists to produce,
#: so the page states it beside the report button instead of hiding it.
LIVE_MARKER = "live"

#: How the page names each source. The demo store is deliberately not given a
#: domain: it ships inside this repository, and the page never lets a reader think
#: its daily price moves came from somebody else's site.
SOURCE_LABELS = {
    "books": "books.toscrape.com",
    "quotes": "quotes.toscrape.com",
    "demo": "the demo store",
}

#: The one source whose changes must always be attributed to the demo in the same
#: sentence that gives their number.
DEMO_SOURCE = "demo"

#: What the published `data/` directory may contain, in the order the page lists
#: it, and what each file is. A name that is not on this list is not published: the
#: page links exactly the files it copied, and copies exactly the files it links.
DATA_FILES: tuple[tuple[str, str], ...] = (
    ("books.csv", "every book collected in this run, one row per book"),
    ("books.json", "the same books as JSON"),
    ("quotes.json", "every quote collected in this run"),
    ("demo.json", "the demo store's catalogue as it stood during this run"),
    (
        "scrapewatch.sqlite3",
        "the database itself: every snapshot this project has taken, which is what "
        "this run's diff was made against and what the next run will compare with",
    ),
)

#: The published database, by name. It is part of the published data and the page
#: says so; it is also what `scripts/restore-db.sh` pulls back before the next run,
#: which is the only reason a nightly diff has a yesterday at all.
DATABASE_FILE = "scrapewatch.sqlite3"

#: How many snapshots per source the published database keeps, which the page
#: states as part of describing what that file is. The number itself lives in
#: `scrapewatch.cli.DEFAULT_KEEP_SNAPSHOTS`, which is what actually applies it;
#: this is the page's copy, and `test_the_page_states_the_retention_the_cli_applies`
#: fails if the two ever part company. Importing the CLI here instead would drag
#: typer, uvicorn and Playwright into a build step that otherwise needs nothing but
#: the standard library.
RETENTION_NIGHTS = 30

#: A recording smaller than this is a truncated file, not a video.
MIN_VIDEO_BYTES = 10 * 1024

#: A trace zip below this is a header and no data.
MIN_TRACE_BYTES = 10 * 1024

#: The recording and the trace, by name. `scrapewatch record-scroll` writes exactly
#: these two files, so there is no selection to make here and no second, separate
#: rule in the publish script that could drift out of step with this one: the script
#: hands over the directory, this module publishes what it finds under these names.
VIDEO_NAME = "scroll.webm"
TRACE_NAME = "scroll-trace.zip"

#: Where the site is served from, and therefore the address the trace viewer has to
#: be handed to fetch the trace. The viewer is a static page at trace.playwright.dev
#: that reads the zip over HTTP from wherever it is hosted, so this one link cannot
#: be relative the way every other link on the page is. GitHub Pages answers with
#: `access-control-allow-origin: *`, which is what makes the cross-origin read work
#: at all; the page also offers the file for download, so a reader is never left
#: with only a link that depends on somebody else's CORS policy.
PAGE_URL = "https://wolfgung.github.io/Web-Scraping-Automation-Framework/"
REPOSITORY_URL = "https://github.com/WolfGung/Web-Scraping-Automation-Framework"
PUBLISHED_TRACE_URL = f"{PAGE_URL}media/{TRACE_NAME}"
TRACE_VIEWER_URL = f"https://trace.playwright.dev/?trace={PUBLISHED_TRACE_URL}"

ASSETS_DIR = Path(__file__).parent / "assets"

#: The diagrams the page shows when they are there. Drawn by their own step, so a
#: publication can carry one, both or neither.
DIAGRAMS = ("architecture", "pipeline")


# -- what was collected ---------------------------------------------------------


@dataclass
class SourceRun:
    """One source's night: what it cost to collect, and what moved since last time."""

    name: str
    records: int = 0
    pages: int = 0
    requests: int = 0
    retries: int = 0
    seconds: float = 0.0
    parse_errors: int = 0
    skipped: bool = False
    reason: str = ""
    added: int = 0
    removed: int = 0
    changed: int = 0
    #: Whether `change-report.json` carried a changeset for this source at all. A
    #: source that was skipped, or one whose snapshot could not be diffed, has no
    #: changeset — which is a different statement from "nothing changed", and the
    #: page makes it in different words.
    compared: bool = False

    @property
    def label(self) -> str:
        return SOURCE_LABELS.get(self.name, self.name)

    @property
    def changes(self) -> int:
        return self.added + self.removed + self.changed


@dataclass
class Collection:
    """What one run collected, source by source, with the totals the page leads with."""

    sources: list[SourceRun] = field(default_factory=list)
    collected_at: str = ""

    @property
    def records(self) -> int:
        return sum(source.records for source in self.sources)

    @property
    def pages(self) -> int:
        return sum(source.pages for source in self.sources)

    @property
    def requests(self) -> int:
        return sum(source.requests for source in self.sources)

    @property
    def retries(self) -> int:
        return sum(source.retries for source in self.sources)

    @property
    def parse_errors(self) -> int:
        return sum(source.parse_errors for source in self.sources)

    @property
    def seconds(self) -> float:
        return sum(source.seconds for source in self.sources)

    @property
    def changes(self) -> int:
        return sum(source.changes for source in self.sources)

    @property
    def skipped(self) -> list[SourceRun]:
        return [source for source in self.sources if source.skipped]


def _load(path: Path, what: str) -> dict:
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"no {what} at {path}")
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{what} at {path} is not readable JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise ValueError(f"{what} at {path} is not an object")
    return body


def read_collection(stats_path: Path, changes_path: Path) -> Collection:
    """Read one run's `run-stats.json` and `change-report.json` into one object.

    A run with no sources at all is an error rather than a page of zeroes, for the
    same reason an empty Allure results directory is: a publication that leads with
    "0 records collected" over a run that never ran describes nothing, and the
    failure is much easier to read here than on the published page.
    """
    stats = _load(stats_path, "run statistics")
    changes = _load(changes_path, "change report")

    per_source = stats.get("sources")
    if not isinstance(per_source, dict) or not per_source:
        raise ValueError(f"no sources in {stats_path}")
    changed_per_source = changes.get("sources")
    if not isinstance(changed_per_source, dict):
        changed_per_source = {}

    collection = Collection(collected_at=str(stats.get("generated_at", "")))
    for name, body in per_source.items():
        if not isinstance(body, dict):
            raise ValueError(f"{name} in {stats_path} is not an object")
        changeset = changed_per_source.get(name)
        changeset = changeset if isinstance(changeset, dict) else None
        collection.sources.append(
            SourceRun(
                name=str(name),
                records=int(body.get("records", 0) or 0),
                pages=int(body.get("pages", 0) or 0),
                requests=int(body.get("requests", 0) or 0),
                retries=int(body.get("retries", 0) or 0),
                seconds=float(body.get("seconds", 0) or 0),
                parse_errors=int(body.get("parse_errors", 0) or 0),
                skipped=bool(body.get("skipped", False)),
                reason=str(body.get("reason") or ""),
                added=int((changeset or {}).get("added", 0) or 0),
                removed=int((changeset or {}).get("removed", 0) or 0),
                changed=int((changeset or {}).get("changed", 0) or 0),
                compared=changeset is not None,
            )
        )
    return collection


# -- what the run's tests did ---------------------------------------------------


@dataclass
class Tally:
    """What happened to one group of results."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    unknown: int = 0
    flaky: int = 0

    @property
    def not_passed(self) -> int:
        """Everything that did not come back green, however it got there."""
        return self.total - self.passed

    def record(self, status: str, flaky: bool, where: str) -> None:
        self.total += 1
        if flaky:
            self.flaky += 1
        if status == "passed":
            self.passed += 1
        elif status in {"failed", "broken"}:
            self.failed += 1
        elif status == "skipped":
            self.skipped += 1
        elif status == "unknown":
            self.unknown += 1
        else:
            # Every counted result has to land in a bucket the page can show.
            # Silently dropping one produces a page whose own totals do not add up,
            # which is the single thing this page cannot afford.
            raise ValueError(f"unrecognised Allure status {status!r} in {where}")


@dataclass
class RunSummary:
    """One run's tests: the whole suite, the live checks apart, and the split by marker."""

    suite: Tally = field(default_factory=Tally)
    live: Tally = field(default_factory=Tally)
    by_marker: dict[str, int] = field(default_factory=dict)
    finished: datetime = datetime.fromtimestamp(0, tz=UTC)


def _identity(body: dict, path: Path) -> str:
    """What makes two result files two attempts at the same test.

    ``historyId`` is Allure's own answer and is what its report groups on, so it is
    used whenever the file carries one. A result written by something that does not
    set it still has to be grouped, so the fallback is the pair the history id is
    derived from anyway: the fully qualified name and the parameters of the case.
    The file name is the last resort, which makes an unidentifiable result its own
    test and keeps it counted.
    """
    history = body.get("historyId")
    if history:
        return f"history:{history}"
    parameters = sorted(
        (str(p.get("name")), str(p.get("value")))
        for p in body.get("parameters", [])
        if isinstance(p, dict)
    )
    name = body.get("fullName") or body.get("name")
    if not name:
        return f"file:{path.name}"
    return f"name:{name}:{parameters}"


def _published_attempt(attempts: list[tuple[Path, dict]]) -> tuple[Path, dict]:
    """The attempt Allure shows for a test, out of all the attempts it made.

    A rerun writes a second result file for the same test, and the report counts the
    last attempt while keeping the earlier ones as retries. The page has to agree
    with the report it sits beside, so it reads the same attempt: the newest by the
    times the run recorded, with the file name breaking a tie so the choice is
    stable across machines.
    """
    return max(
        attempts,
        key=lambda item: (
            int(item[1].get("stop") or 0),
            int(item[1].get("start") or 0),
            item[0].name,
        ),
    )


def summarise(results_dir: Path) -> RunSummary:
    """Count one run's tests, with each test counted once however often it ran.

    Allure writes one result file per *attempt*, so counting files would report
    three tests where two ran and would put a failure on the page beside a report
    that shows none. The results published here are merged from three CI jobs and
    `pytest-rerunfailures` is installed, so a second attempt at the same case is not
    hypothetical: the files are grouped into tests first, and every figure the page
    states is counted from the grouped tests. A test that needed a second attempt to
    pass is counted as the pass it ended on and named separately as a flake, because
    the run that hides its retries is the one nobody can trust.
    """
    files = sorted(Path(results_dir).glob("*-result.json"))
    if not files:
        raise ValueError(f"no Allure results in {results_dir}")

    attempts: dict[str, list[tuple[Path, dict]]] = {}
    stops: list[int] = []
    for path in files:
        body = json.loads(path.read_text(encoding="utf-8"))
        attempts.setdefault(_identity(body, path), []).append((path, body))
        if body.get("stop"):
            stops.append(int(body["stop"]))

    summary = RunSummary(by_marker={marker: 0 for marker in MARKERS})
    for tries in attempts.values():
        path, body = _published_attempt(tries)
        status = body.get("status", "unknown")
        flaky = status == "passed" and any(
            other.get("status") != "passed" for _, other in tries if other is not body
        )
        tags = {item["value"] for item in body.get("labels", []) if item.get("name") == "tag"}

        markers = [marker for marker in MARKERS if marker in tags]
        if len(markers) > 1:
            # A test says which door it goes through once. Two markers means the
            # selection this pipeline runs on (`-m "unit or parsers or integration"`,
            # `-m e2e`, `-m live`) no longer means what the page says it means, and
            # the same test would be counted in two of the figures below.
            raise ValueError(
                f"{body.get('name', path.name)} carries more than one marker "
                f"({', '.join(markers)}); a test belongs to one"
            )
        if not markers:
            # A test under tests/<marker>/ that lost its marker would not be
            # selected by any leg of this pipeline, so it would silently stop
            # running while the page kept publishing a total that looked fine.
            qualifier = (body.get("fullName") or body.get("name") or path.name).split("#", 1)[0]
            stray = next(
                (
                    marker
                    for marker in MARKERS
                    if qualifier == f"tests.{marker}" or qualifier.startswith(f"tests.{marker}.")
                ),
                None,
            )
            if stray is not None:
                raise ValueError(
                    f"{body.get('name', path.name)} sits under tests/{stray} "
                    f"({qualifier}) but carries no marker; no leg of the pipeline "
                    f"selects it, so it would stop running without anything failing"
                )
        for marker in markers:
            summary.by_marker[marker] += 1

        summary.suite.record(status, flaky, where=path.name)
        if LIVE_MARKER in markers:
            summary.live.record(status, flaky, where=path.name)

    if stops:
        summary.finished = datetime.fromtimestamp(max(stops) / 1000, tz=UTC)
    return summary


# -- rendering ------------------------------------------------------------------


def _text(value: object) -> str:
    """Escape a value for text or for an attribute's contents."""
    return html.escape(str(value), quote=True)


def _safe_url(url: str) -> str:
    """An escaped http(s) or relative URL; empty for anything else.

    The run URL arrives from the environment, and the page puts it in an ``href``.
    Escaping alone would still let ``javascript:`` through, so the scheme is checked
    as well and an unusable value is treated as no value: the block that links the
    run is dropped rather than rendered broken.
    """
    candidate = url.strip()
    if not candidate:
        return ""
    parsed = urlparse(candidate)
    if parsed.scheme and parsed.scheme.lower() not in {"http", "https"}:
        return ""
    if candidate.startswith("//"):  # protocol-relative: not ours to resolve
        return ""
    return html.escape(candidate, quote=True)


def _human_bytes(size: int) -> str:
    """A file size a reader can judge at a glance, never more precise than it is."""
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} bytes"


def _seconds(value: float) -> str:
    """A duration with enough precision to be true of itself.

    A tenth of a second is the right resolution for a scrape of a real site, and
    the wrong one for the demo store next door: "0.0 seconds of fetching" is a
    number that reads as nothing happening.
    """
    return f"{value:.1f}" if value >= 1 else f"{value:.2f}"


def _when(iso: str) -> str:
    """An ISO timestamp as a sentence, or the raw value when it is not one."""
    try:
        return datetime.fromisoformat(iso).strftime("%d %B %Y, %H:%M UTC")
    except ValueError:
        return iso


def _place(source: Path | None, target: Path) -> bool:
    """Put an optional artefact beside the page. True when the page can use it.

    A file already sitting at the target counts: the publish step may have put it
    there before calling this module, and the data files are published exactly that
    way — copied into the site's own `data/` directory and then found here.
    """
    if target.is_file() and target.stat().st_size > 0:
        return True
    if source is None or not Path(source).is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return True


def _usable(path: Path, floor: int) -> Path | None:
    """A file big enough to be the thing it claims to be, or nothing."""
    return path if path.is_file() and path.stat().st_size > floor else None


def _resolve(page: str, name: str, keep: bool) -> str:
    """Keep or drop one conditional block of the template.

    Blocks are written as ``<!--[if name]-->...<!--[else name]-->...<!--[end
    name]-->``; the ``else`` half is optional.
    """
    pattern = re.compile(
        rf"<!--\[if {name}\]-->(?P<then>.*?)"
        rf"(?:<!--\[else {name}\]-->(?P<otherwise>.*?))?"
        rf"<!--\[end {name}\]-->",
        re.DOTALL,
    )

    def _swap(match: re.Match[str]) -> str:
        chosen = match.group("then") if keep else (match.group("otherwise") or "")
        return chosen.strip("\n")

    return pattern.sub(_swap, page)


def _fill(page: str, values: dict[str, str]) -> str:
    """Substitute every ``{{NAME}}`` in one pass, and refuse an unknown one.

    One pass, not a replacement per key: a value that itself contained ``{{PAGES}}``
    — a skip reason quoting somebody else's error message, say — would otherwise be
    substituted into on the next iteration, so data would become template. Every
    value here is escaped before it arrives; this is what stops it being read as
    markup of a different kind.

    An unknown name stops the build rather than being left on the page: a mistyped
    placeholder is a visible ``{{RECORD}}`` on a published page, which is exactly
    the kind of thing nobody notices until somebody else does.
    """

    def _swap(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise ValueError(f"the template asks for {{{{{name}}}}}, which this build does not produce")
        return values[name]

    return re.sub(r"\{\{([A-Z0-9_]+)\}\}", _swap, page)


def _source_rows(collection: Collection) -> str:
    """One row per source: what it cost, and what moved. Every value escaped."""
    rows = []
    for source in collection.sources:
        name = _text(source.label)
        if source.skipped:
            reason = _text(source.reason or "no reason recorded")
            rows.append(
                f'<tr><th scope="row">{name}</th>'
                f'<td colspan="5">not collected — {reason}</td></tr>'
            )
            continue
        changes = _text(source.changes) if source.compared else "—"
        rows.append(
            f'<tr><th scope="row">{name}</th>'
            f"<td>{_text(source.records)}</td>"
            f"<td>{_text(source.pages)}</td>"
            f"<td>{_text(source.requests)}</td>"
            f"<td>{_text(_seconds(source.seconds))} s</td>"
            f"<td>{changes}</td></tr>"
        )
    return "\n".join(rows)


def _skipped_prose(collection: Collection) -> str:
    """Every source that did not run, named with the reason the run recorded."""
    return " ".join(
        f"{_text(source.label)} was not collected: {_text(source.reason or 'no reason recorded')}."
        for source in collection.skipped
    )


def _changes_prose(collection: Collection) -> str:
    """One sentence per source, each carrying its own number and its own label.

    The demo store's line names the demo in the same sentence as its number, on
    purpose: it is the one source in this project that is guaranteed to move every
    day, because it moves its own prices, and a reader who took that number for a
    change on somebody else's live site would have been misled by this page.
    """
    items = []
    for source in collection.sources:
        label = _text(source.label)
        if source.skipped:
            items.append(f"<li>{label}: not collected in this run, so there was nothing to compare.</li>")
            continue
        if not source.compared:
            items.append(f"<li>{label}: collected, but this run produced no comparison for it.</li>")
            continue
        detail = (
            f"{_text(source.added)} added, {_text(source.removed)} removed, {_text(source.changed)} changed"
        )
        if source.name == DEMO_SOURCE:
            if source.changes:
                items.append(
                    f"<li>{label} — the catalogue in this repository, whose prices and stock rotate "
                    f"daily by a seeded rule — accounts for {_text(source.changes)} of them: {detail}.</li>"
                )
            else:
                items.append(
                    f"<li>{label} — the catalogue in this repository, whose prices and stock rotate "
                    f"daily by a seeded rule — reports no change in this run, which happens when two "
                    f"runs land on the same date.</li>"
                )
            continue
        if source.changes:
            items.append(f"<li>{label}: {_text(source.changes)} changes — {detail}.</li>")
        else:
            items.append(f"<li>{label}: nothing moved since the previous snapshot.</li>")
    return "\n".join(items)


def _data_links(out_dir: Path) -> str:
    """The published data files, linked with their sizes — exactly what was copied."""
    items = []
    for name, description in DATA_FILES:
        published = out_dir / "data" / name
        if not published.is_file():
            continue
        size = _text(_human_bytes(published.stat().st_size))
        items.append(
            f'<li><a href="data/{_text(name)}">{_text(name)}</a> — {_text(description)} ({size}).</li>'
        )
    return "\n".join(items)


def _marker_split(summary: RunSummary) -> str:
    """The suite by marker, in the order the pipeline runs them."""
    described = {
        "unit": "of pure logic",
        "parsers": "of parsing saved pages",
        "integration": "of the pipeline on a real SQLite file",
        "e2e": "through a browser against the demo store",
        "live": "against the real practice sites",
    }
    return ", ".join(
        f"{_text(summary.by_marker.get(marker, 0))} {_text(described[marker])}" for marker in MARKERS
    )


def _publish_data(data_dir: Path, out_dir: Path) -> None:
    """Copy the run's data files into the site, and nothing else from that directory."""
    for name, _description in DATA_FILES:
        _place(Path(data_dir) / name, out_dir / "data" / name)


def build_site(
    stats_path: Path,
    changes_path: Path,
    results_dir: Path,
    out_dir: Path,
    *,
    revision: str = "local",
    run_url: str = "",
    report_url: str = "report/",
    media_dir: Path = Path("media"),
    data_dir: Path = Path("data"),
    assets_dir: Path = ASSETS_DIR,
) -> None:
    """Write `index.html` and everything it references into `out_dir`."""
    collection = read_collection(stats_path, changes_path)
    summary = summarise(results_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_run_url = _safe_url(run_url)
    safe_report_url = _safe_url(report_url)

    _publish_data(Path(data_dir), out_dir)
    changes_html = Path(changes_path).with_name("change-report.html")
    published_changes_html = _place(changes_html, out_dir / "changes.html")
    _place(Path(changes_path), out_dir / "changes.json")

    media_dir = Path(media_dir)
    present = {
        "video": _place(_usable(media_dir / VIDEO_NAME, MIN_VIDEO_BYTES), out_dir / "media" / VIDEO_NAME),
        "trace": _place(_usable(media_dir / TRACE_NAME, MIN_TRACE_BYTES), out_dir / "media" / TRACE_NAME),
        "run": bool(safe_run_url),
        "report": bool(safe_report_url),
        "changes_page": published_changes_html,
        "database": (out_dir / "data" / DATABASE_FILE).is_file(),
        "data": any((out_dir / "data" / name).is_file() for name, _ in DATA_FILES),
        "skipped": bool(collection.skipped),
        # The lede used to say "records from three sources", which was a typed claim
        # about a night in which one of them may not have answered at all. It is a
        # claim about *all* of them, so it is only true when the run carries every
        # source this project scrapes: a `run-stats.json` holding two of the three,
        # with neither skipped, used to be published as "all 2 sources", which reads
        # as a complete night and is not one.
        "all_sources": (
            len(collection.sources) == len(SOURCE_LABELS) and not collection.skipped
        ),
        "parse_errors": collection.parse_errors > 0,
        "retries": collection.retries > 0,
        "changes_found": collection.changes > 0,
        "tests_failed": summary.suite.failed > 0,
        "tests_skipped": summary.suite.skipped > 0,
        "tests_unknown": summary.suite.unknown > 0,
        "tests_flaky": summary.suite.flaky > 0,
        "live_ran": summary.live.total > 0,
        # Red means a check *failed* — `Tally.failed` counts Allure's `failed` and
        # `broken` together. `not_passed` would also sweep in a skipped check, and
        # a live check that did not run is not a statement about the site: the page
        # would announce drift on a night when nothing drifted.
        "live_red": summary.live.failed > 0,
        "live_skipped": summary.live.skipped > 0,
        # The prose about the live checks is written for a plural and reads as
        # nonsense over a 1 — "1 of the 1 checks did not pass" — so each count gets
        # a sentence that is true of it, the way the sibling project's page does.
        "live_one": summary.live.total == 1,
    }
    for diagram in DIAGRAMS:
        present[diagram] = _place(
            Path(assets_dir) / f"{diagram}.svg", out_dir / "assets" / f"{diagram}.svg"
        )

    page = (Path(__file__).parent / "template.html").read_text(encoding="utf-8")
    for name, keep in present.items():
        page = _resolve(page, name, keep)
    page = _resolve(page, "diagrams", any(present[diagram] for diagram in DIAGRAMS))

    # Everything below is escaped on its way into the page. The counts cannot carry
    # markup, but a skip reason is whichever message the failure carried — somebody
    # else's HTTP error, in practice — and the revision and run URL arrive from the
    # CI environment. A build tool for a public page should not hold a working
    # injection primitive at all. `_safe_url` has already escaped the two URLs.
    page = _fill(
        page,
        {
            "RECORDS": _text(collection.records),
            "PAGES": _text(collection.pages),
            "REQUESTS": _text(collection.requests),
            "RETRIES": _text(collection.retries),
            "CHANGES": _text(collection.changes),
            "PARSE_ERRORS": _text(collection.parse_errors),
            "SECONDS": _text(_seconds(collection.seconds)),
            "COLLECTED": _text(len(collection.sources) - len(collection.skipped)),
            # "the sources this project scrapes" is a fact about the project, not
            # about the night: a run that only carries two of them is two of three,
            # not two of two.
            "SOURCES": _text(len(SOURCE_LABELS)),
            "SOURCE_ROWS": _source_rows(collection),
            "SKIPPED_PROSE": _skipped_prose(collection),
            "CHANGES_PROSE": _changes_prose(collection),
            "DATA_LINKS": _data_links(out_dir),
            "COLLECTED_AT": _text(_when(collection.collected_at)),
            "TESTS": _text(summary.suite.total),
            "TESTS_PASSED": _text(summary.suite.passed),
            "TESTS_FAILED": _text(summary.suite.failed),
            "TESTS_SKIPPED": _text(summary.suite.skipped),
            "TESTS_UNKNOWN": _text(summary.suite.unknown),
            "TESTS_FLAKY": _text(summary.suite.flaky),
            "MARKER_SPLIT": _marker_split(summary),
            "LIVE": _text(summary.live.total),
            "LIVE_FAILED": _text(summary.live.failed),
            "LIVE_SKIPPED": _text(summary.live.skipped),
            "RETENTION": _text(RETENTION_NIGHTS),
            "FINISHED": _text(summary.finished.strftime("%d %B %Y, %H:%M UTC")),
            "REVISION": _text(revision[:7]),
            "RUN_URL": safe_run_url,
            "REPORT_URL": safe_report_url,
            "REPOSITORY_URL": _text(REPOSITORY_URL),
            "TRACE_VIEWER_URL": _text(TRACE_VIEWER_URL),
        },
    )

    (out_dir / "index.html").write_text(page, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stats", type=Path, default=Path("data/run/run-stats.json"))
    parser.add_argument("--changes", type=Path, default=Path("data/run/change-report.json"))
    parser.add_argument("--results", type=Path, default=Path("allure-results"))
    parser.add_argument("--out", type=Path, default=Path("site"))
    parser.add_argument("--report-url", default="report/")
    parser.add_argument("--media-dir", type=Path, default=Path("media"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--revision", default="local")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--assets", type=Path, default=ASSETS_DIR)
    args = parser.parse_args()
    build_site(
        args.stats,
        args.changes,
        args.results,
        args.out,
        revision=args.revision,
        run_url=args.run_url,
        report_url=args.report_url,
        media_dir=args.media_dir,
        data_dir=args.data_dir,
        assets_dir=args.assets,
    )
