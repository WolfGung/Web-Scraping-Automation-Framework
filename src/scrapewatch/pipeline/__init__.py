"""The pure core of scrapewatch: normalise a fetch, diff two snapshots, report the diff.

Nothing in this package does I/O. It takes ``RawRecord``s and ``Record``s in, and
returns ``Record``s, ``ChangeSet``s and reports out — the parts a fetcher or a store
can be swapped around without touching.
"""
from __future__ import annotations
