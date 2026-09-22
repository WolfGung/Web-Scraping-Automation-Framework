"""Render the portfolio images from their sources.

Editing the wording on a cover should be editing a line of text, not opening an
image editor, so the cover is HTML and this script is the exporter. The report
screenshot comes from the real thing — a generated Allure report served over
HTTP — so refreshing it is re-running this script instead of letting it age
into a lie. The same applies to the two pictures the README shows as evidence:
a fragment of the data this project publishes, built from the file itself, and
a photograph of a real CI run on GitHub.

The profile banner is not rendered here. It is a profile-level asset, identical
across the owner's projects, and it is committed as
`guru-profile-banner-1000x250.png`: a second banner that almost matched the
first would look wrong beside it in the same profile.

Three things this script refuses to do, because all three fail silently
otherwise:

* photograph an Allure report that is not a complete, passing run. It reads the
  overview's own JSON and compares it with what pytest collects, and says what
  it found when they disagree;
* photograph a CI run that is not green. It reads the run page's own status —
  the word GitHub prints under "Status", and the labels its status icons carry
  — and refuses anything but a run that completed successfully. A screenshot of
  a green check is a claim about the suite, and this script will not take that
  picture of a red run, or of a run whose status it could not read at all;
* leave behind a blank image. Every export is measured back out of the file and
  out of the pixels the browser actually produced, so a template that rendered
  to an empty field is an error rather than a committed picture of nothing.

Usage:
    pytest -m "unit or parsers or integration or e2e" --alluredir=allure-results
    ~/.local/bin/allure generate allure-results --clean -o site/report
    PYTHONPATH=. python scripts/make-assets.py --ran "unit or parsers or integration or e2e"

`--ran` is the marker expression the photographed run was given. The live
checks reach somebody else's site and are not part of a local run, so the
selection above is the ordinary one here; omitting `--ran` asks for the whole
suite and is then checked against the whole suite, live checks included.

Each picture has a name, and `--only` exports just the ones named — which is
what refreshing one of them is, and what keeps a run that only wanted the data
plate from demanding a freshly generated report. The CI photograph is the one
export that is not in the default set, because it is the one that reaches the
network: `--ci-run` asks for it, either at a run page you name or at the latest
green run of this workflow, looked up through GitHub's public API.

The HTTP server is this script's own: Allure's report reads its data with
`fetch`, which a `file://` page is not allowed to do, and the pixel check below
reads a canvas back, which needs the image to share an origin with the page
reading it. It listens on the loopback interface, on a port the kernel picks,
and it is rooted at the generated report — not at the repository, and not at
the repository behind a rule about how a request is spelled. When no report is
being photographed it is rooted at an empty directory instead, since the only
routes still wanted are the two this script serves itself. The images it also
offers back are named under `/exports/`, never walked to.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import functools
import html
import http.server
import json
import struct
import subprocess
import sys
import tempfile
import threading
import tomllib
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NamedTuple

from playwright.sync_api import Browser, Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent


class Plate(NamedTuple):
    """An HTML file in this repository, photographed as the browser renders it."""

    key: str
    source: str
    out: str
    width: int
    height: int


class Served(NamedTuple):
    """A page of the generated report, photographed over this script's own server."""

    key: str
    path: str
    out: str
    ready: str
    width: int
    height: int


class Remote(NamedTuple):
    """A page on somebody else's server, photographed as a logged-out visitor sees it.

    `crop` is the height kept out of `height`: the run page continues below the
    workflow's own graph into annotations and artifacts, which say nothing about
    whether the run passed, so the picture stops at the gap between the two.
    """

    key: str
    out: str
    width: int
    height: int
    crop: int


# Sized to what the profile expects; anything else is cropped by it.
COVER = Plate("cover", "showcase/assets/cover.html", "guru-cover-image.png", 1536, 1024)

#: A fragment of the data a night actually publishes, as a picture of the file:
#: the plate is `showcase/assets/data-sample.html`, and the table in it is built
#: here from `showcase/assets/books-sample.csv` — the head of the published
#: `books.csv`, kept as that file spells it.
DATA = Plate("data", "showcase/assets/data-sample.html", "showcase/images/data-sample.png", 1536, 600)

#: The report finishes itself after load: Allure draws the overview charts in
#: script. So the page waits for something it only shows once it is ready, then
#: gets a moment to settle.
REPORT = Served("report", "/", "allure-report-screenshot.png", "text=test cases", 1536, 1024)

#: The run page, at the width GitHub lays its two columns out at, cropped to the
#: run: the title and its status, the summary panel, and the jobs.
CI_RUN = Remote("ci-run", "showcase/images/ci-run.png", 1400, 900, 748)

#: Every picture, in the order they are exported, and the ones a run with no
#: `--only` and no `--ci-run` produces. The CI photograph is left out of the
#: default set because it is the one export that reaches the network.
PICTURES: tuple[Plate | Served | Remote, ...] = (COVER, DATA, REPORT, CI_RUN)
DEFAULT_PICTURES = tuple(picture.key for picture in PICTURES if picture is not CI_RUN)

#: Where `allure generate` is asked to write, by default and in the publish
#: script. `--report` points this script at another one, which is what a run
#: that generated into a scratch directory needs.
DEFAULT_REPORT_DIR = ROOT / "site" / "report"

#: The head of the published `books.csv`, as the data plate shows it.
SAMPLE_CSV = ROOT / "showcase" / "assets" / "books-sample.csv"

#: Where the table goes in that plate. The comment is in the template, so the
#: file says what fills it; if it is ever edited away, the export stops rather
#: than photographing a plate with no data on it.
TABLE_MARK = "<!-- the table the exporter builds from showcase/assets/books-sample.csv -->"

#: The workflow whose runs this script photographs, and the branch it asks about.
#: The repository itself is not spelled here: it is read from `pyproject.toml`,
#: which already states it for anyone installing the project.
WORKFLOW = ".github/workflows/ci.yml"
DEFAULT_BRANCH = "main"

#: What `--ci-run` means when it is given without a run page: ask the public API
#: for one. No token is used — the repository, its runs and this page are public,
#: and a screenshot of a page only a credential can see would prove nothing to
#: the person reading the README.
LATEST = "latest"
RUNS_API = "https://api.github.com/repos/{repository}/actions/runs"

#: What the run page says about a run that finished green, in the summary panel
#: under the word "Status". GitHub prints the conclusion there in words.
GREEN = "success"

#: How a status icon on that page labels a conclusion that is not green. The
#: labels read "failure: <job>", "cancelled: <job>" and so on, so the prefix is
#: what identifies them.
NOT_GREEN_LABELS = (
    "failure", "failed", "cancelled", "canceled", "timed out", "action required", "startup failure",
)

#: A flat field is one colour everywhere. Real output is not: the cover is a
#: gradient under type, and the report is a white page under a dark sidebar
#: and a chart. Both thresholds sit far below what either produces and far
#: above what an empty page does, so they separate the two cases without
#: being a second opinion on the design.
MIN_DISTINCT_COLOURS = 24
MIN_INK_SHARE = 0.02

#: The pixel probe samples a grid rather than every pixel: 40_000 points out
#: of a million and a half is plenty to tell a picture from an empty field,
#: and keeps the readback off the critical path of the export.
PROBE_GRID = 200

#: The document `/probe` answers with: somewhere for the canvas readback to
#: run that shares an origin with the images it reads.
PROBE_PAGE = b"<!doctype html><meta charset=utf-8><title>probe</title>"

#: The images this script writes, offered back under a prefix of their own.
#: They are named, not looked up: the request supplies a name to compare
#: against this mapping and never a path to walk — so the key is the file's own
#: name, and two pictures may not share one.
EXPORTS = {Path(picture.out).name: ROOT / picture.out for picture in PICTURES}
assert len(EXPORTS) == len(PICTURES), "two pictures share a file name, and the export routes go by name"
EXPORT_PREFIX = "/exports/"


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Serves the generated report, the exported images, and one blank page.

    A static handler rooted at the repository would put everything in the
    working tree — `.env` included — on a listening socket, and a rule that
    only inspected the request string would not stop it: the string
    `/site/report/../../.env` starts with the report's path and still names
    the repository root once it is decoded and normalised. So the root is the
    report directory itself, and every path this handler is willing to open is
    resolved first and then checked to be inside it. The images are not served
    from that tree at all; they are looked up by name under `/exports/`.

    `/probe` exists only to give the canvas readback a document of the same
    origin as the images.
    """

    def do_GET(self) -> None:  # the name is http.server's
        served = self._special()
        if served is None:
            super().do_GET()
            return
        status, content_type, body = served
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self) -> None:  # the name is http.server's
        served = self._special()
        if served is None:
            super().do_HEAD()
            return
        status, content_type, body = served
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

    def _special(self) -> tuple[int, str, bytes] | None:
        """The two routes that are not files inside the report directory."""
        path = urllib.parse.unquote(self.path.split("?", 1)[0].split("#", 1)[0])
        if path == "/probe":
            return 200, "text/html; charset=utf-8", PROBE_PAGE
        if not path.startswith(EXPORT_PREFIX):
            return None
        name = path[len(EXPORT_PREFIX):]
        source = EXPORTS.get(name)
        if source is None or "/" in name:
            return 404, "text/plain; charset=utf-8", b"not exported by this script"
        return 200, "image/png", source.read_bytes()

    def send_head(self):  # returns what http.server returns: a file object, or None
        """Refuse anything that resolves outside the report directory.

        `SimpleHTTPRequestHandler.translate_path` decodes, normalises and
        joins against the root, so the decision here is made on the file that
        would actually be opened rather than on the string that asked for it.
        """
        candidate = Path(self.translate_path(self.path)).resolve()
        root = Path(self.directory).resolve()
        if candidate != root and root not in candidate.parents:
            self.send_error(404, "not served by this exporter")
            return None
        return super().send_head()

    def log_message(self, fmt: str, *args: object) -> None:
        """Silence the per-request log; the export's own output is the story."""


@contextlib.contextmanager
def _serving(root: Path) -> Iterator[str]:
    """The generated report over HTTP on loopback, for as long as the export runs."""
    handler = functools.partial(_Handler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _collected_test_count(selection: tuple[str, ...]) -> int:
    """How many tests pytest collects for the selection the run was given.

    Runs in a subprocess with its own throwaway allure directory, so that
    collecting here cannot write into `allure-results` — the directory the
    report being photographed was just built from.
    """
    with tempfile.TemporaryDirectory() as spool:
        proc = subprocess.run(
            [
                sys.executable, "-m", "pytest", "--collect-only", "-q",
                "-p", "no:cacheprovider", f"--alluredir={spool}", *selection,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    ids = [line for line in proc.stdout.splitlines() if line.startswith("tests/") and "::" in line]
    if not ids:
        raise RuntimeError(
            f"pytest --collect-only {' '.join(selection)!s} collected nothing, so the "
            f"report cannot be checked against it.\n{proc.stdout}\n{proc.stderr}"
        )
    return len(ids)


def _summary_statistic(page: Page, summary_url: str, report_dir: Path) -> dict[str, int]:
    """The overview's own numbers, or an explanation of why there are none.

    A half-generated report is the ordinary failure here: the directory looks
    like a report, the page even renders, but `widgets/summary.json` is
    missing, truncated or is the server's 404 page. Left alone that surfaces
    as a JSON decode error naming nothing, so every case is turned into a
    sentence that says what was found at that URL and what to do about it.
    """
    fix = (
        "Generate the report again before taking this screenshot:\n"
        f"  ~/.local/bin/allure generate allure-results --clean -o {report_dir}"
    )
    response = page.request.get(summary_url)
    if not response.ok:
        raise RuntimeError(
            f"{summary_url} answered HTTP {response.status}, so the report being "
            f"photographed has no overview data. Either the report was never "
            f"generated into {report_dir}, or it is not the report being served.\n{fix}"
        )
    body = response.text()
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{summary_url} is not JSON ({exc}); it starts with {body[:120]!r}. That "
            f"is what a half-written report, or a web server answering with an HTML "
            f"error page, looks like.\n{fix}"
        ) from exc
    statistic = parsed.get("statistic") if isinstance(parsed, dict) else None
    if not isinstance(statistic, dict):
        held = sorted(parsed) if isinstance(parsed, dict) else type(parsed).__name__
        raise RuntimeError(
            f"{summary_url} parsed, but it carries no 'statistic' object: it is "
            f"{len(body)} byte(s) holding {held}. A complete report always has "
            f"one.\n{fix}"
        )
    missing = sorted(
        key for key in ("total", "failed", "broken", "unknown")
        if not isinstance(statistic.get(key), int)
    )
    if missing:
        raise RuntimeError(
            f"{summary_url} reports a run without {', '.join(missing)}; its statistic "
            f"is {statistic}. The counts this screenshot is checked against cannot be "
            f"read from it.\n{fix}"
        )
    return statistic


def _require_report_is_complete_and_green(
    page: Page, base_url: str, ran: str, report_dir: Path
) -> None:
    """Refuse to screenshot a report that is partial or has failures.

    Waiting for the text "test cases" only proves *an* overview rendered — a
    five-test partial with two failures says "test cases" too. The overview is
    drawn straight from this JSON, so reading it is reading what a viewer of
    the screenshot would actually see: how many cases the run reports, and
    whether any of them failed.

    This runs before the page is given time to render, and not after. The
    overview is drawn from `widgets/summary.json`: if that file is missing or
    malformed, the text worth waiting for is exactly the text that can never
    appear, and waiting for it first would replace every sentence below with a
    bare sixty-second timeout naming nothing. The file is read from the server
    rather than from the page, and the URL is built from the address being
    served rather than from `page.url`, which Allure rewrites with a fragment
    as it routes.

    `ran` is the marker expression the run was given, and is empty for the
    whole suite. A run that deliberately left part of the suite out passes the
    same expression to `--ran`, so a short report is still checked against a
    number rather than waved through.
    """
    summary_url = f"{base_url}/widgets/summary.json"
    stat = _summary_statistic(page, summary_url, report_dir)
    not_green = stat["failed"] + stat["broken"] + stat["unknown"]
    expected = _collected_test_count(("-m", ran))
    asked_for = f'-m "{ran}"' if ran else "the whole suite"
    if not_green or stat["total"] != expected:
        raise RuntimeError(
            f"{report_dir} is not a complete, passing run of {asked_for}: it reports "
            f"{stat['total']} test case(s) ({expected} expected from the current "
            f"suite), {stat['failed']} failed, {stat['broken']} broken, "
            f"{stat['unknown']} unknown. Regenerate it before taking this screenshot: "
            f"run the suite, then `~/.local/bin/allure generate allure-results "
            f"--clean -o {report_dir}`. If the run left part of the suite out on "
            f"purpose, say so with --ran."
        )


def _png_size(path: Path) -> tuple[int, int]:
    """The dimensions the file itself carries, read out of its header.

    Asking the browser for a viewport is asking for one; the profile crops
    what it is given, so the number that matters is the one in the PNG.
    """
    header = path.read_bytes()[:24]
    if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise RuntimeError(f"{path.name} is not a PNG: it begins {header[:8]!r}.")
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def _ink(page: Page, image_url: str) -> tuple[int, float]:
    """How many colours a grid of samples finds, and how much is not the field.

    Read back through a canvas in the browser that produced the file: it is
    the only PNG decoder guaranteed to be present, and the image is fetched
    from the same origin as the page, which is what makes the readback legal.
    """
    result = page.evaluate(
        """async ([url, grid]) => {
          const img = new Image();
          img.src = url;
          await img.decode();
          const canvas = document.createElement('canvas');
          canvas.width = img.naturalWidth;
          canvas.height = img.naturalHeight;
          const ctx = canvas.getContext('2d', { willReadFrequently: true });
          ctx.drawImage(img, 0, 0);
          const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
          const seen = new Map();
          const stepX = Math.max(1, Math.floor(canvas.width / grid));
          const stepY = Math.max(1, Math.floor(canvas.height / grid));
          let sampled = 0;
          for (let y = 0; y < canvas.height; y += stepY) {
            for (let x = 0; x < canvas.width; x += stepX) {
              const i = (y * canvas.width + x) * 4;
              const key = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2];
              seen.set(key, (seen.get(key) || 0) + 1);
              sampled += 1;
            }
          }
          let modal = 0;
          for (const count of seen.values()) modal = Math.max(modal, count);
          return { distinct: seen.size, ink: sampled ? (sampled - modal) / sampled : 0 };
        }""",
        [image_url, PROBE_GRID],
    )
    return int(result["distinct"]), float(result["ink"])


def _require_a_picture(page: Page, base_url: str, name: str, width: int, height: int) -> None:
    """Refuse to leave behind a file that is the right size and says nothing.

    A template whose stylesheet failed to apply, a report that rendered into
    an empty body, a screenshot taken before anything painted: each writes a
    perfectly valid PNG of the right dimensions. Measuring the file and then
    the pixels turns all three into a message instead of a commit.
    """
    path = ROOT / name
    if not path.exists():
        raise RuntimeError(f"{name} was not written, though the screenshot reported no error.")
    actual = _png_size(path)
    if actual != (width, height):
        raise RuntimeError(
            f"{name} is {actual[0]}x{actual[1]}, not the {width}x{height} it has to be. "
            f"The profile crops anything else, so this file cannot be shipped."
        )
    distinct, ink = _ink(page, f"{base_url}{EXPORT_PREFIX}{path.name}")
    if distinct < MIN_DISTINCT_COLOURS or ink < MIN_INK_SHARE:
        raise RuntimeError(
            f"{name} is {width}x{height} but essentially blank: a grid of samples found "
            f"{distinct} distinct colour(s) (at least {MIN_DISTINCT_COLOURS} expected) "
            f"and {ink:.3%} of them off the dominant one (at least {MIN_INK_SHARE:.0%} "
            f"expected). Something rendered an empty field — a stylesheet that did not "
            f"load, or a page photographed before it painted."
        )
    print(f"  {name}: {width}x{height}, {distinct} colours sampled, {ink:.1%} ink")


# -- the plates ------------------------------------------------------------------


def _export_plate(
    browser: Browser,
    probe: Page,
    base_url: str,
    plate: Plate,
    markup: str | None = None,
    check: Callable[[Page, Plate], None] | None = None,
) -> None:
    """Photograph one of this repository's own HTML files.

    `markup` is the document to render when it is not simply the file on disk —
    the data plate is a template with a table built into it here — and `check`
    is whatever that plate has to be held to before it is photographed.
    """
    page = browser.new_page(viewport={"width": plate.width, "height": plate.height})
    if markup is None:
        page.goto((ROOT / plate.source).as_uri())
    else:
        page.set_content(markup, wait_until="load")
    page.wait_for_timeout(300)
    if check is not None:
        check(page, plate)
    (ROOT / plate.out).parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=ROOT / plate.out)
    page.close()
    _require_a_picture(probe, base_url, plate.out, plate.width, plate.height)


def _sample_rows() -> tuple[list[str], list[dict[str, str]]]:
    """The head of the published `books.csv`, as the file spells it."""
    with SAMPLE_CSV.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        rows = list(reader)
    if not columns or not rows:
        raise RuntimeError(
            f"{SAMPLE_CSV.relative_to(ROOT)} holds {len(columns)} column(s) and "
            f"{len(rows)} row(s), so there is no table to draw. It is the head of the "
            f"published books.csv; refresh it from "
            f"https://wolfgung.github.io/Web-Scraping-Automation-Framework/data/books.csv"
        )
    return columns, rows


def _data_plate_markup() -> str:
    """The data plate with the table built into it, straight out of the CSV.

    The columns are the file's own columns, in the file's own order, and each
    cell is the file's own text: the picture is a rendering of the data rather
    than a second copy of it that could disagree.
    """
    template = (ROOT / DATA.source).read_text(encoding="utf-8")
    if TABLE_MARK not in template:
        raise RuntimeError(
            f"{DATA.source} no longer carries the comment the table replaces:\n"
            f"  {TABLE_MARK}\nWithout it this export would photograph an empty plate."
        )
    columns, rows = _sample_rows()
    head = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(row[column] or '')}</td>" for column in columns) + "</tr>"
        for row in rows
    )
    table = f'<table class="grid"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
    return template.replace(TABLE_MARK, table)


def _require_the_caption_counts_the_rows(page: Page, plate: Plate) -> None:
    """Refuse a plate whose caption counts something other than what it shows.

    The caption states how many rows the picture holds, and a reader takes that
    on trust — so it is read back off the rendered page, beside the rows that
    were actually drawn, rather than believed.
    """
    said = page.locator(".rows-shown").inner_text().strip()
    drawn = page.locator(".grid tbody tr").count()
    if said != str(drawn):
        raise RuntimeError(
            f"{plate.source} says it shows {said} row(s), but {drawn} were drawn into "
            f"it from {SAMPLE_CSV.relative_to(ROOT)}. Edit the caption, or the sample, "
            f"so that the picture counts itself correctly — and note that the number in "
            f"that caption is pinned by tests/unit/test_showcase_figures.py."
        )


# -- the run on GitHub -----------------------------------------------------------


def _repository() -> str:
    """`owner/name`, read from the metadata this project already states it in."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    url = config["project"]["urls"]["Repository"]
    prefix = "https://github.com/"
    if not url.startswith(prefix):
        raise RuntimeError(
            f"pyproject.toml gives the repository as {url!r}, which is not a GitHub "
            f"URL this script can turn into an API path."
        )
    return url[len(prefix):].strip("/")


def _head_of(branch: str) -> str | None:
    """What `origin/<branch>` points at here, or nothing if this clone cannot say."""
    proc = subprocess.run(
        ["git", "rev-parse", f"origin/{branch}"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    return proc.stdout.strip() or None


def _latest_green_run(branch: str = DEFAULT_BRANCH) -> str:
    """The run page to photograph: the latest green run of this workflow.

    Asked of the public API, without a token, because the repository is public
    and so is the page being photographed. The run that built what
    `origin/<branch>` points at is preferred where there is one, so the picture
    shows the checks that passed on the code a visitor is reading; otherwise it
    is simply the most recent green run, which is the honest answer when the
    working clone is ahead of what CI has seen.
    """
    repository = _repository()
    query = urllib.parse.urlencode({"branch": branch, "status": "success", "per_page": 30})
    request = urllib.request.Request(
        f"{RUNS_API.format(repository=repository)}?{query}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"{repository} make-assets"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # a literal https URL, built above
        payload = json.load(response)
    runs = [
        run for run in payload.get("workflow_runs", [])
        if run.get("path") == WORKFLOW and run.get("status") == "completed" and run.get("conclusion") == "success"
    ]
    if not runs:
        raise RuntimeError(
            f"the public API reports no completed, successful run of {WORKFLOW} on "
            f"{branch} for {repository}, so there is no green run to photograph."
        )
    head = _head_of(branch)
    chosen = next((run for run in runs if run.get("head_sha") == head), runs[0])
    built = chosen.get("head_sha", "")[:7]
    whose = f"origin/{branch}" if head == chosen.get("head_sha") else f"an older commit than origin/{branch}"
    print(f"  ci-run: the latest green run of {WORKFLOW} on {branch} built {built}, which is {whose}")
    return chosen["html_url"]


def _require_the_run_is_green(page: Page, url: str) -> None:
    """Refuse to photograph a run that is not green, or one that will not say.

    A screenshot of a CI run is a claim that the suite passed, so it is read off
    the page rather than assumed from the URL it was asked for: the word GitHub
    prints under "Status" in the summary panel, and the labels its status icons
    carry. A run whose status cannot be read at all is refused too — that is
    what a redesigned page looks like, and a picture taken through it would be a
    picture nobody checked.
    """
    seen = page.evaluate(
        """([notGreen]) => {
          const label = [...document.querySelectorAll('span, div, dt')].find(
            (element) => element.children.length === 0 && element.textContent.trim() === 'Status');
          const beside = label && label.nextElementSibling;
          const marks = [...document.querySelectorAll('[aria-label]')]
            .map((element) => (element.getAttribute('aria-label') || '').trim())
            .filter((text) => text);
          return {
            said: beside ? beside.textContent.trim() : null,
            green: marks.filter((text) => text.toLowerCase().startsWith('completed successfully')).length,
            wrong: [...new Set(marks.filter(
              (text) => notGreen.some((bad) => text.toLowerCase().startsWith(bad))))],
          };
        }""",
        [list(NOT_GREEN_LABELS)],
    )
    said, green, wrong = seen["said"], int(seen["green"]), list(seen["wrong"])
    if said is None:
        raise RuntimeError(
            f"{url} does not state a status where this script reads one — the summary "
            f"panel prints the conclusion beside the word \"Status\". Either the run "
            f"page has been redesigned, or that is not a run page. Nothing was "
            f"photographed: a screenshot of a run whose result was never read is a "
            f"picture of nothing in particular."
        )
    if said.lower() != GREEN or wrong or not green:
        raise RuntimeError(
            f"{url} is not a green run: its summary panel says {said!r}, {green} status "
            f"icon(s) report success, and what its icons report that is not green is "
            f"{wrong or 'nothing — so nothing on that page called the run passing'}. "
            f"This exporter does not photograph a run it cannot call passing — point "
            f"--ci-run at a green run, or let it look one up itself."
        )


def _export_ci_run(browser: Browser, probe: Page, base_url: str, url: str | None) -> None:
    """Photograph the public run page: the workflow, its status, and its jobs.

    Logged out, in a browser with nothing signed in, because that is what a
    visitor following the badge sees. The page is given the width GitHub lays
    its two columns out at, and the shot is cropped above the annotations and
    artifacts below them — they are not evidence about the run, and a fixed
    height is what keeps the committed file the same size every time.
    """
    run_url = _latest_green_run() if url in (None, LATEST) else url
    page = browser.new_page(viewport={"width": CI_RUN.width, "height": CI_RUN.height})
    page.goto(run_url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_selector("text=Total duration", timeout=60_000)
    page.wait_for_selector('[aria-label="Workflow run graph"]', timeout=60_000)
    page.wait_for_timeout(2500)
    _require_the_run_is_green(page, run_url)
    (ROOT / CI_RUN.out).parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(
        path=ROOT / CI_RUN.out,
        clip={"x": 0, "y": 0, "width": CI_RUN.width, "height": CI_RUN.crop},
    )
    page.close()
    _require_a_picture(probe, base_url, CI_RUN.out, CI_RUN.width, CI_RUN.crop)
    print(f"  ci-run: photographed {run_url}")


# -- the report ------------------------------------------------------------------


def _export_report(browser: Browser, probe: Page, base_url: str, ran: str, report_dir: Path) -> None:
    """Photograph the generated Allure report, once it is known to be green."""
    page = browser.new_page(viewport={"width": REPORT.width, "height": REPORT.height})
    page.goto(f"{base_url}{REPORT.path}", wait_until="load", timeout=60_000)
    _require_report_is_complete_and_green(page, base_url, ran, report_dir)
    page.wait_for_selector(REPORT.ready, timeout=60_000)
    page.wait_for_timeout(3000)
    page.screenshot(path=ROOT / REPORT.out)
    page.close()
    _require_a_picture(probe, base_url, REPORT.out, REPORT.width, REPORT.height)


# -- the command line ------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--ran",
        metavar="MARKEREXPR",
        default="",
        help=(
            "the -m expression the photographed run was given, when it was not the "
            'whole suite, e.g. --ran "not live". The report is checked against what '
            "pytest collects for it."
        ),
    )
    parser.add_argument(
        "--report",
        metavar="DIR",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help=(
            "the generated Allure report to photograph "
            f"(default: {DEFAULT_REPORT_DIR.relative_to(ROOT)})."
        ),
    )
    parser.add_argument(
        "--only",
        metavar="PICTURE",
        nargs="+",
        choices=[picture.key for picture in PICTURES],
        help=(
            "export only these pictures, of "
            f"{', '.join(picture.key for picture in PICTURES)} "
            f"(default: {', '.join(DEFAULT_PICTURES)}, and ci-run when --ci-run is given)."
        ),
    )
    parser.add_argument(
        "--ci-run",
        metavar="URL",
        nargs="?",
        const=LATEST,
        default=None,
        help=(
            "also photograph a GitHub Actions run page into showcase/images/ci-run.png. "
            "With no URL, the latest completed, successful run of this workflow on "
            f"{DEFAULT_BRANCH} is looked up through the public API — preferring the run "
            f"that built what origin/{DEFAULT_BRANCH} points at. A run that is not green "
            "is refused rather than photographed."
        ),
    )
    return parser.parse_args(argv)


def _wanted(args: argparse.Namespace) -> tuple[str, ...]:
    """Which pictures this run exports, in the order they are taken."""
    asked = tuple(args.only) if args.only else DEFAULT_PICTURES + (
        (CI_RUN.key,) if args.ci_run is not None else ()
    )
    return tuple(picture.key for picture in PICTURES if picture.key in asked)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    wanted = _wanted(args)
    ran, report_dir = args.ran, args.report.resolve()
    if REPORT.key in wanted and not (report_dir / "index.html").exists():
        raise RuntimeError(
            f"{report_dir}/index.html does not exist, so there is no report to "
            f"photograph. Generate one first:\n"
            f"  ~/.local/bin/allure generate allure-results --clean -o {report_dir}\n"
            f"Or leave the report out of this run: --only "
            f"{' '.join(key for key in wanted if key != REPORT.key) or COVER.key}"
        )
    with contextlib.ExitStack() as stack:
        # The server exists for `/probe` and `/exports/` as much as for the
        # report, and those two are served by this handler rather than read off
        # disk — so a run that photographs no report is rooted at an empty
        # directory, which is the only root that can leak nothing.
        root = report_dir if REPORT.key in wanted else Path(stack.enter_context(tempfile.TemporaryDirectory()))
        base_url = stack.enter_context(_serving(root))
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch()
        # Closed the way the server is closed: every refusal in this script
        # leaves by raising, and the one resource that was not unwound in a
        # `finally` was the browser.
        try:
            probe = browser.new_page()
            probe.goto(f"{base_url}/probe", wait_until="load")

            if COVER.key in wanted:
                _export_plate(browser, probe, base_url, COVER)
            if DATA.key in wanted:
                _export_plate(
                    browser, probe, base_url, DATA,
                    markup=_data_plate_markup(),
                    check=_require_the_caption_counts_the_rows,
                )
            if REPORT.key in wanted:
                _export_report(browser, probe, base_url, ran, report_dir)
            if CI_RUN.key in wanted:
                _export_ci_run(browser, probe, base_url, args.ci_run)

            probe.close()
        finally:
            browser.close()


if __name__ == "__main__":
    main()
