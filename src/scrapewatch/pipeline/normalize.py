"""Turn what a page said into what a database can compare.

Different sources speak differently — a bookshop prices in symbols and counts stock in
a sentence, a quotes site just has text and tags, a demo store speaks the same symbol
prices but already hands back a stock flag and a count directly. ``normalize`` is the
one seam all three squeeze through: an unparsable price, an unrecognised rating word,
or a missing field all fail the same way — a ``ValueError`` naming exactly the field
that could not be trusted.
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any

from scrapewatch.models import RawRecord, Record

#: First character of a price string tells us the currency; anything else is unparsable.
_CURRENCY_SYMBOLS = {"£": "GBP", "$": "USD", "€": "EUR"}
_RATING_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_AVAILABLE_RE = re.compile(r"(\d+)\s*available", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize(raw: RawRecord) -> Record:
    """Validate and normalise a fetch into a typed, comparable ``Record``.

    Dispatches on ``raw.source``; an unrecognised source is itself a ``ValueError``
    naming it, the same way a bad field is a ``ValueError`` naming that field.
    """
    if raw.source == "books":
        kind: Any = "book"
        fields = _normalize_book(raw.fields)
    elif raw.source == "quotes":
        kind = "quote"
        fields = _normalize_quote(raw.fields)
    elif raw.source == "demo":
        kind = "product"
        fields = _normalize_demo(raw.fields)
    else:
        raise ValueError(f"source: unknown source {raw.source!r}")

    return Record(
        source=raw.source,
        external_id=raw.external_id,
        kind=kind,
        fetched_at=raw.fetched_at,
        fields=fields,
        url=raw.url,
    )


def _require(fields: dict[str, Any], name: str) -> Any:
    if fields.get(name) is None:
        raise ValueError(f"{name}: missing")
    return fields[name]


def _clean_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name}: not text ({value!r})")
    return _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFKC", value)).strip()


def _parse_price(value: Any) -> tuple[Decimal, str]:
    if not isinstance(value, str) or not value:
        raise ValueError(f"price: cannot parse {value!r}")
    symbol, digits = value[0], value[1:]
    currency = _CURRENCY_SYMBOLS.get(symbol)
    if currency is None:
        raise ValueError(f"price: unknown currency in {value!r}")
    try:
        amount = Decimal(digits)
    except InvalidOperation as exc:
        raise ValueError(f"price: cannot parse {value!r}") from exc
    return amount, currency


def _parse_availability(value: Any) -> tuple[bool, int]:
    if not isinstance(value, str):
        raise ValueError(f"availability: cannot parse {value!r}")
    in_stock = "in stock" in value.lower()
    match = _AVAILABLE_RE.search(value)
    stock = int(match.group(1)) if match else 0
    return in_stock, stock


def _parse_rating(value: Any) -> int:
    if not isinstance(value, str):
        raise ValueError(f"rating: cannot parse {value!r}")
    rating = _RATING_WORDS.get(value.strip().lower())
    if rating is None:
        raise ValueError(f"rating: unrecognised rating {value!r}")
    return rating


def _parse_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name}: not a boolean ({value!r})")


def _parse_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name}: not an integer ({value!r})")
    return value


def _normalize_book(fields: dict[str, Any]) -> dict[str, Any]:
    title = _clean_text(_require(fields, "title"), "title")
    price, currency = _parse_price(_require(fields, "price"))
    in_stock, stock = _parse_availability(_require(fields, "availability"))
    rating = _parse_rating(_require(fields, "rating"))
    out: dict[str, Any] = {
        "title": title,
        "price": price,
        "currency": currency,
        "in_stock": in_stock,
        "stock": stock,
        "rating": rating,
    }
    category = fields.get("category")
    if category is not None:
        out["category"] = _clean_text(category, "category")
    return out


def _normalize_quote(fields: dict[str, Any]) -> dict[str, Any]:
    text = _clean_text(_require(fields, "text"), "text")
    author = _clean_text(_require(fields, "author"), "author")
    tags = _require(fields, "tags")
    if not isinstance(tags, list):
        raise ValueError(f"tags: not a list ({tags!r})")
    return {"text": text, "author": author, "tags": [_clean_text(tag, "tags") for tag in tags]}


def _normalize_demo(fields: dict[str, Any]) -> dict[str, Any]:
    name = _clean_text(_require(fields, "name"), "name")
    price, currency = _parse_price(_require(fields, "price"))
    in_stock = _parse_bool(_require(fields, "in_stock"), "in_stock")
    stock = _parse_int(_require(fields, "stock"), "stock")
    return {"name": name, "price": price, "currency": currency, "in_stock": in_stock, "stock": stock}
