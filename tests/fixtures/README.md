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
