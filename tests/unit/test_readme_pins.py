"""Every number the README and the two documents state is a number the code holds.

The README is the page a reader judges this project by, and it is written in
prose: a catalogue that changed size, a marker that gained a test, a retention
that was halved all leave it stating something that used to be true. Nobody
re-reads a README against the repository, so the drift has to fail the build.

Three kinds of number live in these documents, and each is read the way it is
written rather than looked up in a table kept beside it:

- the coverage table, whose rows are read out of the markdown and compared
  against what `pytest --collect-only` collects for the selection each row
  names;
- a fact stated in a sentence — a catalogue size, the wait between two
  requests, the nights of history kept — read by the regular expression that
  describes the sentence and compared against the constant the code applies;
- a relative link, resolved against the repository so that a document cannot
  point at a file that has moved.

Two guards sit underneath. A reader that finds nothing fails rather than passing
over an empty reading, and a number written twice in one document has to be
written the same way both times. And `test_no_number_in_the_readme_escapes_this
_module` strikes every number this module checked out of the README's prose and
refuses to find a digit left, so a number added to a sentence cannot escape by
being phrased in a way the readers above do not recognise. It reads prose only:
code spans, fenced blocks and link targets are where file names and versions
live, and those are pinned by being paths that have to resolve.

One number here counts this module's own cases. The `unit` row of the coverage
table includes every test in this file, so adding a pin changes the number the
README states — deliberately: the alternative is a table that quietly stops
describing the suite it claims to describe.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from scrapewatch.cli import DEFAULT_KEEP_SNAPSHOTS
from scrapewatch.config import Settings
from scrapewatch.demo_store.app import PER_PAGE
from scrapewatch.sources import BOOKS_PAGE_SIZE, FULL_RUN_SIZES, KNOWN_SOURCES
from showcase.build import MARKERS

# `_collected` runs `pytest --collect-only` in a subprocess and caches the answer
# per selection; `_cron` and `_python_floor` read the workflow and pyproject. All
# three already exist for the figures and the cover, and a second copy of a reader
# is a second thing to keep in step, so they are borrowed rather than rewritten.
from tests.unit.test_showcase_figures import _collected, _cron, _python_floor

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]

README = "README.md"
DOCS = ("docs/01-what-it-scrapes-and-why.md", "docs/02-politeness.md")
DOCUMENTS = (README, *DOCS)

#: The selection each coverage row describes, keyed by how the row names itself.
#: The five markers are the ones `pyproject.toml` registers (`showcase.build`
#: keeps the list, and its own test holds it to pyproject); the gate is the
#: selection CI actually runs on every push.
GATE = "the gate"
GATE_SELECTION = ("-m", "not live")
COVERAGE_SELECTIONS: dict[str, tuple[str, ...]] = {
    **{marker: ("-m", marker) for marker in MARKERS},
    GATE: GATE_SELECTION,
}


def _settings_default(field: str) -> str:
    """A `Settings` default, read off the class rather than off an instance.

    `Settings()` reads the environment and a `.env` beside it, so an instance
    would tell us what this machine is configured to do. The README states what
    the project does out of the box, which is the field's default.
    """
    return str(Settings.model_fields[field].default)


def _hash_width() -> str:
    """How much of a quote's SHA-1 becomes its `external_id`, read from the source.

    The quotes site offers no identifier, so one is derived; the document
    explains the choice and states the width. The width itself is a slice in
    `sources/quotes.py`, and this reads it there rather than repeating it.
    """
    source = (ROOT / "src" / "scrapewatch" / "sources" / "quotes.py").read_text(encoding="utf-8")
    widths = set(re.findall(r"hexdigest\(\)\[:(\d+)\]", source))
    assert len(widths) == 1, (
        f"src/scrapewatch/sources/quotes.py truncates a hexdigest in {len(widths)} "
        f"different ways ({sorted(widths)}), so this test cannot tell which width the "
        f"document should state."
    )
    return widths.pop()



#: Every number a document states as a fact, with the sentence that states it and
#: the value the code holds. The key is the document and what the number is, so a
#: number moved into a sentence about something else is a failure here rather
#: than something plausible.
CLAIMS: dict[tuple[str, str], tuple[str, str]] = {
    (README, "the book catalogue"): (r"(\d+) books", str(FULL_RUN_SIZES["books"])),
    (README, "the catalogue's pages"): (
        r"(\d+) catalogue pages",
        str(FULL_RUN_SIZES["books"] // BOOKS_PAGE_SIZE),
    ),
    (README, "the quotes catalogue"): (r"(\d+) quotes", str(FULL_RUN_SIZES["quotes"])),
    (README, "the demo catalogue"): (r"(\d+) products\b(?! a page)", str(FULL_RUN_SIZES["demo"])),
    (README, "the demo API's page size"): (r"(\d+) products a page", str(PER_PAGE)),
    (README, "the number of sources"): (r"(\d+) sources", str(len(KNOWN_SOURCES))),
    (README, "a full run's records"): (r"(\d+) records", str(sum(FULL_RUN_SIZES.values()))),
    (README, "the published retention"): (r"(\d+) nights", str(DEFAULT_KEEP_SNAPSHOTS)),
    (README, "the wait between two requests"): (
        r"waits ([\d.]+) s between",
        _settings_default("min_request_interval_s"),
    ),
    (README, "the retry budget"): (r"up to (\d+) times", _settings_default("max_retries")),
    (README, "the Python version"): (r"Python[- ](\d+\.\d+)", _python_floor()),
    (DOCS[0], "the book catalogue"): (r"(\d+) books", str(FULL_RUN_SIZES["books"])),
    (DOCS[0], "the catalogue's pages"): (
        r"(\d+) listing pages",
        str(FULL_RUN_SIZES["books"] // BOOKS_PAGE_SIZE),
    ),
    (DOCS[0], "the quotes catalogue"): (r"(\d+) quotes", str(FULL_RUN_SIZES["quotes"])),
    (DOCS[0], "the demo catalogue"): (r"(\d+) products", str(FULL_RUN_SIZES["demo"])),
    (DOCS[0], "the width of a quote's identifier"): (r"first (\d+) characters", _hash_width()),
    (DOCS[1], "the wait between two requests"): (
        r"which is ([\d.]+) s by default",
        _settings_default("min_request_interval_s"),
    ),
    (DOCS[1], "the retry budget"): (r"up to (\d+) times", _settings_default("max_retries")),
}


# -- reading a document ----------------------------------------------------------


def _text(document: str) -> str:
    return (ROOT / document).read_text(encoding="utf-8")


def _stated(text: str, document: str, what: str, pattern: str) -> str:
    """The number `pattern` finds in `text`, refusing an empty or a contradictory reading.

    Finding nothing is a failure, not a pass: a sentence that was rewritten takes
    its number out of this module's sight, and a pin nobody can apply is worse
    than no pin at all. Finding the same fact written two different ways in one
    document is a failure too — the reader cannot decide which one the code is
    supposed to match, and a reader on paper cannot either.
    """
    matches = re.findall(pattern, text)
    assert matches, (
        f"{document} no longer says anything matching /{pattern}/, so the number for "
        f"{what} is not checked by anything. Either the sentence was rewritten — in "
        f"which case fix the pattern in tests/unit/test_readme_pins.py — or the number "
        f"was dropped from the document."
    )
    unique = sorted(set(matches))
    assert len(unique) == 1, (
        f"{document} states {what} as {' and as '.join(unique)}. One fact, written "
        f"twice, two different ways: whichever is right, the other one is wrong."
    )
    return unique[0]


def _coverage_table(text: str) -> dict[str, int]:
    """The coverage table, read out of the README's markdown, keyed by what each row selects.

    The section is found by its heading and read to the next one, so the other
    table in this file cannot be mistaken for it. A row names its selection in
    the first cell — a marker in backticks, or the gate — and states its count in
    the third.
    """
    sections = re.split(r"^## ", text, flags=re.M)
    coverage = [section for section in sections if section.startswith("Coverage")]
    assert len(coverage) == 1, (
        f"{README} has {len(coverage)} sections titled 'Coverage', not one. This "
        f"module reads the table under that heading, so it has to be able to find it."
    )

    rows: dict[str, int] = {}
    for line in coverage[0].splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4 or cells[0] in ("Marker", "---"):
            continue
        label, count = cells[0], cells[2]
        marker = re.fullmatch(r"`([a-z0-9]+)`", label)
        if marker is not None and marker.group(1) in MARKERS:
            key = marker.group(1)
        elif GATE_SELECTION[1] in label:
            key = GATE
        else:
            raise AssertionError(
                f"{README}: the coverage row beginning {label!r} names neither one of "
                f"the suite's markers ({', '.join(MARKERS)}) nor the gate. Every row of "
                f"that table has to be a selection pytest can be asked for, or its "
                f"number describes nothing."
            )
        assert count.isdigit(), (
            f"{README}: the coverage row for {key} states {count!r} cases, which is not "
            f"a number."
        )
        assert key not in rows, f"{README}: two coverage rows describe {key}."
        rows[key] = int(count)

    assert rows, (
        f"{README} states no coverage table at all: nothing under its Coverage heading "
        f"is a row this module can read. Either the table was removed, or it was "
        f"rewritten in a way this reader no longer understands — in which case the "
        f"numbers in it are no longer checked by anything."
    )
    return rows


#: Every relative link in a document, as it is written. Markdown nests an image
#: inside a link (`[![alt](image)](page)`), so the targets are read one by one
#: rather than by matching a whole link, which would swallow the outer one.
LINK_TARGET = re.compile(r"\]\((?!https?://|mailto:)([^()\s#]*)(#[^()\s]*)?\)")

#: What is not prose: a fenced block, an inline code span, and the target of a
#: link. File names and versions live there, and they are pinned by having to
#: resolve rather than by being numbers.
NOT_PROSE = (
    re.compile(r"```.*?```", re.S),
    re.compile(r"`[^`]*`"),
    re.compile(r"\]\([^()]*\)"),
)

DIGITS = re.compile(r"\d")


def _prose(document: str) -> str:
    """What a reader reads in `document`, with the code and the link targets taken out."""
    text = _text(document)
    for pattern in NOT_PROSE:
        text = pattern.sub(" ", text)
    return text


# -- the pins --------------------------------------------------------------------


@pytest.mark.parametrize(("document", "what"), list(CLAIMS), ids=lambda value: value)
def test_a_document_states_the_number_the_code_holds(document: str, what: str) -> None:
    pattern, expected = CLAIMS[(document, what)]
    stated = _stated(_text(document), document, what, pattern)
    assert stated == expected, (
        f"{document} states {what} as {stated}, but the code holds {expected}. Edit the "
        f"sentence, or the pin in tests/unit/test_readme_pins.py if the fact itself moved."
    )


@pytest.mark.parametrize("selects", list(COVERAGE_SELECTIONS), ids=lambda value: value)
def test_the_coverage_table_states_the_number_of_cases_pytest_collects(selects: str) -> None:
    stated = _coverage_table(_text(README)).get(selects)
    assert stated is not None, (
        f"{README}'s coverage table no longer has a row for {selects}. Either the row "
        f"was dropped, or it was renamed — in which case fix COVERAGE_SELECTIONS in "
        f"tests/unit/test_readme_pins.py."
    )
    selection = COVERAGE_SELECTIONS[selects]
    collected = _collected(selection)
    assert stated == collected, (
        f"{README} states {stated} cases for {selects}, but pytest collects "
        f"{collected}: `pytest {' '.join(selection)} --collect-only`. A test was added, "
        f"removed or re-marked; the table has to follow the suite."
    )


def test_the_coverage_table_describes_every_marker_the_suite_registers() -> None:
    """A marker with no row is a whole layer of the suite the README does not mention."""
    assert set(_coverage_table(_text(README))) == set(COVERAGE_SELECTIONS), (
        f"{README}'s coverage table describes {sorted(_coverage_table(_text(README)))}, "
        f"but the suite's rows are {sorted(COVERAGE_SELECTIONS)}."
    )


def test_the_gate_row_is_the_sum_of_the_rows_above_it() -> None:
    """The table adds up, so a reader can check it without running anything."""
    rows = _coverage_table(_text(README))
    parts = {marker: count for marker, count in rows.items() if marker not in (GATE, "live")}
    assert sum(parts.values()) == rows[GATE], (
        f"{README}'s coverage table states {rows[GATE]} cases in the gate, but its "
        f"other network-free rows add up to {sum(parts.values())} ({parts})."
    )


def test_the_readme_spells_the_nightly_schedule_the_way_the_workflow_does() -> None:
    """The one number in the README that is a setting rather than a count."""
    cron = _cron()
    assert cron in _text(README), (
        f"{README} no longer names the nightly schedule as \"{cron}\", which is how "
        f".github/workflows/ci.yml spells it today."
    )


def test_no_number_in_the_readme_escapes_this_module() -> None:
    """A number added to a sentence has to be a number something here checks.

    Every value this module pinned is struck out of the README's prose, longest
    first, and what is left may not contain a digit. Without it, a new figure in
    a new sentence would simply not be read by any of the patterns above, and
    nothing would say so.
    """
    pinned = [expected for (document, _what), (_pattern, expected) in CLAIMS.items() if document == README]
    pinned += [str(count) for count in _coverage_table(_text(README)).values()]
    remainder = _prose(README)
    for value in sorted(pinned, key=len, reverse=True):
        remainder = remainder.replace(value, " ")
    leftover = DIGITS.search(remainder)
    assert leftover is None, (
        f"{README} states a number this module does not check: "
        f"...{remainder[max(0, leftover.start() - 70):leftover.start() + 70].strip()}...\n"
        f"Every number in the prose has to be pinned to something — a claim in CLAIMS, "
        f"or a row of the coverage table — or it is a number that can rot without "
        f"anybody noticing. A file name or a version belongs in backticks or in a link, "
        f"where it is checked by having to resolve."
    )


@pytest.mark.parametrize("document", DOCUMENTS)
def test_every_relative_link_resolves_to_something_in_the_repository(document: str) -> None:
    """A document that points at a file which has moved is a document nobody can follow."""
    targets = [match.group(1) for match in LINK_TARGET.finditer(_text(document))]
    assert targets, (
        f"{document} has no relative link at all, so this test proves nothing about it. "
        f"Either every link in it became absolute, or the way links are written changed."
    )
    here = (ROOT / document).parent
    missing = [target for target in targets if not (here / target).exists()]
    assert not missing, (
        f"{document} links to {missing}, which {'is' if len(missing) == 1 else 'are'} "
        f"not in the repository. The file moved, was renamed, or was never committed."
    )


# -- the readers themselves --------------------------------------------------------
#
# Everything above stands on two readers. These hold them to reading what is
# written: a reading they must refuse, and the real README with one digit changed.


def test_a_sentence_this_module_cannot_find_is_a_failure_not_a_silent_pass() -> None:
    with pytest.raises(AssertionError, match="no longer says anything matching"):
        _stated("a document stating nothing", "written-for-this-test.md", "the demo catalogue", r"(\d+) products")


def test_one_fact_written_two_different_ways_is_a_failure() -> None:
    with pytest.raises(AssertionError, match="One fact, written"):
        _stated("40 products here and 41 products there", "written-for-this-test.md", "x", r"(\d+) products")


def test_a_coverage_table_stating_nothing_fails_instead_of_passing_over_an_empty_reading() -> None:
    with pytest.raises(AssertionError, match="states no coverage table at all"):
        _coverage_table("## Coverage\n\nNo table here at all.\n")


def test_the_link_reader_finds_a_nested_target_and_leaves_the_absolute_ones_alone() -> None:
    """The cover is an image inside a link; both targets have to be seen, and only those."""
    text = "[![alt](guru-cover-image.png)](https://example.invalid/) and [a](docs/02-politeness.md#rules)"
    assert [match.group(1) for match in LINK_TARGET.finditer(text)] == [
        "guru-cover-image.png",
        "docs/02-politeness.md",
    ]


def test_a_number_changed_in_the_readme_is_one_this_module_would_catch() -> None:
    """The pin bites: the reader reads the digits in the file, not a constant."""
    pattern, expected = CLAIMS[(README, "the demo catalogue")]
    text = _text(README)
    assert _stated(text, README, "the demo catalogue", pattern) == expected, (
        "precondition: the README is right today"
    )

    tampered = text.replace(f"{expected} products", f"{int(expected) + 1} products")
    assert tampered != text, (
        f'no sentence reading "{expected} products" is in {README}: this test edits the '
        f"README as it is written, so a README written differently needs it rewritten too"
    )
    assert _stated(tampered, README, "the demo catalogue", pattern) == str(int(expected) + 1), (
        "the reader did not see the changed digit, so it is not reading the README"
    )
