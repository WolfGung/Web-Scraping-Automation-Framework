"""The demo store is a real HTTP server; the demo source scrapes it like any other."""
from __future__ import annotations

import pytest

from scrapewatch.config import Settings
from scrapewatch.http import PoliteClient
from scrapewatch.sources.demo import DemoSource

pytestmark = pytest.mark.e2e


def test_the_demo_source_walks_every_page(demo_store_url: str) -> None:
    with PoliteClient(Settings(min_request_interval_s=0)) as client:
        records = list(DemoSource(client, demo_store_url).fetch())
    assert len(records) == 40 and len({r.external_id for r in records}) == 40


def test_members_page_needs_a_login(demo_store_url: str) -> None:
    import httpx

    assert httpx.get(f"{demo_store_url}/members", follow_redirects=False).status_code == 302
