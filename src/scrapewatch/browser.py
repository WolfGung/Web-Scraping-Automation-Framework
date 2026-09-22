"""The Playwright engine: what a source reaches for when HTTP alone cannot see the page.

Three moves cover every browser-driven source this project has: render a URL once the
selector it promised shows up, scroll a page that loads more of itself as you go until
it says it is done, and log in so a later `render` sees what only a session can see.
Recording (video + trace) is optional and off by default — the nightly run turns it on
for a post-mortem when a site changes shape; a test that doesn't ask for it should never
see a `videos/` or `traces/` directory appear.
"""
from __future__ import annotations

import warnings
from pathlib import Path
from types import TracebackType

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from scrapewatch.config import Settings

#: How far one wheel scroll moves the page, and how long to let the DOM catch up.
_SCROLL_STEP_PX = 4000
_SCROLL_PAUSE_MS = 300
#: Stop `scroll_until` once the item count has stayed flat for this many rounds in a
#: row, even if `done_selector` never shows up — a page that stalls without saying so
#: should not spin forever.
_STALL_ROUNDS = 3


class BrowserSession:
    """One Chromium browser, one context, one page — reused across `render` calls.

    Reusing the page (created lazily, on first use) is what makes `login` useful at
    all: the session cookie a login sets survives into the next `render` only because
    they share the same page and context, not a fresh one each time.
    """

    def __init__(self, settings: Settings, trace_name: str | None = None) -> None:
        self._settings = settings
        self._trace_name = trace_name if trace_name is not None else "session"
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._tracing_started = False

    def __enter__(self) -> BrowserSession:
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self._settings.headless)

            context_kwargs: dict = {"user_agent": self._settings.user_agent}
            if self._settings.record_video:
                video_dir = Path(self._settings.video_dir)
                video_dir.mkdir(parents=True, exist_ok=True)
                context_kwargs["record_video_dir"] = str(video_dir)
            self._context = self._browser.new_context(**context_kwargs)

            if self._settings.record_video:
                trace_dir = Path(self._settings.trace_dir)
                trace_dir.mkdir(parents=True, exist_ok=True)
                self._context.tracing.start(screenshots=True, snapshots=True, sources=True)
                self._tracing_started = True
        except BaseException:
            # launch() or new_context() failed partway through: tear down whatever did
            # start before re-raising, or the Playwright driver (and maybe a browser)
            # leaks — nothing will ever call `__exit__` for a `with` block that never
            # returned from `__enter__`.
            self._teardown()
            raise

        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._teardown()

    def _teardown(self) -> None:
        """Stop tracing, then close the context, the browser, and the Playwright driver.

        Shared by `__exit__` and by `__enter__`'s failure path, in the same order
        either way: whatever was started gets closed, in reverse order, and a failure
        at one step never skips the next.
        """
        try:
            if self._tracing_started and self._context is not None:
                trace_path = Path(self._settings.trace_dir) / f"{self._trace_name}.zip"
                try:
                    self._context.tracing.stop(path=str(trace_path))
                except Exception as trace_exc:  # a recording must never fail the run
                    warnings.warn(f"failed to stop tracing: {trace_exc}", stacklevel=2)
        finally:
            try:
                if self._context is not None:
                    self._context.close()
            finally:
                try:
                    if self._browser is not None:
                        self._browser.close()
                finally:
                    if self._playwright is not None:
                        self._playwright.stop()

    @property
    def page(self) -> Page | None:
        """The page this session has open, or `None` if nothing has opened one yet.

        Read-only, and deliberately not `_get_page()`: this exists for the failure
        screenshot in `tests/conftest.py`, and a test that failed before it ever
        rendered anything must not have a browser page created for it on the way out.
        Nothing about the session's own work goes through here.
        """
        return self._page

    def _get_page(self) -> Page:
        if self._page is None:
            assert self._context is not None, "BrowserSession used outside its `with` block"
            self._page = self._context.new_page()
        return self._page

    def render(self, url: str, wait_for: str) -> str:
        """Load `url`, wait for `wait_for` to appear, and return the page's HTML.

        Not counted in `PoliteClient.stats` — a browser page load is not a polite-client
        request at all, it has no shared rate limit or retry budget with the HTTP
        sources, so a caller that wants both numbers has to add them itself.
        """
        page = self._get_page()
        page.goto(url)
        page.wait_for_selector(wait_for)
        return page.content()

    def scroll_until(self, url: str, item_selector: str, done_selector: str) -> str:
        """Scroll `url` until `done_selector` appears or the item count stalls for 3 rounds.

        Never a fixed number of scrolls: some pages announce completion explicitly
        (`done_selector`), others might not, so a stalled count is the fallback signal
        that there is nothing left to load.

        Honours the demo store's `data-error` convention: a load that failed sets
        `done_selector` alongside a `data-error="<message>"` attribute, so once
        `done_selector` has matched, its first element's `data-error` is read and a
        non-empty value is raised rather than returned as an ordinary, successful end
        of scrolling. Harmless for pages that use `done_selector` without ever setting
        `data-error` — `get_attribute` returns `None` there, and nothing is raised.
        """
        page = self._get_page()
        page.goto(url)

        stalled_rounds = 0
        last_count = page.locator(item_selector).count()
        done_locator = page.locator(done_selector)
        while done_locator.count() == 0:
            page.mouse.wheel(0, _SCROLL_STEP_PX)
            page.wait_for_timeout(_SCROLL_PAUSE_MS)
            count = page.locator(item_selector).count()
            if count > last_count:
                stalled_rounds = 0
            else:
                stalled_rounds += 1
            last_count = count
            if stalled_rounds >= _STALL_ROUNDS:
                break

        if done_locator.count() > 0:
            error = done_locator.first.get_attribute("data-error")
            if error:
                raise RuntimeError(f"page reported a load error at {url}: {error}")
        return page.content()

    def login(self, url: str, username: str, password: str) -> None:
        """Fill and submit the login form at `url`, waiting for the resulting navigation.

        A rejected login re-renders the same form rather than raising on its own —
        `expect_navigation()` is satisfied by that response just as it would be by a
        real redirect, so this checks the result: if the page still shows
        `input[name=password]` afterwards, the login did not succeed, and a
        `RuntimeError` is raised naming `url`. Without this check a caller only finds
        out much later, as an unrelated `wait_for_selector` timeout on whatever page
        it renders next.
        """
        page = self._get_page()
        page.goto(url)
        page.fill("input[name=username]", username)
        page.fill("input[name=password]", password)
        with page.expect_navigation():
            page.click("button[type=submit]")
        if page.locator("input[name=password]").count() > 0:
            raise RuntimeError(f"login failed at {url}: the page still shows the login form")
