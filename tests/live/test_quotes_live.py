from __future__ import annotations

import pytest

from scrapewatch.browser import BrowserSession
from scrapewatch.config import Settings
from scrapewatch.http import PoliteClient
from scrapewatch.sources.quotes import QuotesSource

pytestmark = pytest.mark.live


def test_the_rendered_page_and_the_api_agree_on_the_first_ten_quotes() -> None:
    with PoliteClient(Settings()) as client, BrowserSession(Settings()) as session:
        assert QuotesSource(session, client).cross_check() == 10
