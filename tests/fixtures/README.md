# Fixtures: books.toscrape.com

Snapshots of the real site, taken **2026-09-22**, so the `parsers` test gate proves the
`books` parser against real markup without touching the network. A fixture without
provenance is a fixture nobody dares to refresh — if the site's markup changes, the
nightly `live` run (`pytest -m live`) will notice; these files should be re-taken then,
and this note updated with the new date.

Fetched with the client's own User-Agent, three requests total, spaced by hand
(one second apart) to be polite to a demo site that exists for scrapers to practice on:

```bash
curl -sA "scrapewatch/0.1 (+https://github.com/WolfGung/Web-Scraping-Automation-Framework)" \
  https://books.toscrape.com/ -o tests/fixtures/books/index.html

curl -sA "scrapewatch/0.1 (+https://github.com/WolfGung/Web-Scraping-Automation-Framework)" \
  https://books.toscrape.com/catalogue/page-2.html -o tests/fixtures/books/page-2.html

curl -sA "scrapewatch/0.1 (+https://github.com/WolfGung/Web-Scraping-Automation-Framework)" \
  https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html -o tests/fixtures/books/detail.html
```

| File | Source URL |
| --- | --- |
| `index.html` | `https://books.toscrape.com/` — page 1 of 50, 20 book listings |
| `page-2.html` | `https://books.toscrape.com/catalogue/page-2.html` — page 2 of 50 |
| `detail.html` | `https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html` |

`robots.txt` on this host is a 404 (checked 2026-09-22), so nothing here is disallowed.

# Fixtures: quotes.toscrape.com

Snapshot of the `/js/` page and its API twin, taken **2026-09-22**, for the `parsers`
test gate that proves `parse_quotes` against real markup with no network.

`js.html` is the point of `QuotesSource` existing at all: it carries its quotes only
inside a `<script>` block, as a JSON array a client-side script renders into
`div.quote` markup after load. Fetched as plain HTML with `curl`, the page has no
`div.quote` anywhere in it — a parser run on it correctly finds nothing, which is
exactly what `test_the_unrendered_js_page_has_no_quotes_in_its_html` proves. Reading
this page at all requires a browser (`scrapewatch.browser.BrowserSession`), not
another HTTP client.

```bash
curl -sA "scrapewatch/0.1 (+https://github.com/WolfGung/Web-Scraping-Automation-Framework)" \
  https://quotes.toscrape.com/js/ -o tests/fixtures/quotes/js.html

curl -sA "scrapewatch/0.1 (+https://github.com/WolfGung/Web-Scraping-Automation-Framework)" \
  "https://quotes.toscrape.com/api/quotes?page=1" -o tests/fixtures/quotes/api-page-1.json
```

| File | Source URL |
| --- | --- |
| `js.html` | `https://quotes.toscrape.com/js/` — quotes only in a `<script>` block, none in the HTML |
| `api-page-1.json` | `https://quotes.toscrape.com/api/quotes?page=1` — reference data for `QuotesSource.cross_check()` |

`robots.txt` on this host is a 404 (checked 2026-09-22), so nothing here is disallowed.
