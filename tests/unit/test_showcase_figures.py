"""Every number the drawings and the cover state is a number the code holds.

The two figures on the showcase page and the cover the profile shows quote
numbers — how much each catalogue holds, how many pages that is, how many
checks a job runs, how many nights of history the published database keeps —
and nobody notices a drawing going stale. A reader takes a figure in over a few
seconds and never checks it against the repository, so a catalogue that changed
size, or a check that was added, has to fail the build here rather than quietly
make a picture lie.

Three sources state those numbers today: the two hand-drawn SVG figures under
`showcase/assets/`, and `cover.html`, the template `guru-cover-image.png` is
exported from. Each kind is read the way it is written — a count in an SVG is a
line of text inside a box, a count on the cover is an element carrying the
classes this reader looks for — and each one is compared against the thing it
claims to describe: `scrapewatch.sources` for the catalogue sizes,
`showcase.build.RETENTION_NIGHTS` for the history, and `pytest --collect-only`
for anything counting checks.

Two further guards sit underneath. Nothing here may pass by finding nothing: a
reader that came back empty is a failure, and every count found has to be one
this module checks. And no digit may appear in any of the three files that this
module does not account for, so a number added to a drawing cannot escape by
being written in a shape the structured readers do not recognise.

The last section pins facts the code itself keeps in two places, for the same
reason: two copies of one truth drift, and the cheapest moment to hear about it
is here.
"""
from __future__ import annotations

import os
import re
import shlex
import struct
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from datetime import date
from functools import cache
from html.parser import HTMLParser
from pathlib import Path

import pytest

from scrapewatch.demo_store.catalogue import catalogue_for
from scrapewatch.sources import BOOKS_PAGE_SIZE, FULL_RUN_SIZES, KNOWN_SOURCES
from showcase.build import DATABASE_FILE, RETENTION_NIGHTS

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "showcase" / "assets"
SVG = "{http://www.w3.org/2000/svg}"

FIGURES = ("architecture.svg", "pipeline.svg")

#: The template `guru-cover-image.png` is exported from. It is read here for the
#: same reason the figures are, and more urgently: the cover is the first thing a
#: visitor to the profile sees, it is a picture rather than text, and nobody
#: re-reads a picture to check whether it still adds up.
COVER = "cover.html"
SOURCES = (*FIGURES, COVER)

#: A line of a drawing that states a count: a number, then what is being counted.
#: "50 catalogue pages" and "3 cases" are both this shape; "cron 0 5 * * *" is
#: not, and is pinned as a literal further down instead.
DRAWN_COUNT = re.compile(r"^(\d+) ([a-z][a-z ]*)$")

#: Any run of digits. The last guard in this module removes every number it has
#: checked from the text of a file and then refuses to find one of these left.
DIGITS = re.compile(r"\d")

#: Collection normally finishes in a fraction of a second (see `_collected`);
#: this is generous enough never to fire on a merely slow machine, so a hit here
#: means the subprocess actually hung.
COLLECT_TIMEOUT_SECONDS = 60

#: What to do when a number no longer matches, per kind of source.
FIX = {
    ".svg": (
        "The figure is hand-drawn SVG: edit the number in showcase/assets/{source}, "
        "and check its <desc> — the description a screen reader hears states the same "
        "numbers, and both figures quote some of them."
    ),
    ".html": (
        "The cover is exported from showcase/assets/{source}, so edit the number there "
        "and export again — the image is not edited by hand:\n"
        '  pytest -m "unit or parsers or integration or e2e" --alluredir=allure-results\n'
        "  ~/.local/bin/allure generate allure-results --clean -o site/report\n"
        '  PYTHONPATH=. python scripts/make-assets.py --ran "unit or parsers or integration or e2e"\n'
        "then `git add guru-cover-image.png`: the exported image is a tracked file, and "
        "a template nobody exported changes nothing the profile shows."
    ),
}
FIX_DEFAULT = "Edit the number in {source} so that it states what the code holds."

#: Every number a drawing or the cover states about a catalogue, a history or a
#: sum, with the value the code holds for it. The key is the file, the box or
#: card the number sits in, and what it counts — so a number moved into the
#: wrong box is a failure here rather than something plausible.
CLAIMS: dict[tuple[str, str, str], int] = {
    ("architecture.svg", "books.toscrape.com", "books"): FULL_RUN_SIZES["books"],
    # The catalogue is walked page by page, so the page count is the catalogue
    # divided by what one listing page carries — not a third number to keep.
    ("architecture.svg", "books.toscrape.com", "catalogue pages"): (
        FULL_RUN_SIZES["books"] // BOOKS_PAGE_SIZE
    ),
    ("architecture.svg", "quotes.toscrape.com/js/", "quotes"): FULL_RUN_SIZES["quotes"],
    ("architecture.svg", "the demo store", "products"): FULL_RUN_SIZES["demo"],
    ("pipeline.svg", "the database, carried from one night to the next", "nights per source"): (
        RETENTION_NIGHTS
    ),
    (COVER, "books.toscrape.com", "books"): FULL_RUN_SIZES["books"],
    (COVER, "quotes.toscrape.com", "quotes"): FULL_RUN_SIZES["quotes"],
    (COVER, "the demo store", "products"): FULL_RUN_SIZES["demo"],
    # The badge is not a fourth source: its first number is what the three cards
    # add up to in a night where every source answered, and its second is how
    # many sources that is.
    (COVER, "in one full run", "records"): sum(FULL_RUN_SIZES.values()),
    (COVER, "one pipeline", "sources"): len(KNOWN_SOURCES),
}

#: Every number a drawing states about the suite, with the selection it claims to
#: describe. These are checked against what pytest collects rather than against a
#: constant: the count of checks is not a fact the code states anywhere, it is a
#: fact about the tests themselves.
#:
#: Neither figure states a count for the network-free checks on purpose. That
#: number moves whenever a test is added to `tests/unit` — including to this
#: file, whose parametrised cases would then count themselves — so a drawing
#: quoting it would be redrawn every week and teach its readers to ignore the
#: failure.
SELECTIONS: dict[tuple[str, str, str], tuple[str, ...]] = {
    ("pipeline.svg", "the browser check", "cases"): ("-m", "e2e"),
    ("pipeline.svg", "the live checks", "cases"): ("-m", "live"),
}

CHECKED = {**{key: None for key in CLAIMS}, **{key: None for key in SELECTIONS}}


def _path(source: str) -> str:
    """Where a source lives, for a message someone has to act on."""
    return f"showcase/assets/{source}"


# -- reading a drawing ----------------------------------------------------------


def _counts_in_svg(path: Path, figure: str) -> dict[tuple[str, str], int]:
    """Every count an SVG figure states, keyed by the box holding it and its unit.

    Both the boxes and the text are in the file with their coordinates, so the
    pairing is read out of the drawing itself rather than out of a table kept
    beside it: a text belongs to the box it sits inside, and a box's title is its
    topmost line. A number moved into the wrong box, a number floating outside
    every box, or a box left with a number and no title, is therefore visible
    here rather than plausible.
    """
    tree = ET.parse(path)
    boxes = [
        (float(r.get("x")), float(r.get("y")), float(r.get("width")), float(r.get("height")))
        for r in tree.iter(f"{SVG}rect")
        if "box" in (r.get("class") or "").split()
    ]
    lines: dict[tuple[float, float, float, float], list[tuple[float, str]]] = {}
    for text in tree.iter(f"{SVG}text"):
        x, y = float(text.get("x")), float(text.get("y"))
        for box in boxes:
            left, top, width, height = box
            if left <= x <= left + width and top <= y <= top + height:
                lines.setdefault(box, []).append((y, (text.text or "").strip()))
                break

    drawn: dict[tuple[str, str], int] = {}
    for _box, contents in lines.items():
        contents.sort()
        bodies = [body for _, body in contents]
        title = bodies[0]
        for body in bodies:
            match = DRAWN_COUNT.match(body)
            if match is None:
                continue
            unit = match.group(2)
            assert (title, unit) not in drawn, (
                f'{_path(figure)}: two counts of "{unit}" in the box titled "{title}". '
                f"A box and a unit are what this test looks a count up by, so two "
                f"cannot share them."
            )
            drawn[(title, unit)] = int(match.group(1))
    return drawn


@cache
def _drawn(figure: str) -> dict[tuple[str, str], int]:
    """The counts of one of this project's own figures, read once per figure."""
    return _counts_in_svg(ASSETS / figure, figure)


# -- reading the cover ----------------------------------------------------------


#: Elements HTML closes on your behalf. A stack waiting for their end tag would
#: never unwind, and every count after the first `<meta>` would be read as if it
#: sat inside it.
VOID_ELEMENTS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)

#: Elements whose text is not text a reader sees. The cover's stylesheet is full
#: of numbers, none of which are on the picture.
UNSPOKEN = frozenset({"style", "script"})


class _Stats(HTMLParser):
    """The counts a cover template states, read out of the markup it renders.

    The same idea as `_drawn`, in the medium the cover is written in: a count is
    not listed in a table kept beside the file, it is found in the file. An
    element with class `stat` is one stated count; the number is the element with
    class `stat-n` inside it, the unit beside that number is `stat-u`, and the
    label a reader reads it under is `stat-of`. All three are visible text, so
    what this finds is what the exported image shows — a number moved into the
    wrong card, or a card left with a number and no label, is a failure here
    rather than something plausible nobody looks at twice.

    The same walk collects the visible text of the document, which the last guard
    in this module reads.
    """

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self._source = source
        self._open: list[dict] = []
        self._mute = 0
        self.found: dict[tuple[str, str], int] = {}
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in UNSPOKEN:
            self._mute += 1
        if tag in VOID_ELEMENTS:
            return
        classes = set((dict(attrs).get("class") or "").split())
        self._open.append({"tag": tag, "classes": classes, "text": [], "roles": {}})

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """`<path/>` and the rest of the inline SVG open and close at once."""

    def handle_data(self, data: str) -> None:
        if self._mute:
            return
        self.text.append(data)
        if self._open:
            self._open[-1]["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in UNSPOKEN and self._mute:
            self._mute -= 1
        # Unwinds to the tag that closed rather than assuming the markup is
        # balanced: one unclosed element should cost its own count, not every
        # count after it.
        while self._open:
            frame = self._open.pop()
            self._finish(frame)
            if frame["tag"] == tag:
                return

    def close(self) -> None:
        super().close()
        while self._open:
            self._finish(self._open.pop())

    def _finish(self, frame: dict) -> None:
        """Hand one closed element's text and roles up to the one holding it."""
        text = " ".join("".join(frame["text"]).split())
        roles = frame["roles"]
        for role, marker in (("n", "stat-n"), ("u", "stat-u"), ("of", "stat-of")):
            if marker in frame["classes"]:
                roles.setdefault(role, text)
        if "stat" in frame["classes"]:
            self._record(roles, text)
            roles = {}  # a stated count is closed; it is not part of an outer one
        if self._open:
            parent = self._open[-1]
            parent["text"].append(text)
            for role, value in roles.items():
                parent["roles"].setdefault(role, value)

    def _record(self, roles: dict[str, str], text: str) -> None:
        markers = {"n": "stat-n", "u": "stat-u", "of": "stat-of"}
        missing = sorted(markers[key] for key in markers if key not in roles)
        assert not missing, (
            f'{_path(self._source)}: the count reading "{text}" has no '
            f"{' and no '.join(missing)}. An element with class `stat` states one "
            f"count, and has to hold the number, the unit and the label it is shown under."
        )
        number, unit, label = roles["n"], roles["u"], roles["of"]
        assert number.isdigit(), (
            f'{_path(self._source)}: the count shown under "{label}" is {number!r}, '
            f"which is not a number."
        )
        assert (label, unit) not in self.found, (
            f'{_path(self._source)}: two counts of "{unit}" are shown under "{label}". '
            f"A label and a unit are what this test looks a count up by, so two cannot "
            f"share them."
        )
        self.found[(label, unit)] = int(number)


@cache
def _read_cover(cover: str) -> _Stats:
    parser = _Stats(cover)
    parser.feed((ASSETS / cover).read_text(encoding="utf-8"))
    parser.close()
    return parser


def _stated(cover: str) -> dict[tuple[str, str], int]:
    """Every count a cover template states, keyed by its label and unit."""
    return _read_cover(cover).found


def _require_counts(found: dict[tuple[str, str], int], source: str) -> dict[tuple[str, str], int]:
    """Refuse an empty reading instead of passing it on.

    Every claim is checked against what the reader finds, so a reader that came
    back empty from a rewritten file would report success over a picture stating
    nothing at all. This is the one place that says no.
    """
    assert found, (
        f"{_path(source)} states no counts at all: nothing in it is a number this "
        f"module can read. Either the file stopped quoting numbers, or it was "
        f"rewritten in a way this reader no longer understands — in which case the "
        f"numbers it shows are no longer checked by anything."
    )
    return found


def _found(source: str) -> dict[tuple[str, str], int]:
    """Every count `source` states, dispatched by how that kind of file is written."""
    reader = _stated if Path(source).suffix == ".html" else _drawn
    return _require_counts(reader(source), source)


# -- what pytest collects -------------------------------------------------------


@cache
def _collected(selection: tuple[str, ...]) -> int:
    """How many tests pytest collects for one selection, counted from node ids.

    Collection runs in a subprocess for two reasons. It is the command the
    failure message tells a reader to run, so what this test checks and what they
    can reproduce by hand are the same thing; and importing the suite in the
    session that is already running it would re-enter this module's own
    collection. The spool directory keeps `--alluredir` from writing into the
    results a report may be built from. It costs about a fifth of a second and is
    cached per selection.
    """
    with tempfile.TemporaryDirectory() as spool:
        try:
            proc = subprocess.run(
                [
                    sys.executable, "-m", "pytest", "--collect-only", "-q",
                    "-p", "no:cacheprovider", f"--alluredir={spool}", *selection,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONPATH": f"{ROOT / 'src'}{os.pathsep}{ROOT}"},
                timeout=COLLECT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            pytest.fail(
                f"pytest --collect-only {shlex.join(selection)} did not finish within "
                f"{COLLECT_TIMEOUT_SECONDS}s, waiting on collection that normally takes "
                f"a fraction of a second — this is a hang, not a slow machine.\n"
                f"stdout so far: {exc.stdout!r}\nstderr so far: {exc.stderr!r}"
            )
    ids = [line for line in proc.stdout.splitlines() if line.startswith("tests/") and "::" in line]
    assert ids, (
        f"pytest {shlex.join(selection)} collected nothing, so this test cannot check "
        f"anything.\n{proc.stdout}\n{proc.stderr}"
    )
    return len(ids)


# -- the pins -------------------------------------------------------------------


@pytest.mark.parametrize(("source", "box", "unit"), list(CLAIMS), ids=lambda v: v)
def test_a_figure_states_the_number_the_code_holds(source: str, box: str, unit: str) -> None:
    drawn = _found(source).get((box, unit))
    assert drawn is not None, (
        f'{_path(source)} no longer states a count of "{unit}" under "{box}". Either '
        f"the box was renamed, in which case fix CLAIMS in this file, or the count was "
        f"dropped from the drawing."
    )
    expected = CLAIMS[(source, box, unit)]
    assert drawn == expected, (
        f'{_path(source)} states {drawn} {unit} for "{box}", but the code holds '
        f"{expected}.\n" + FIX.get(Path(source).suffix, FIX_DEFAULT).format(source=source)
    )


@pytest.mark.parametrize(("source", "box", "unit"), list(SELECTIONS), ids=lambda v: v)
def test_a_figure_states_the_number_of_cases_pytest_collects(source: str, box: str, unit: str) -> None:
    selection = SELECTIONS[(source, box, unit)]
    drawn = _found(source).get((box, unit))
    assert drawn is not None, (
        f'{_path(source)} no longer states a count of "{unit}" under "{box}". Either '
        f"the box was renamed, in which case fix SELECTIONS in this file, or the count "
        f"was dropped from the drawing."
    )
    collected = _collected(selection)
    assert drawn == collected, (
        f'{_path(source)} states {drawn} for "{box}", but pytest collects {collected}: '
        f"`pytest {shlex.join(selection)}`.\nA test was added, removed or re-marked. "
        + FIX.get(Path(source).suffix, FIX_DEFAULT).format(source=source)
    )


def test_every_count_a_figure_states_is_one_this_module_checks() -> None:
    """Without this, a loose reading of the files would pass by finding nothing."""
    found = {(source, box, unit) for source in SOURCES for box, unit in _found(source)}
    assert found == set(CHECKED), (
        "the counts found in the drawings and the cover are not the ones this module "
        f"checks.\n  found:   {sorted(found)}\n  checked: {sorted(CHECKED)}\n"
        "A count added to a figure needs a line in CLAIMS naming the value the code "
        "holds for it, or in SELECTIONS naming the pytest selection it describes; if "
        "nothing was found at all, the figures or the way this module reads them have "
        "changed."
    )


# -- numbers that are not counts ------------------------------------------------


def _cron() -> str:
    """The nightly schedule, as the workflow spells it."""
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    schedules = re.findall(r'-\s*cron:\s*"([^"]+)"', workflow)
    assert len(schedules) == 1, (
        f".github/workflows/ci.yml has {len(schedules)} schedule(s), not one: "
        f"{schedules}. The figure names the nightly schedule, so this test needs to "
        f"know which one it means."
    )
    return schedules[0]


def _marker(name: str) -> str:
    """A marker name, refused unless `pyproject.toml` registers it.

    The drawing quotes the command a reader can run (`pytest -m e2e`), and the
    suite runs under `--strict-markers`: a marker that was renamed makes that
    command an error, and the picture would go on printing it.
    """
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    registered = {
        entry.split(":", 1)[0].strip()
        for entry in config["tool"]["pytest"]["ini_options"]["markers"]
    }
    assert name in registered, (
        f"pyproject.toml does not register a {name!r} marker; it registers "
        f"{', '.join(sorted(registered))}. A figure naming a marker the suite does not "
        f"have is printing a command that fails."
    )
    return name


def _python_floor() -> str:
    """The Python version this project requires, without the comparison."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requires = config["project"]["requires-python"]
    floor = requires.lstrip("><=~^ ")
    assert floor and floor[0].isdigit(), (
        f"pyproject.toml requires-python is {requires!r}, which this test cannot read "
        f"a version out of — the cover names one, so the two have to be comparable."
    )
    return floor


#: The numbers on these files that are not counts of anything: a schedule, a file
#: name, a version. Each is a string the code holds, so the picture cannot go on
#: naming something the project stopped doing.
LITERALS: dict[tuple[str, str], str] = {
    ("architecture.svg", "the published database"): DATABASE_FILE,
    ("pipeline.svg", "the nightly schedule"): _cron(),
    ("pipeline.svg", "the browser marker"): _marker("e2e"),
    (COVER, "the Python version"): _python_floor(),
}


def _visible_text(source: str) -> str:
    """Everything a reader — or a screen reader — gets out of one file.

    For a figure that is its title, its description and every line of text in it;
    the stylesheet and the coordinates are not read to anybody. For the cover it
    is the markup's own text, with `<style>` and `<script>` left out for the same
    reason.
    """
    if Path(source).suffix == ".html":
        return " ".join(_read_cover(source).text)
    tree = ET.parse(ASSETS / source)
    spoken = [f"{SVG}title", f"{SVG}desc", f"{SVG}text"]
    return " ".join(
        (element.text or "") for tag in spoken for element in tree.iter(tag)
    )


@pytest.mark.parametrize(("source", "what"), list(LITERALS), ids=lambda v: v)
def test_a_figure_spells_a_setting_the_way_the_project_spells_it(source: str, what: str) -> None:
    expected = LITERALS[(source, what)]
    assert expected in _visible_text(source), (
        f'{_path(source)} no longer shows {what} as "{expected}", which is how the '
        f"project spells it today. Either the drawing is out of date, or the setting "
        f"moved and this pin needs to follow it."
    )


@pytest.mark.parametrize("source", SOURCES)
def test_no_number_on_these_files_escapes_this_module(source: str) -> None:
    """The structured readers only see numbers written the way they expect.

    A count added to a drawing in some other shape — inside a sentence, in a box
    with no title, in the description a screen reader hears — would be a number
    nobody checks, and the readers above would never mention it. So every number
    this module has checked is struck out of the text of each file, longest
    first, and what is left may not contain a digit.
    """
    pinned = [str(value) for (owner, _b, _u), value in CLAIMS.items() if owner == source]
    pinned += [
        str(_collected(selection))
        for (owner, _b, _u), selection in SELECTIONS.items()
        if owner == source
    ]
    pinned += [value for (owner, _w), value in LITERALS.items() if owner == source]
    remainder = _visible_text(source)
    for value in sorted(pinned, key=len, reverse=True):
        remainder = remainder.replace(value, " ")
    leftover = DIGITS.search(remainder)
    assert leftover is None, (
        f"{_path(source)} shows a number this module does not check: "
        f"...{remainder[max(0, leftover.start() - 60):leftover.start() + 60].strip()}...\n"
        f"Every number on a drawing has to be pinned to something — a constant in "
        f"CLAIMS, a pytest selection in SELECTIONS, or a setting in LITERALS — or it "
        f"is a number that can rot without anybody noticing."
    )


# -- the readers themselves ------------------------------------------------------
#
# Everything above stands on three readers. These hold them to reading what is
# written: a count they must find, a reading they must refuse, and the real files
# with one digit changed.


def test_the_svg_reader_reads_a_count_out_of_the_box_that_holds_it(tmp_path: Path) -> None:
    """A count belongs to the box it sits inside, and a box is titled by its top line.

    The last text in this figure is shaped exactly like a count and sits in no
    box at all, which is what a number dropped into a drawing without a home
    looks like. The reader does not report it, and the guard over the raw text
    is what catches it instead.
    """
    figure = tmp_path / "written-for-this-test.svg"
    figure.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">'
        '<rect class="zone" x="0" y="0" width="200" height="200"/>'
        '<rect class="box" x="10" y="10" width="100" height="60"/>'
        '<text x="20" y="30">the demo store</text>'
        '<text x="20" y="50">40 products</text>'
        '<text x="20" y="180">7 things nobody drew a box around</text>'
        "</svg>",
        encoding="utf-8",
    )
    assert _counts_in_svg(figure, "written for this test") == {("the demo store", "products"): 40}


def test_a_figure_stating_nothing_fails_instead_of_passing_over_an_empty_reading() -> None:
    """A reading that matched nothing is a failure, not a pass over an empty set."""
    with pytest.raises(AssertionError, match="states no counts at all"):
        _require_counts({}, "architecture.svg")


def test_a_number_changed_in_a_figure_is_one_this_module_would_catch() -> None:
    """The pin bites: the reader reads the digits in the file, not a constant."""
    source, box, unit = "architecture.svg", "the demo store", "products"
    stated = _found(source)[(box, unit)]
    assert stated == CLAIMS[(source, box, unit)], "precondition: the figure is right today"

    text = (ASSETS / source).read_text(encoding="utf-8")
    tampered_text = text.replace(f">{stated} {unit}<", f">{stated + 1} {unit}<", 1)
    assert tampered_text != text, (
        f'no line reading "{stated} {unit}" is in {_path(source)}: this test edits the '
        f"figure as it is written, so a figure written differently needs it rewritten too"
    )
    tampered = ET.fromstring(tampered_text)
    bodies = [(element.text or "").strip() for element in tampered.iter(f"{SVG}text")]
    assert f"{stated + 1} {unit}" in bodies, (
        "the reader did not see the changed digit, so it is not reading the drawing"
    )
    assert f"{stated} {unit}" not in bodies


def test_the_cover_reader_reads_the_counts_a_card_states() -> None:
    """Number, unit and label are read from the markup, and muted text is skipped."""
    parser = _Stats("written for this test")
    parser.feed(
        "<html><head><style>.n { font-size: 54px }</style></head><body>"
        '<div class="card stat"><span class="layer stat-of">the demo store</span>'
        '<span class="n stat-n">40</span><span class="u stat-u">products</span></div>'
        "</body></html>"
    )
    parser.close()
    assert parser.found == {("the demo store", "products"): 40}
    assert "54px" not in " ".join(parser.text), "the stylesheet is not text a reader sees"


def test_a_cover_count_with_no_label_is_a_failure_rather_than_a_silent_skip() -> None:
    """A number nobody can look up is a number this module cannot check."""
    parser = _Stats("written for this test")
    with pytest.raises(AssertionError, match="stat-of"):
        parser.feed('<div class="stat"><span class="stat-n">40</span></div>')


def test_a_number_changed_on_the_cover_is_one_this_module_would_catch() -> None:
    """The same proof for the template the profile's image is exported from."""
    label, unit = "the demo store", "products"
    stated = _stated(COVER)[(label, unit)]
    assert stated == CLAIMS[(COVER, label, unit)], "precondition: the cover is right today"

    text = (ASSETS / COVER).read_text(encoding="utf-8")
    tampered_text = text.replace(f'"n stat-n">{stated}<', f'"n stat-n">{stated + 1}<', 1)
    assert tampered_text != text, (
        f"no card stating {stated} is in {_path(COVER)}: this test edits the cover as "
        f"it is written, so a cover written differently needs it rewritten too"
    )
    parser = _Stats(COVER)
    parser.feed(tampered_text)
    parser.close()
    assert parser.found[(label, unit)] == stated + 1, (
        "the reader did not see the changed digit, so it is not reading the cover"
    )
    assert parser.found[(label, unit)] != CLAIMS[(COVER, label, unit)]


# -- the images the profile shows ------------------------------------------------


def _png_size(path: Path) -> tuple[int, int]:
    """The dimensions a PNG carries in its own header.

    Read here rather than imported from `scripts/make-assets.py`: that file is a
    script with a hyphen in its name, and importing it would start Playwright to
    read twenty-four bytes.
    """
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n" and header[12:16] == b"IHDR", (
        f"{path.name} is not a PNG: it begins {header[:8]!r}."
    )
    return struct.unpack(">II", header[16:24])


@pytest.mark.parametrize(
    ("name", "size"),
    [
        ("guru-cover-image.png", (1536, 1024)),
        ("allure-report-screenshot.png", (1536, 1024)),
        ("guru-profile-banner-1000x250.png", (1000, 250)),
    ],
)
def test_a_committed_image_is_the_size_the_profile_expects(name: str, size: tuple[int, int]) -> None:
    """The profile crops anything else, and nobody chose that crop.

    The banner also carries its size in its own name, so a file that no longer
    matches it is a file whose name lies.
    """
    path = ROOT / name
    assert path.is_file(), (
        f"{name} is not in the repository. It is a tracked file the profile shows; "
        f"export it with `PYTHONPATH=. python scripts/make-assets.py` and commit it."
    )
    assert _png_size(path) == size, (
        f"{name} is {'x'.join(map(str, _png_size(path)))}, not the "
        f"{'x'.join(map(str, size))} the profile expects."
    )


# -- one fact, one home ----------------------------------------------------------
#
# The figures are not the only place this project could state the same thing
# twice. These pin the copies the code itself keeps, for the same reason: two
# copies of one truth drift, and the cheapest moment to hear about it is here.


def test_the_command_line_and_the_sources_package_know_the_same_sources() -> None:
    from scrapewatch import cli

    assert cli.KNOWN_SOURCES is KNOWN_SOURCES


def test_every_known_source_has_a_catalogue_size() -> None:
    """A source with no stated size is one the nightly live check cannot hold to one."""
    assert set(FULL_RUN_SIZES) == set(KNOWN_SOURCES)


def test_the_demo_stores_stated_size_is_the_catalogue_it_ships() -> None:
    """The one catalogue in this repository can be counted rather than believed.

    Counted on two different days, because the demo store moves its prices daily:
    what moves is the price, not the number of products, and a change that broke
    that would make the figures wrong on some days and right on others.
    """
    assert len(catalogue_for(date(2026, 1, 1))) == FULL_RUN_SIZES["demo"]
    assert len(catalogue_for(date(2026, 9, 22))) == FULL_RUN_SIZES["demo"]


def test_the_books_catalogue_divides_into_whole_pages() -> None:
    """The figure states a page count; it is only a number if the division is exact."""
    assert FULL_RUN_SIZES["books"] % BOOKS_PAGE_SIZE == 0


def test_the_page_names_every_source_the_project_scrapes() -> None:
    """`showcase.build` keeps its own copy of the names, to stay free of the package.

    The lede counts on it: "all N sources this project scrapes" is a claim about
    every source there is, and it is drawn from that copy.
    """
    from showcase.build import SOURCE_LABELS

    assert set(SOURCE_LABELS) == set(KNOWN_SOURCES)


def test_a_source_that_never_ran_reads_like_one_that_did() -> None:
    """`run-stats.json` has one shape, whoever wrote the entry.

    `run_sources` writes the numbers for a source it ran; the CLI writes the entry
    for a source the probe dropped before the run. A key in one and not the other
    is a hole in the page's table that only shows up on a night something was
    skipped.
    """
    from scrapewatch.cli import _ZERO_SOURCE_STATS
    from scrapewatch.pipeline.run import _ZERO_STATS

    assert set(_ZERO_SOURCE_STATS) == set(_ZERO_STATS)


def test_the_files_the_cli_exports_are_the_files_the_page_publishes() -> None:
    """`--export-dir` writes `<source>.<format>`; the page links exactly those.

    The database is the one published file no export produces — it is the storage
    itself, copied in by the publish step — so it is the one name taken out of the
    comparison rather than left to look like a missing export.
    """
    from scrapewatch.cli import EXPORT_FORMATS
    from showcase.build import DATA_FILES

    exported = {f"{source}.{fmt}" for source, formats in EXPORT_FORMATS.items() for fmt in formats}
    published = {name for name, _description in DATA_FILES} - {DATABASE_FILE}
    assert exported == published
