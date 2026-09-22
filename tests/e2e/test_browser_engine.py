"""The browser engine's three moves, proven on the demo store where the end is known."""
from __future__ import annotations

import time

import pytest
from selectolax.parser import HTMLParser

from scrapewatch.browser import BrowserSession
from scrapewatch.config import Settings

pytestmark = pytest.mark.e2e


def test_scroll_until_reaches_the_end_and_collects_every_item(demo_store_url: str) -> None:
    with BrowserSession(Settings()) as session:
        html = session.scroll_until(
            f"{demo_store_url}/scroll", item_selector=".product", done_selector='[data-done="true"]'
        )
    assert len(HTMLParser(html).css(".product")) == 40


def test_login_opens_the_members_page(demo_store_url: str) -> None:
    with BrowserSession(Settings()) as session:
        session.login(f"{demo_store_url}/login", "demo", "demo")
        html = session.render(f"{demo_store_url}/members", wait_for=".product")
    assert 'class="member-price"' in html


def test_login_with_the_wrong_password_fails_fast_instead_of_looking_like_success(demo_store_url: str) -> None:
    with BrowserSession(Settings()) as session:
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="login failed"):
            session.login(f"{demo_store_url}/login", "demo", "wrong")
        elapsed = time.monotonic() - started
    assert elapsed < 10.0


def test_recording_is_off_by_default_and_on_when_asked(demo_store_url: str, tmp_path) -> None:
    with BrowserSession(Settings(record_video=False)) as session:
        session.render(f"{demo_store_url}/", wait_for=".product")
    assert not (tmp_path / "videos").exists()
    recording_settings = Settings(
        record_video=True, video_dir=str(tmp_path / "videos"), trace_dir=str(tmp_path / "traces")
    )
    with BrowserSession(recording_settings, trace_name="demo") as session:
        session.render(f"{demo_store_url}/", wait_for=".product")
    assert list((tmp_path / "videos").glob("*.webm")) and (tmp_path / "traces" / "demo.zip").is_file()
