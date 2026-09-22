"""Sources: things that fetch records over the network. See `sources.base.Source`.

This module also keeps the few facts that are true of the sources themselves
rather than of any one run: which sources exist, how much each of them holds
when nothing is capped, and how many books one listing page of
books.toscrape.com carries. They live here, above the individual source modules
and above the CLI, because several readers need them and a fact with two homes
is a fact that drifts: the CLI resolves `scrape all` from `KNOWN_SOURCES`,
`tests/live/test_full_run_sizes.py` holds a night's collection to
`FULL_RUN_SIZES`, and the drawings under `showcase/assets/` state both — pinned
by `tests/unit/test_showcase_figures.py`, so a picture cannot go on claiming a
number the code no longer holds.
"""
from __future__ import annotations

#: Every source this project knows about, in the order `scrape all` walks them —
#: which is also the order stats, exports and the published page list them in.
#: `scrapewatch.cli` imports this rather than keeping a second list of names.
KNOWN_SOURCES: tuple[str, ...] = ("books", "quotes", "demo")

#: The known catalogue sizes of the practice sites and the demo store: what an
#: uncapped run of each source collects. books.toscrape.com and
#: quotes.toscrape.com are fixed teaching catalogues that do not grow, and the
#: demo store's catalogue ships in this repository
#: (`scrapewatch.demo_store.catalogue`), so all three are facts the code can
#: state. The nightly live check asserts them against what was actually
#: collected (`tests/live/test_full_run_sizes.py`), which is how a site that
#: quietly changed shape — or a parser that stopped following pagination —
#: becomes a red check rather than a smaller number nobody notices.
FULL_RUN_SIZES: dict[str, int] = {"books": 1000, "quotes": 100, "demo": 40}

#: How many books one listing page of books.toscrape.com carries. The catalogue
#: is walked page by page, so this is what turns a number of books into a number
#: of pages: `FULL_RUN_SIZES["books"] // BOOKS_PAGE_SIZE` is the 50 catalogue
#: pages the architecture drawing states.
BOOKS_PAGE_SIZE = 20
