"""A demo product store, served in-process, whose catalogue moves a little each day.

Everything a scraper would meet in the wild — a paginated listing, a JSON API behind
it, an infinite-scroll page, a login wall — but labelled as the demo everywhere it
appears, because it exists only to give the nightly change report real work to do.
See `scrapewatch.demo_store.catalogue` for the mechanism and
`scrapewatch.demo_store.app` for the server.
"""
from __future__ import annotations
