# Web Scraping Automation Framework

[![CI](https://github.com/WolfGung/Web-Scraping-Automation-Framework/actions/workflows/ci.yml/badge.svg)](https://github.com/WolfGung/Web-Scraping-Automation-Framework/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue)](pyproject.toml)

ScrapeWatch collects three catalogues, stores every collection as a snapshot, compares tonight's with last night's, and publishes the data together with the difference. It runs itself every night and leaves the evidence where anyone can open it.

[![The project cover. On the left, the title "Web Scraping Automation Framework" over the claim "Three sources, one pipeline - and a report that says what changed since last night", the pipeline written out as validate, normalise, snapshot, diff, report, and a row of the tools used. On the right, three cards: books.toscrape.com, static HTML over a polite client, 1000 books; quotes.toscrape.com, rendered first in a real browser, 100 quotes; the demo store, ships here and moves every day, 40 products. A badge underneath reads 1140 records in one full run, 3 sources, one pipeline.](guru-cover-image.png)](https://wolfgung.github.io/Web-Scraping-Automation-Framework/)

## Start with what it collected

Last night's run is published whole, not summarised:

- **[The page](https://wolfgung.github.io/Web-Scraping-Automation-Framework/)** — built from that run's own `run-stats.json` and change report, so every figure on it came out of the run it describes.
- **The data, as files you can open:** [`books.csv`](https://wolfgung.github.io/Web-Scraping-Automation-Framework/data/books.csv), [`books.json`](https://wolfgung.github.io/Web-Scraping-Automation-Framework/data/books.json), [`quotes.json`](https://wolfgung.github.io/Web-Scraping-Automation-Framework/data/quotes.json), [`demo.json`](https://wolfgung.github.io/Web-Scraping-Automation-Framework/data/demo.json) — and [the SQLite database itself](https://wolfgung.github.io/Web-Scraping-Automation-Framework/data/scrapewatch.sqlite3), which is what tonight's diff was made against.
- **[What changed since the night before](https://wolfgung.github.io/Web-Scraping-Automation-Framework/changes.html)** — the change report, also published [as JSON](https://wolfgung.github.io/Web-Scraping-Automation-Framework/changes.json) for anything that wants to read it.
- **[The recording](https://wolfgung.github.io/Web-Scraping-Automation-Framework/media/scroll.webm)** — Chromium walking the demo store's infinite scroll to the end, as it happened.
- **[The same scroll in the Playwright trace viewer](https://trace.playwright.dev/?trace=https://wolfgung.github.io/Web-Scraping-Automation-Framework/media/scroll-trace.zip)** — steppable action by action, with the page's DOM at each step.
- **[The Allure report](https://wolfgung.github.io/Web-Scraping-Automation-Framework/report/)** of the whole suite, with the trend carried over from the previous publication.

## What it scrapes, and through which door

| Source | Door | Why that door |
| --- | --- | --- |
| books.toscrape.com — a catalogue of 1000 books over 50 catalogue pages | the polite HTTP client, [`src/scrapewatch/http.py`](src/scrapewatch/http.py) | the markup arrives in the response. Starting a browser to read it would cost a process, a few hundred megabytes and a class of flakiness, and buy nothing. |
| quotes.toscrape.com's `/js/` page — 100 quotes | Chromium through Playwright, [`src/scrapewatch/browser.py`](src/scrapewatch/browser.py) | the page carries its quotes as a JSON array inside a `<script>` and builds the markup itself, after load. |
| the demo store, [`src/scrapewatch/demo_store/`](src/scrapewatch/demo_store) — a catalogue of 40 products, its API handing back 12 products a page | the same polite client, against its JSON API | it ships inside this repository, and it is the one catalogue here that actually moves. |

The second row is a claim, so it is proven rather than asserted. [`tests/fixtures/quotes/js.html`](tests/fixtures/quotes/js.html) is that page as plain HTTP fetches it, saved to disk; [`test_the_unrendered_js_page_has_no_quotes_in_its_html`](tests/parsers/test_quotes_parser.py) runs the parser over it and finds nothing at all, and the next test runs the same parser over what a browser rendered and finds every quote. A browser is started where the markup is built by script, and nowhere else.

The third row is the honest part. Both practice sites are fixed teaching catalogues that do not change: a monitor pointed only at them would report "no change" every night for ever, which proves nothing about the monitor. So this repository ships a store of its own — a FastAPI application whose prices and stock move every day by a rule seeded from the date ([`demo_store/catalogue.py`](src/scrapewatch/demo_store/catalogue.py)) — and the published change report has real movement to name. The same date always produces the same catalogue, so a second run on one day correctly reports nothing new.

## Politeness is code, not a promise

Every HTTP request in this project goes through one client, [`src/scrapewatch/http.py`](src/scrapewatch/http.py), so no source can forget the rules and none of them can be read as a good intention:

- It **waits 0.5 s between two requests to the same host** — a per-host clock, not a global sleep, so one slow site does not slow down another ([`test_it_waits_between_requests_to_the_same_host`](tests/unit/test_http_client.py)).
- It **retries a dropped connection or a `5xx` up to 3 times**, backing off between attempts, and never retries a `4xx` — a refusal is an answer ([`test_it_retries_a_5xx_and_counts_the_retry`](tests/unit/test_http_client.py), `test_it_does_not_retry_a_4xx`).
- It **says who it is**: a User-Agent naming the project and linking this repository, so an operator reading their own logs can tell what this is ([`test_it_identifies_itself`](tests/unit/test_http_client.py)).
- It **reads robots.txt through itself**, so that fetch waits its turn and counts like any other ([`test_the_robots_fetch_is_a_request_like_any_other`](tests/unit/test_http_client.py)), and it **honours what it finds**: a `Disallow` refuses, a `404` means there is no file and nothing is restricted, and a robots.txt that could not be read at all — a `5xx` that survived the retries, or a dropped connection — also refuses, with the reason recorded. A scraper that cannot read the rules does not get to assume there are none.

A refusal is not a crash: the source is skipped, the run continues, and `run-stats.json` carries the reason in words. [`docs/02-politeness.md`](docs/02-politeness.md) states the rules and shows what that entry looks like.

## The pipeline

[![Four bands. The three doors and the sites they reach; the pipeline underneath them in five steps - validate, normalise, snapshot, diff, report; storage as SQLAlchemy over SQLite or PostgreSQL; and at the bottom what a night leaves behind: the exports, the change report, the Allure report and the published page.](showcase/assets/architecture.svg)](showcase/assets/architecture.svg)

One path, whatever the door: a source yields raw records, [`pipeline/run.py`](src/scrapewatch/pipeline/run.py) validates them against a typed model, normalises them into comparable fields (a price becomes a `Decimal` and a currency, `In stock (22 available)` becomes a flag and a count), writes the snapshot, diffs it against the previous snapshot of the same source, and reports what moved. One malformed record is counted and isolated rather than discarding the fetch around it; one broken source is recorded as skipped rather than aborting the night.

[![Two lanes. The gate, run on every push and pull request and touching no network: lint, then the checks that need no site, then the browser check. The night, run on a schedule: probe the practice sites, restore the database from the last publication, scrape and export, record the scroll, run the live checks, then merge every job's results and publish the page. Underneath, the database being carried from one night to the next.](showcase/assets/pipeline.svg)](showcase/assets/pipeline.svg)

Storage is SQLAlchemy over SQLite by default — one clone and it runs — or PostgreSQL through the Compose profile. The database URL is the only difference between the two.

## Run it

Two commands, and nothing on the machine but Python 3.12. Neither of them touches a site this project scrapes.

```bash
make install          # the project and its dev extras, editable, plus Chromium
make test             # the whole gate: every check that needs no network
```

`make install` also installs the browser, because `make test` drives one: the gate includes the browser checks, and a clean clone whose first command fails for a missing Chromium has failed for a reason that has nothing to do with the code. It asks for the browser's OS packages too, which needs root; if that is refused it installs the browser alone and says so, which on most machines is all that was needed.

`make test` is `pytest -m "not live"`. It ends green with no site reachable at all — the parsers run against pages saved under [`tests/fixtures/`](tests/fixtures), and the browser checks drive a demo store the suite starts itself on loopback.

Then watch it actually collect something — the store in one terminal, the scrape in another (or skip both and use the Docker one-liner below, which runs the pair for you). In one terminal:

```bash
scrapewatch demo-store        # the demo store on http://127.0.0.1:8765
```

and in another:

```bash
scrapewatch scrape demo --out data/run
```

The scrape prints one line per source — how many records it collected, over how many pages, what that cost in requests, and how many changes it found against the last snapshot — and then the paths it wrote: `data/run/run-stats.json`, `data/run/change-report.json` and `data/run/change-report.html`. The database appears at `data/scrapewatch.sqlite3` — nothing had to be created first. Run the same command again tomorrow and the report stops saying that nothing changed — the demo store's prices will have moved overnight. `scrapewatch report --html --out changes.html` rebuilds that report from storage at any time.

## Docker

Nothing on the machine but Docker — no Python, no Playwright, no browser, no database:

```bash
docker compose run --rm scrape
```

That starts the demo store as a service, scrapes it from a second container, and keeps the SQLite database on a named volume, so the next run has a yesterday to compare with. The image is built once and reused after that — `--build` is what picks up a change to the code. PostgreSQL is the same image and the same command behind a profile, with the database URL pointed elsewhere:

```bash
docker compose --profile postgres run --rm scrape-pg
docker compose --profile postgres down -v      # stop the store and the database, and drop the volumes
```

## Coverage

Counted by `pytest --collect-only`, and pinned by [`tests/unit/test_readme_pins.py`](tests/unit/test_readme_pins.py), so a number in this table cannot drift away from the suite it describes:

| Marker | What it proves | Cases | Network |
| --- | --- | --- | --- |
| `unit` | pure logic and the project's own tooling: normalisation, the diff, the polite client against a stub transport, the CLI, the demo catalogue, the page builder, and these pins | 214 | none |
| `parsers` | the parsers, against pages saved from the real sites — including the proof that the unrendered `/js/` page holds no quotes | 7 | none |
| `integration` | the pipeline and storage against a real SQLite file: snapshots, retention, exports, a decimal that survives the round trip | 24 | none |
| `e2e` | Chromium and the demo store, started by the suite: render, scroll to the end, log in, and a failed login that fails fast | 12 | loopback only |
| `live` | the practice sites themselves: the parsers still fit their markup, and a full run collects the whole catalogue | 3 | the real sites |
| the gate, `pytest -m "not live"` | the four rows above it | 257 | none |

The gate is what every push and every pull request runs. The `live` row is opt-in (`make test-live`) and runs on the nightly schedule, where a red check is the drift signal this project exists to produce — the night is not allowed to fail because of it, and the published page states it instead.

## What it deliberately does not do

Plainly, because these are choices and not gaps:

- **No proxies, no proxy rotation, no residential IP pools.** A scraper that needs to hide which machine it is coming from is answering a "no" it has already been given.
- **No CAPTCHA solving, and no attempt to look like a human being.** The client identifies itself in every request, by name and with a link back here.
- **No scraping behind a login that forbids it.** The one login this project drives is on the demo store it ships, in a browser check ([`tests/e2e/test_browser_engine.py`](tests/e2e/test_browser_engine.py)) — the mechanism is demonstrated where it is nobody else's server and nobody else's terms.
- **No personal data.** Books, quotes and invented products; nothing here collects a person.
- **No site without a permissive robots policy.** The client refuses on a `Disallow` and refuses on a robots.txt it could not read, and both practice sites were checked before a line of this was written ([`tests/fixtures/README.md`](tests/fixtures/README.md)).

## The nightly publication

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs the gate on every push and pull request. On the nightly schedule (`0 5 * * *`) it does the rest, in this order: probe both practice sites and record exactly what each answered; restore the database from the last publication; scrape every source that answered and export what it collected; record the scroll; run the live checks without letting them stop the night; then merge all three jobs' Allure results and publish the page, the report, the data and the media to GitHub Pages.

A site that did not answer is skipped with its reason in `run-stats.json`, and the page prints that reason — a night that collected two sources out of three says so, rather than quietly publishing a smaller number.

**The database is part of the publication.** It goes out as [`data/scrapewatch.sqlite3`](https://wolfgung.github.io/Web-Scraping-Automation-Framework/data/scrapewatch.sqlite3), and [`scripts/restore-db.sh`](scripts/restore-db.sh) pulls it back down before the next scrape. That is the only reason a nightly diff has a yesterday at all: a runner starts with an empty checkout, and without it every night would be a first night with every record reported as added. Its history is bounded on purpose — the last 30 nights per source, applied by `scrapewatch scrape --keep-snapshots` — because a published file that grows for ever is a download nobody makes.

## Reading further

- [`docs/01-what-it-scrapes-and-why.md`](docs/01-what-it-scrapes-and-why.md) — why these two sites and a store of our own, why an external id is a slug or a hash, and why the page leads with the data.
- [`docs/02-politeness.md`](docs/02-politeness.md) — the rules the client enforces, where each one lives in the code, and what a refusal looks like when it happens.
