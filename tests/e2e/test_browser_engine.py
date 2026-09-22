"""The browser engine's three moves, proven on the demo store where the end is known."""
from __future__ import annotations

import time

import pytest
from selectolax.parser import HTMLParser

from scrapewatch.browser import BrowserSession
from scrapewatch.config import Settings

pytestmark = pytest.mark.e2e


def test_scroll_until_reaches_the_end_and_collects_every_item(
    demo_store_url: str, browser_session: BrowserSession
) -> None:
    html = browser_session.scroll_until(
        f"{demo_store_url}/scroll", item_selector=".product", done_selector='[data-done="true"]'
    )
    assert len(HTMLParser(html).css(".product")) == 40


def test_login_opens_the_members_page(demo_store_url: str, browser_session: BrowserSession) -> None:
    browser_session.login(f"{demo_store_url}/login", "demo", "demo")
    html = browser_session.render(f"{demo_store_url}/members", wait_for=".product")
    assert 'class="member-price"' in html


def test_login_with_the_wrong_password_fails_fast_instead_of_looking_like_success(
    demo_store_url: str, browser_session: BrowserSession
) -> None:
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="login failed"):
        browser_session.login(f"{demo_store_url}/login", "demo", "wrong")
    assert time.monotonic() - started < 10.0


def test_recording_is_off_by_default_and_on_when_asked(demo_store_url: str, tmp_path) -> None:
    """The one test here that opens its own sessions: the settings are what it is about.

    Both halves name the same directory. Asserting that a directory nothing was ever
    pointed at does not exist would pass however the recording behaved — the off case
    has to be pointed at the very directory the on case fills, or it is not testing
    that recording was off.
    """
    videos, traces = tmp_path / "videos", tmp_path / "traces"
    off = Settings(record_video=False, video_dir=str(videos), trace_dir=str(traces))
    with BrowserSession(off) as session:
        session.render(f"{demo_store_url}/", wait_for=".product")
    assert not videos.exists() and not traces.exists()

    on = Settings(record_video=True, video_dir=str(videos), trace_dir=str(traces))
    with BrowserSession(on, trace_name="demo") as session:
        session.render(f"{demo_store_url}/", wait_for=".product")
    assert list(videos.glob("*.webm")) and (traces / "demo.zip").is_file()
