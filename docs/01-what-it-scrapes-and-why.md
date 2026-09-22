# What it scrapes, and why those three

The reasoning behind the three sources, the identifiers they are stored under, and
the shape of the published page — the README states what the project does, this
states why it was built that way.

## Two practice sites, chosen for their difference

[books.toscrape.com](https://books.toscrape.com) and
[quotes.toscrape.com](https://quotes.toscrape.com) exist to be scraped: they are
published for exactly this, they are stable, and neither of them is somebody's
business being inconvenienced by a portfolio project. That much only makes them
safe. What makes them worth having is that they are not the same problem twice.

The book catalogue — 1000 books over 50 listing pages — is ordinary server-rendered
markup. Everything a record needs is in the response, so the polite HTTP client
reads it and nothing else is started. Choosing *not* to open a browser is the
decision on display here: a browser for a static page costs a process, a few
hundred megabytes and a class of flakiness, and buys nothing.

The quotes site's `/js/` page — 100 quotes — is the deliberate opposite. It ships
its quotes as a JSON array inside a `<script>` block and renders them into markup
after load, so an HTTP client sees a page with no quotes in it at all. That claim
is not left as prose: the page as plain HTTP fetches it is saved at
[`tests/fixtures/quotes/js.html`](../tests/fixtures/quotes/js.html), and one test runs the parser over it and
asserts it finds nothing while the next runs the same parser over browser-rendered
markup and finds every quote. Two doors, and a test that says which door each page
needs.

## A store of our own, because the practice sites never move

Both catalogues are fixed. Scrape either one twice and the diff is empty — not
because change detection works, but because nothing changed. A monitor whose whole
output is "no change", for ever, has demonstrated nothing, and the published page
would be a table of zeroes.

So this repository ships a store: a FastAPI application with a listing, a paginated
JSON API, an infinite-scroll page and a login, serving 40 products whose prices and
stock move a little every day. The daily move is seeded from the date itself
([`demo_store/catalogue.py`](../src/scrapewatch/demo_store/catalogue.py)), which buys two things at once. The
change report has real movement to name — this product went from this price to that
one, this one went out of stock — and the movement is reproducible: the same date
always yields the same catalogue, so scraping twice in one day correctly reports
nothing new, and a test can assert exactly what two given days differ by.

It also means the demonstration costs nobody else anything: the part of this
project that runs nightly and is expected to find something runs against a server
shipped in the same repository.

## Why an external id is a slug or a hash

Every record is stored under `(source, external_id)`, and the diff ([`pipeline/diff.py`](../src/scrapewatch/pipeline/diff.py)) is keyed by it.
That identifier has one job: to mean the same thing tomorrow. Position cannot do
it — a book that moves to another page is not a different book, and a list that
reorders would otherwise read as everything removed and everything added.

Each source is therefore asked for the most stable name it already has. The book
catalogue has one in its own URLs (`a-light-in-the-attic_1000`), so the slug is
lifted out of the href rather than invented. The demo store has real integer ids,
so those are used. The quotes site has no identifier anywhere: a quote is a text
and an author, and nothing else. So the id is derived from exactly those two — the
first 16 characters of the SHA-1 of the text and the author together. It is stable
because the quote is, it is independent of page order, and it changes if and only
if the quote's own words change, which is the one event that *should* read as a
removal and an addition rather than as an edit.

Hashing is the fallback, not the habit. A source that offers an identifier is taken
at its word; only a source that offers none gets one computed for it.

## Why the published page leads with the data

The page opens with the catalogue that was collected, the changes that were found
and the recording of the browser collecting them, and only then explains itself. A
scraping project is judged by what it brought back, and the fastest way to find out
whether this one works is to open `books.csv` and look; the prose can wait for
whoever is still interested.

The same rule applies to the figures on it: every number comes from the run's own
`run-stats.json` and change report, never from a person typing what they remember.
A page that disagrees with its own data is worse than no page at all.
