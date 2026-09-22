"""Render the portfolio images from their sources.

Editing the wording on a cover should be editing a line of text, not opening an
image editor, so the cover is HTML and this script is the exporter. The report
screenshot comes from the real thing — a generated Allure report served over
HTTP — so refreshing it is re-running this script instead of letting it age
into a lie.

The profile banner is not rendered here. It is a profile-level asset, identical
across the owner's projects, and it is committed as
`guru-profile-banner-1000x250.png`: a second banner that almost matched the
first would look wrong beside it in the same profile.

Two things this script refuses to do, because both fail silently otherwise:

* photograph an Allure report that is not a complete, passing run. It reads the
  overview's own JSON and compares it with what pytest collects, and says what
  it found when they disagree;
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

The HTTP server is this script's own: Allure's report reads its data with
`fetch`, which a `file://` page is not allowed to do, and the pixel check below
reads a canvas back, which needs the image to share an origin with the page
reading it. It listens on the loopback interface, on a port the kernel picks,
and it is rooted at the generated report — not at the repository, and not at
the repository behind a rule about how a request is spelled. The images it also
offers back are named under `/exports/`, never walked to.
"""
from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import json
import struct
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from collections.abc import Iterator
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent

# Sized to what the profile expects; anything else is cropped by it.
SHOTS = [
    ("showcase/assets/cover.html", "guru-cover-image.png", 1536, 1024),
]

# The report finishes itself after load: Allure draws the overview charts in
# script. So the page waits for something it only shows once it is ready, then
# gets a moment to settle.
PAGES = [
    ("/", "allure-report-screenshot.png", "text=test cases", 1536, 1024),
]

#: Where `allure generate` is asked to write, by default and in the publish
#: script. `--report` points this script at another one, which is what a run
#: that generated into a scratch directory needs.
DEFAULT_REPORT_DIR = ROOT / "site" / "report"

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
#: against this mapping and never a path to walk.
EXPORTS = {name: ROOT / name for _s, name, _w, _h in SHOTS} | {
    name: ROOT / name for _u, name, _r, _w, _h in PAGES
}
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

    def do_GET(self) -> None:  # noqa: N802 - the name is http.server's
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

    def do_HEAD(self) -> None:  # noqa: N802 - the name is http.server's
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
def _serving(report_dir: Path) -> Iterator[str]:
    """The generated report over HTTP on loopback, for as long as the export runs."""
    handler = functools.partial(_Handler, directory=str(report_dir))
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
    distinct, ink = _ink(page, f"{base_url}{EXPORT_PREFIX}{name}")
    if distinct < MIN_DISTINCT_COLOURS or ink < MIN_INK_SHARE:
        raise RuntimeError(
            f"{name} is {width}x{height} but essentially blank: a grid of samples found "
            f"{distinct} distinct colour(s) (at least {MIN_DISTINCT_COLOURS} expected) "
            f"and {ink:.3%} of them off the dominant one (at least {MIN_INK_SHARE:.0%} "
            f"expected). Something rendered an empty field — a stylesheet that did not "
            f"load, or a page photographed before it painted."
        )
    print(f"  {name}: {width}x{height}, {distinct} colours sampled, {ink:.1%} ink")


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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    ran, report_dir = args.ran, args.report.resolve()
    if not (report_dir / "index.html").exists():
        raise RuntimeError(
            f"{report_dir}/index.html does not exist, so there is no report to "
            f"photograph. Generate one first:\n"
            f"  ~/.local/bin/allure generate allure-results --clean -o {report_dir}"
        )
    with _serving(report_dir) as base_url, sync_playwright() as p:
        browser = p.chromium.launch()
        # Closed the way the server is closed: every refusal in this script
        # leaves by raising, and the one resource that was not unwound in a
        # `finally` was the browser.
        try:
            probe = browser.new_page()
            probe.goto(f"{base_url}/probe", wait_until="load")

            for source, name, width, height in SHOTS:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.goto((ROOT / source).as_uri())
                page.wait_for_timeout(300)
                page.screenshot(path=ROOT / name)
                page.close()
                _require_a_picture(probe, base_url, name, width, height)

            for path, name, ready, width, height in PAGES:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.goto(f"{base_url}{path}", wait_until="load", timeout=60_000)
                _require_report_is_complete_and_green(page, base_url, ran, report_dir)
                page.wait_for_selector(ready, timeout=60_000)
                page.wait_for_timeout(3000)
                page.screenshot(path=ROOT / name)
                page.close()
                _require_a_picture(probe, base_url, name, width, height)

            probe.close()
        finally:
            browser.close()


if __name__ == "__main__":
    main()
