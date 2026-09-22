from __future__ import annotations

from pathlib import Path

import pytest

from scrapewatch.sources.quotes import parse_quotes

pytestmark = pytest.mark.parsers
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "quotes"


def test_the_unrendered_js_page_has_no_quotes_in_its_html() -> None:
    assert parse_quotes((FIXTURES / "js.html").read_text(encoding="utf-8")) == []


def test_rendered_markup_yields_text_author_and_tags() -> None:
    rendered = (
        '<div class="quote"><span class="text">“A”</span>'
        '<small class="author">B</small><a class="tag">c</a></div>'
    )
    (q,) = parse_quotes(rendered)
    assert q == {"text": "A", "author": "B", "tags": ["c"]}
