"""Against the real quotes site: the browser and the site's own API tell one story.

Opt-in (`pytest -m live`), and the one check here that needs both doors at once —
the rendered page from Chromium, the JSON from the polite client.
"""
from __future__ import annotations

import pytest

from scrapewatch.browser import BrowserSession
from scrapewatch.config import Settings
from scrapewatch.http import PoliteClient
from scrapewatch.sources.quotes import QuotesSource

pytestmark = pytest.mark.live


def test_the_rendered_page_and_the_api_agree_on_the_first_ten_quotes(
    browser_session: BrowserSession,
) -> None:
    with PoliteClient(Settings()) as client:
        assert QuotesSource(browser_session, client).cross_check() == 10
