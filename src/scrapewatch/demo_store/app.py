"""The demo store's FastAPI app: a listing, a JSON API, infinite scroll, and a login wall.

Every page carries a visible line saying what this is: a demo catalogue whose prices
rotate daily by a seeded rule (`scrapewatch.demo_store.catalogue.catalogue_for`). It
exists purely so `scrapewatch.sources.demo.DemoSource` and, later, a browser-driven
source have a real HTTP server to scrape — one with an ordinary listing, a paginated
JSON API that never 404s past the end (it just returns an empty page), an
infinite-scroll page that signals its own completion, and a login-gated view, all
served from templates that are plain f-strings: nothing here is complex enough to
need Jinja.

`create_app(today=None)` takes the catalogue date explicitly for tests; a running
server passes `None` so `date.today()` is read fresh on every request and the
catalogue turns over naturally at midnight.
"""
from __future__ import annotations

from datetime import date
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from scrapewatch.demo_store.catalogue import catalogue_for, member_price_for

PER_PAGE = 12
TOTAL_PRODUCTS = 40
SESSION_COOKIE = "demo_session"
DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo"
HEADER_LINE = "ScrapeWatch demo store — a demo catalogue whose prices rotate daily by a seeded rule"

#: The `/scroll` page's own script: fetches `/api/products` a page at a time, appends
#: what it gets, and either fetches again immediately (the page is shorter than the
#: viewport, so there is nothing to scroll yet) or waits for a `scroll` event within
#: 200px of the bottom. An empty page marks the container `data-done="true"` — the
#: end-of-stream signal a browser-driven source waits on.
_SCROLL_SCRIPT = """
<script>
(function () {
  var container = document.getElementById("products");
  var page = 1;
  var done = false;

  function renderProduct(p) {
    var article = document.createElement("article");
    article.className = "product";
    article.dataset.id = p.id;
    var stockText = p.in_stock ? "In stock (" + p.stock + ")" : "Out of stock";
    article.innerHTML =
      '<h2 class="name"></h2><p class="price"></p><p class="stock"></p>';
    article.querySelector(".name").textContent = p.name;
    article.querySelector(".price").textContent = p.price;
    article.querySelector(".stock").textContent = stockText;
    container.appendChild(article);
  }

  function fitsViewport() {
    return document.documentElement.scrollHeight <= window.innerHeight + 200;
  }

  function nearBottom() {
    return window.innerHeight + window.scrollY >= document.body.scrollHeight - 200;
  }

  function loadNextPage() {
    if (done) {
      return;
    }
    fetch("/api/products?page=" + page)
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (data.items.length === 0) {
          done = true;
          container.setAttribute("data-done", "true");
          return;
        }
        data.items.forEach(renderProduct);
        page += 1;
        if (fitsViewport()) {
          loadNextPage();
        }
      });
  }

  window.addEventListener("scroll", function () {
    if (!done && nearBottom()) {
      loadNextPage();
    }
  });

  loadNextPage();
})();
</script>
"""


def _today(today: date | None) -> date:
    return today if today is not None else date.today()


def _page_slice(items: list[dict], page: int) -> list[dict]:
    start = (page - 1) * PER_PAGE
    return items[start : start + PER_PAGE]


def _product_card(product: dict, *, member_price: str | None = None) -> str:
    stock_text = f"In stock ({product['stock']})" if product["in_stock"] else "Out of stock"
    member_html = (
        f'<p class="member-price">Member price: {member_price}</p>' if member_price is not None else ""
    )
    return (
        f'<article class="product" data-id="{product["id"]}">'
        f'<h2 class="name">{product["name"]}</h2>'
        f'<p class="price">{product["price"]}</p>'
        f'<p class="stock">{stock_text}</p>'
        f"{member_html}"
        f"</article>"
    )


def _page_html(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html>"
        f'<html lang="en"><head><meta charset="utf-8"><title>{title}</title></head>'
        f"<body><header><p>{HEADER_LINE}</p></header>{body}</body></html>"
    )


def _login_page(*, failed: bool = False) -> str:
    message = '<p class="error">Invalid username or password.</p>' if failed else ""
    body = (
        f"{message}"
        '<form method="post" action="/login">'
        '<input name="username" placeholder="username">'
        '<input name="password" type="password" placeholder="password">'
        '<button type="submit">Log in</button>'
        "</form>"
    )
    return _page_html("ScrapeWatch demo store — login", body)


def create_app(today: date | None = None) -> FastAPI:
    """Build the demo store. `today` pins the catalogue date for tests; `None` reads it live."""
    app = FastAPI(title="ScrapeWatch demo store")

    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots_txt() -> str:
        return "User-agent: *\nAllow: /\n"

    @app.get("/", response_class=HTMLResponse)
    def index(page: int = 1) -> str:
        products = catalogue_for(_today(today))
        cards = "".join(_product_card(p) for p in _page_slice(products, page))
        body = f'<main id="products">{cards}</main>'
        return _page_html("ScrapeWatch demo store", body)

    @app.get("/api/products")
    def api_products(page: int = 1) -> dict:
        products = catalogue_for(_today(today))
        items = _page_slice(products, page)
        return {"page": page, "per_page": PER_PAGE, "total": TOTAL_PRODUCTS, "items": items}

    @app.get("/scroll", response_class=HTMLResponse)
    def scroll() -> str:
        body = f'<main id="products" data-done="false"></main>{_SCROLL_SCRIPT}'
        return _page_html("ScrapeWatch demo store — scroll", body)

    @app.get("/login", response_class=HTMLResponse)
    def login_form() -> str:
        return _login_page()

    @app.post("/login")
    async def login_submit(request: Request):
        # Parsed by hand from the raw body rather than FastAPI's `Form(...)`: that
        # dependency requires the optional `python-multipart` package to even be
        # importable, for urlencoded bodies as much as multipart ones, and this demo
        # login needs nothing beyond two plain fields.
        body = (await request.body()).decode("utf-8")
        fields = dict(parse_qsl(body))
        if fields.get("username") == DEMO_USERNAME and fields.get("password") == DEMO_PASSWORD:
            response = RedirectResponse(url="/members", status_code=302)
            response.set_cookie(SESSION_COOKIE, "1")
            return response
        return HTMLResponse(_login_page(failed=True), status_code=401)

    @app.get("/members", response_class=HTMLResponse)
    def members(request: Request):
        if request.cookies.get(SESSION_COOKIE) != "1":
            return RedirectResponse(url="/login", status_code=302)
        products = catalogue_for(_today(today))
        cards = "".join(_product_card(p, member_price=member_price_for(p["price"])) for p in products)
        body = f'<main id="products">{cards}</main>'
        return HTMLResponse(_page_html("ScrapeWatch demo store — members", body))

    return app
