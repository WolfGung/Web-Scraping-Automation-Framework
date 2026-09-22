"""The demo store's catalogue: 40 fixed products, nudged a little each day.

The demo store exists to give the published change report real work to show — the
practice sites it scrapes alongside never change. `catalogue_for(today)` starts from
a fixed baseline (id, name, base price, base stock) and applies a rule seeded from
the date itself: it picks about six products to reprice by ±5-15% and, among those,
flips the `in_stock` flag on two. The same date always seeds the same choices, so a
second run on the same day reports no further change, and the report can name
exactly which products moved between two different days.
"""
from __future__ import annotations

import random
from datetime import date
from functools import lru_cache

#: id, name, base price in cents, base stock (0 means out of stock at baseline).
_BASE_PRODUCTS: tuple[tuple[int, str, int, int], ...] = (
    (1, "Aurora Desk Lamp", 2499, 14),
    (2, "Basalt Coffee Mug", 899, 27),
    (3, "Cobalt Backpack", 5499, 9),
    (4, "Driftwood Cutting Board", 1899, 0),
    (5, "Ember Candle Trio", 1299, 22),
    (6, "Fjord Wool Blanket", 6999, 5),
    (7, "Granite Bookend Pair", 2199, 18),
    (8, "Harbor Umbrella", 3499, 0),
    (9, "Ivory Ceramic Vase", 2799, 11),
    (10, "Juniper Hand Soap", 599, 30),
    (11, "Kestrel Binoculars", 8999, 3),
    (12, "Lagoon Beach Towel", 1799, 20),
    (13, "Meadow Throw Pillow", 2299, 16),
    (14, "Nimbus Rain Jacket", 7499, 8),
    (15, "Onyx Desk Organizer", 1999, 25),
    (16, "Pinecone Air Freshener", 499, 29),
    (17, "Quartz Bookmark Set", 899, 0),
    (18, "Redwood Picture Frame", 1699, 12),
    (19, "Sable Leather Wallet", 4499, 7),
    (20, "Tundra Sleeping Bag", 8999, 4),
    (21, "Umber Ceramic Bowl", 1499, 19),
    (22, "Violet Garden Trowel", 999, 15),
    (23, "Willow Wind Chime", 2099, 10),
    (24, "Xenon Bike Light", 1899, 0),
    (25, "Yarrow Herbal Tea Tin", 799, 26),
    (26, "Zinc Watering Can", 1599, 13),
    (27, "Alder Cutting Knife", 2999, 6),
    (28, "Birch Serving Tray", 2399, 17),
    (29, "Cedar Soap Dish", 699, 28),
    (30, "Delta Camping Stove", 5999, 2),
    (31, "Ember Trail Mix Jar", 549, 24),
    (32, "Frost Insulated Bottle", 2499, 21),
    (33, "Glacier Ice Scraper", 349, 30),
    (34, "Heron Feather Pen", 1299, 0),
    (35, "Indigo Notebook", 999, 23),
    (36, "Jasper Stone Coaster Set", 1799, 14),
    (37, "Kelp Sea Salt Grinder", 1199, 9),
    (38, "Lantern Solar Charger", 3999, 5),
    (39, "Marble Rolling Pin", 2199, 18),
    (40, "Nectar Honey Dipper", 449, 27),
)

#: How many products the store has. Counted from the list above rather than typed
#: again: the API states this as its `total`, and a second copy is a number that goes
#: wrong the first time a product is added.
TOTAL_PRODUCTS = len(_BASE_PRODUCTS)

#: Percent range (inclusive) a repriced product moves, up or down, chosen per product.
_PRICE_ADJUST_RANGE_PCT = (5, 15)
_PRODUCTS_ADJUSTED_PER_DAY = 6
_STOCK_FLIPS_PER_DAY = 2


def _format_price(cents: int) -> str:
    """An already-rounded cent amount as a `"$d.dd"` string."""
    return f"${cents // 100}.{cents % 100:02d}"


def _cents_from_price(price: str) -> int:
    dollars, _, fraction = price.removeprefix("$").partition(".")
    return int(dollars) * 100 + int((fraction or "0").ljust(2, "0")[:2])


def member_price_for(price: str) -> str:
    """The `/members` price: 10% below the listed price, rounded to the nearest cent."""
    return _format_price(round(_cents_from_price(price) * 0.9))


@lru_cache
def _catalogue_for(ordinal: int) -> list[dict]:
    """The cached computation, keyed by `date.toordinal()`. Callers use `catalogue_for`.

    Deterministic and pure: seeded from `ordinal`, so the same date always produces
    the same list, and two different dates differ only in the handful of products
    the seeded rule chose to touch that day. This is the mechanism the published
    change report relies on to have something real, and reproducible, to report —
    repeat the fetch on the same day and nothing new is announced. The price
    adjustment below goes through float multiplication and is rounded to whole
    cents, not integer arithmetic throughout.

    Each item carries exactly the fields `scrapewatch.sources.demo.DemoSource` and
    `normalize()`'s demo branch expect: `id`, `name`, `price` (a `"$d.dd"` string),
    `in_stock` (a real bool) and `stock` (a real int, 0 when out of stock). Not for
    outside use: these are the dicts `lru_cache` holds onto, so a caller that
    mutates one would poison every future call for this date. `catalogue_for`
    returns fresh copies instead.
    """
    rng = random.Random(ordinal)
    product_ids = [product_id for product_id, _, _, _ in _BASE_PRODUCTS]

    adjusted_ids = set(rng.sample(product_ids, _PRODUCTS_ADJUSTED_PER_DAY))
    flipped_ids = set(rng.sample(sorted(adjusted_ids), _STOCK_FLIPS_PER_DAY))

    catalogue: list[dict] = []
    for product_id, name, base_price_cents, base_stock in _BASE_PRODUCTS:
        price_cents = base_price_cents
        in_stock = base_stock > 0
        stock = base_stock

        if product_id in adjusted_ids:
            pct = rng.randint(*_PRICE_ADJUST_RANGE_PCT)
            sign = rng.choice((1, -1))
            # Float multiplication, then rounded to whole cents — not integer math.
            price_cents = max(1, round(base_price_cents * (1 + sign * pct / 100)))

        if product_id in flipped_ids:
            in_stock = not in_stock
            stock = rng.randint(1, 30) if in_stock else 0

        catalogue.append(
            {
                "id": product_id,
                "name": name,
                "price": _format_price(price_cents),
                "in_stock": in_stock,
                "stock": stock,
            }
        )
    return catalogue


def catalogue_for(today: date) -> list[dict]:
    """The full 40-product catalogue as it stands on `today`, safe for a caller to mutate.

    `_catalogue_for` does the deterministic work once per date and `lru_cache`s the
    result; this wrapper rebuilds each item's dict on every call so that mutating a
    returned product (as a caller reasonably might, e.g. to add a display-only field)
    can never poison what a later call for the same date sees.
    """
    return [dict(item) for item in _catalogue_for(today.toordinal())]
