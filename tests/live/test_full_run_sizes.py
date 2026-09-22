"""The night's collection, against the sizes the code says those catalogues hold.

`scrapewatch.sources.FULL_RUN_SIZES` is a typed claim about somebody else's site:
1000 books, 100 quotes, and the 40 products the demo store ships with. The
drawings on the showcase page state those numbers and `tests/unit` pins them to
this constant, so the constant is checked against the code — but nothing in the
gate can check it against the sites, because the gate never touches them.

This is where that happens. The nightly job scrapes first and runs `pytest -m
live` afterwards, so by the time this check runs the night's own
`data/run/run-stats.json` is on disk: what each source actually collected, and
which of them were skipped before the run because they did not answer the probe.
A source that answered and came back short is the interesting failure — a
catalogue that changed size, or pagination this project stopped following after
the first page — and it reads as a red live check rather than as a smaller
number on the page that nobody compares with anything.

Nothing here scrapes. When the file is not there, nothing scraped before this ran
and the check skips saying so: passing would be claiming a collection that never
happened.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scrapewatch.sources import FULL_RUN_SIZES

pytestmark = pytest.mark.live

ROOT = Path(__file__).resolve().parents[2]

#: Where a run leaves its statistics: `scrapewatch scrape ... --out data/run`, which
#: is what the nightly job runs and what `showcase/build.py` reads afterwards.
RUN_STATS = Path("data/run/run-stats.json")

_DID_NOT_RUN = (
    f"{RUN_STATS} is not there, so no scrape ran before this check. The nightly job "
    f"scrapes and then runs `pytest -m live`; to reproduce it locally, run "
    f"`scrapewatch scrape all --out data/run` (or `scrape demo`, with the demo store "
    f"up) first. This check reads that file and never scrapes anything itself."
)


def _sources_of(path: Path) -> dict[str, dict]:
    """The per-source block of `run-stats.json`, or a failure naming what was there."""
    body = json.loads(path.read_text(encoding="utf-8"))
    sources = body.get("sources") if isinstance(body, dict) else None
    assert isinstance(sources, dict) and sources, (
        f"{RUN_STATS} carries no 'sources' object, so there is nothing to check it "
        f"against: it holds {sorted(body) if isinstance(body, dict) else type(body).__name__}. "
        f"A run always writes one, even when every source was skipped."
    )
    return sources


def test_every_source_that_ran_collected_its_whole_catalogue() -> None:
    path = ROOT / RUN_STATS
    if not path.is_file():
        pytest.skip(_DID_NOT_RUN)

    sources = _sources_of(path)
    ran = {name: body for name, body in sources.items() if not body.get("skipped")}
    if not ran:
        reasons = ", ".join(
            f"{name}: {body.get('reason') or 'no reason recorded'}" for name, body in sorted(sources.items())
        )
        pytest.skip(
            f"every source in {RUN_STATS} was skipped, so this run collected nothing to "
            f"check against the catalogue sizes — {reasons}"
        )

    unknown = sorted(set(ran) - set(FULL_RUN_SIZES))
    assert not unknown, (
        f"{RUN_STATS} reports {', '.join(unknown)}, which FULL_RUN_SIZES says nothing "
        f"about: it knows {', '.join(sorted(FULL_RUN_SIZES))}. A new source needs its "
        f"catalogue size in `scrapewatch.sources`, or this check silently stops covering it."
    )

    short = [
        f"{name}: collected {body.get('records')} record(s), "
        f"but FULL_RUN_SIZES says {name} holds {FULL_RUN_SIZES[name]}"
        for name, body in sorted(ran.items())
        if body.get("records") != FULL_RUN_SIZES[name]
    ]
    assert not short, (
        "a source answered and came back with a different catalogue than the one this "
        "project states:\n  " + "\n  ".join(short) + "\n"
        "Either the site changed size — in which case `scrapewatch.sources.FULL_RUN_SIZES` "
        "and the figures under showcase/assets/ are now wrong and both need the new number "
        "— or this run was capped (`--max-pages`), or the source stopped following "
        "pagination past its first page."
    )
