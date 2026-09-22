# Politeness, as the code actually enforces it

Every HTTP request this project makes goes through one object,
[`PoliteClient`](../src/scrapewatch/http.py). There is no second code path — a
source is handed a client and fetches only through it — so politeness here is a
property of the program, not a promise in a README.

## The four rules

**Wait between two requests to the same host.** `_wait_for_host()` keeps the last
request time per host and sleeps out the remainder of
`Settings.min_request_interval_s`, which is 0.5 s by default
([`config.py`](../src/scrapewatch/config.py)). Per host, not global: a run that
touches three sites should not serialise itself into one slow queue, and the site
being protected is the one being asked repeatedly. Proven by
`test_it_waits_between_requests_to_the_same_host`, which patches the clock rather
than actually sleeping.

**Retry what is worth retrying, up to 3 times.** A dropped connection or a 500,
502, 503 or 504 is retried after a backoff of `min(2 ** attempt * 0.25, 5.0)`
seconds; every other status is returned or raised as it is. A 4xx is an answer —
retrying it is asking the same question again after being told no, and it is the
kind of "resilience" that turns a scraper into a nuisance. Each retry is counted in
`FetchStats.retries` and ends up in `run-stats.json`, so a night spent fighting a
struggling server is visible rather than hidden in a duration.

**Say who you are.** Every request and every browser context carries
`Settings.user_agent`: `scrapewatch/0.1` and a link to this repository, so an
operator reading their own access log can tell what this is and where to complain.
A scraper disguised as a browser has decided it does not want that conversation.

**Read robots.txt, and honour what it says.** `allowed()` is called by every source
before its first fetch ([`sources/books.py`](../src/scrapewatch/sources/books.py),
`quotes.py`, `demo.py`), and the file is fetched through `get()` like any other
request — so it waits its turn, counts in the stats, and gets the same retries. The
result is cached per origin for the life of the client, so one run asks each site
once.

## What "honour it" means in the three cases that differ

| What robots.txt did | What the client does | Why |
| --- | --- | --- |
| Returned rules containing a matching `Disallow` | Refuses the fetch | The site said no. |
| Answered 404 (or any 4xx) | Allows everything | There is no file, so nothing is restricted. |
| Returned a 5xx that survived the retries, or never answered at all | Refuses the fetch, and records why | The rules could not be read, and a scraper that cannot read the rules does not get to assume there are none. |

The third row is the one worth arguing about, and the argument is short: the
alternative is guessing "probably allowed" on behalf of a server already having a
bad day. `_RobotsState.unreachable_reason` carries the sentence and
`PoliteClient.robots_refusal_reason` hands it to whoever asked.

## What a refusal looks like from the outside

A refused source raises out of `fetch()` with the reason attached. That is not a
crash: [`run_sources`](../src/scrapewatch/pipeline/run.py) catches it, marks the
source skipped, and lets the rest of the night continue. The entry in
`run-stats.json` is the same shape as every other source's, with zeroes where the
numbers would be:

```json
"books": {
  "records": 0,
  "pages": 0,
  "requests": 0,
  "retries": 0,
  "bytes": 0,
  "seconds": 0,
  "kind": "books",
  "skipped": true,
  "reason": "books: fetch refused by robots.txt: robots.txt at https://books.toscrape.com returned HTTP 503 after retries",
  "parse_errors": 0,
  "parse_error_reasons": []
}
```

Two details are deliberate. The counters read zero even though fetching robots.txt
did cost a request: a skipped source reports the collection it did not make. And
`reason` is a sentence rather than a code, because the CLI prints it, the published
page states it, and a person has to decide from it whether the site refused or the
night simply failed to ask.
