"""Shared pytest fixtures, plus the suite's Allure wiring.

Two independent jobs live here:

- `demo_store_url` — a real HTTP server for the length of the session. It used to
  live under `tests/e2e/conftest.py` alone; it moved up here once `tests/unit` grew
  a test (`test_scrape_demo_writes_stats_and_report`) that also needs one, so both
  directories now share the same fixture instead of two copies drifting apart.

- `browser_session` — a `BrowserSession` for the length of one test, which is also
  what makes the failure screenshot below possible at all.

- Allure wiring — `pytest_sessionstart` copies `allure/categories.json` and writes
  most of `environment.properties` into `--alluredir`; `pytest_collection_finish`
  appends the one fact `sessionstart` can't yet know (`browser=...`, only once
  `session.items` says a browser test was actually selected); and
  `pytest_runtest_makereport` attaches a best-effort failure screenshot for anything
  driving a Playwright page. All three are courtesies to the report, never reasons
  to fail or abort a run: every step here is wrapped so a missing file, an
  unwritable directory, or a hung page turns into a `warnings.warn`, not a crashed
  session.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import allure
import httpx
import pytest
import uvicorn

from scrapewatch.browser import BrowserSession
from scrapewatch.config import Settings
from scrapewatch.demo_store.app import create_app

# -- the demo store, as a real server -------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def demo_store_url() -> Iterator[str]:
    """Run the demo store as a real HTTP server for the length of the session.

    A source is only proven against a live server, not a mock of one — so this
    starts `uvicorn` in a background thread on a free port, the same as anyone
    running `scrapewatch.demo_store.app` for real would, and tears it down when the
    session ends.
    """
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base_url}/api/products?page=1", timeout=1.0).status_code == 200:
                break
        except httpx.TransportError:
            pass
        time.sleep(0.05)
    else:
        raise RuntimeError("demo store did not come up within 10s")

    yield base_url

    server.should_exit = True
    thread.join(timeout=5.0)


@pytest.fixture
def browser_session() -> Iterator[BrowserSession]:
    """One `BrowserSession` on default settings, opened and closed around a test.

    A fixture rather than a `with` block inside each test, for one reason: when a
    browser test fails, `pytest_runtest_makereport` below can reach the session
    through `item.funcargs` and attach a screenshot of whatever was on screen. A
    session opened inside the test body is gone by then — the `with` has already
    exited — and the hook has nothing to photograph.

    Function-scoped on purpose: a browser shared between tests carries one test's
    cookies and one test's page into the next, and `login` exists here precisely
    because a session remembers things.

    A test that needs different settings — recording turned on, a video directory of
    its own — still opens its own session, since that is what it is testing.
    """
    with BrowserSession(Settings()) as session:
        yield session


# -- Allure: categories + environment, copied into --alluredir ------------------

#: Kept beside the suite, not inside any results directory: Allure only groups
#: failures when `categories.json` sits *inside* the results directory it is
#: reading, so a copy that lives only in the repository never travels with a run.
_CATEGORIES_FILE = Path(__file__).resolve().parent.parent / "allure" / "categories.json"


def _copy_categories(results_dir: Path) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_CATEGORIES_FILE, results_dir / "categories.json")


def _collects_browser_tests(items: list[pytest.Item]) -> bool:
    """Whether this session will actually run a browser test, exactly, not a guess.

    Reads `session.items` — what will actually run, after `-m` deselection has
    already happened — rather than the raw `-m` expression text: a substring check
    on that text would mislabel `pytest -m "not live" tests/unit` as a browser run,
    since `"not live"` itself contains the substring `"live"` despite excluding only
    `live`-marked tests and running no browser at all.
    """
    return any(item.get_closest_marker("e2e") or item.get_closest_marker("live") for item in items)


def _chromium_version() -> str:
    """`"chromium <version>"` when cheaply available, else just `"chromium"`.

    "Cheap" means asking the already-installed binary for its own version string —
    one bounded subprocess call, not a browser launch, a context, or a page.
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            executable_path = playwright.chromium.executable_path
        result = subprocess.run([executable_path, "--version"], capture_output=True, text=True, timeout=5)
        version = (result.stdout or result.stderr).strip()
        return f"chromium {version}" if version else "chromium"
    except Exception:
        return "chromium"


def _environment_key(name: str) -> str:
    """The key this job writes its environment fact under.

    Qualified by the CI job that wrote it, because the published report is built
    from three jobs' results merged into one directory (the gate, the browser job
    and the nightly live run — see `showcase/merge.py`), and they genuinely
    disagree: the ones that drove a browser name it, the gate opens none. Unqualified
    keys would make that a collision the merge cannot resolve, and the choice is
    between failing the publication over a cosmetic difference and quietly dropping
    whichever job's file was read last. Qualified, all three survive and the panel
    says which job each fact came from. Locally `GITHUB_JOB` is unset and the keys
    stay plain, because there is only one run to describe.
    """
    job = os.getenv("GITHUB_JOB")
    return f"{job}.{name}" if job else name


def _write_environment_properties(results_dir: Path) -> None:
    """The facts knowable at session start — everything except `browser`.

    Whether this run drives a browser at all is not a fact about `Settings()` or the
    command line; it is a fact about which tests actually ended up selected, which
    isn't known yet at `pytest_sessionstart`. `pytest_collection_finish` appends
    `browser=...` afterwards, once `session.items` exists to ask.
    """
    settings = Settings()
    lines = [
        f"{_environment_key('db.url.scheme')}={urlsplit(settings.db_url).scheme}",
        f"{_environment_key('headless')}={settings.headless}",
        f"{_environment_key('python')}={sys.version.split()[0]}",
        f"{_environment_key('ci')}={os.getenv('GITHUB_ACTIONS', 'false')}",
    ]
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "environment.properties").write_text("\n".join(lines) + "\n", encoding="utf-8")


def pytest_sessionstart(session: pytest.Session) -> None:
    """Put Allure's failure categories and this run's environment facts where it reads them from.

    Both are copies into `--alluredir`; nothing happens when it is not set — there
    is no results directory to put them in, and no report that will look for them.
    """
    alluredir = session.config.getoption("--alluredir", default=None)
    if not alluredir:
        return
    results_dir = Path(alluredir)
    try:
        _copy_categories(results_dir)
    except Exception as exc:
        warnings.warn(f"could not copy categories.json into {results_dir}: {exc!r}", stacklevel=2)
    try:
        _write_environment_properties(results_dir)
    except Exception as exc:
        warnings.warn(f"could not write environment.properties into {results_dir}: {exc!r}", stacklevel=2)


def pytest_collection_finish(session: pytest.Session) -> None:
    """Append `browser=...` to `environment.properties`, once collection says one will run.

    Deliberately later than `pytest_sessionstart`: `session.items` is the selected
    set after `-m` deselection, so this is exact where a check at session start could
    only guess. An append, not a rewrite, so a failure here can never undo the facts
    `pytest_sessionstart` already wrote successfully — the two are independent, same
    as the two writes inside `pytest_sessionstart` itself are independent of each
    other.
    """
    if session.config.option.collectonly:
        return
    alluredir = session.config.getoption("--alluredir", default=None)
    if not alluredir:
        return
    if not _collects_browser_tests(session.items):
        return
    results_dir = Path(alluredir)
    try:
        with (results_dir / "environment.properties").open("a", encoding="utf-8") as handle:
            handle.write(f"{_environment_key('browser')}={_chromium_version()}\n")
    except Exception as exc:
        warnings.warn(f"could not append browser to environment.properties in {results_dir}: {exc!r}", stacklevel=2)


# -- Allure: a failure screenshot for anything driving a page --------------------

#: Short and independent of any test's own timeouts: this runs *after* a test has
#: already failed, so it must fail fast rather than block the session on a hung page.
_SCREENSHOT_TIMEOUT_MS = 5_000


def _attach(body: Any, *, name: str, attachment_type: Any) -> None:
    """Best-effort Allure attach: a failure here must never fail an otherwise-green session."""
    try:
        allure.attach(body, name=name, attachment_type=attachment_type)
    except Exception as exc:
        warnings.warn(f"could not attach {name!r} to Allure: {exc!r}", stacklevel=2)


def _capture_failure_diagnostics(page: Any) -> None:
    """Screenshot a failed browser test's page, bounded to `_SCREENSHOT_TIMEOUT_MS`.

    `page.content()` has no timeout of its own to bound it with in this Playwright
    version, so a page that cannot produce a screenshot within the bound is not
    trusted to serialise its DOM either: `content()` is skipped entirely rather than
    attempted unbounded, the same reasoning a sibling project's conftest documents
    at length after verifying it against a real hung page.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    try:
        screenshot = page.screenshot(timeout=_SCREENSHOT_TIMEOUT_MS)
    except PlaywrightTimeoutError as exc:
        _attach(
            f"Could not capture failure screenshot (timed out): {exc!r}",
            name="failure-screenshot-unavailable",
            attachment_type=allure.attachment_type.TEXT,
        )
        return
    except Exception as exc:
        _attach(
            f"Could not capture failure screenshot: {exc!r}",
            name="failure-screenshot-unavailable",
            attachment_type=allure.attachment_type.TEXT,
        )
        return
    _attach(screenshot, name="failure-screenshot", attachment_type=allure.attachment_type.PNG)

    try:
        html = page.content()
    except Exception as exc:
        _attach(
            f"Could not capture failure HTML: {exc!r}",
            name="failure-html-unavailable",
            attachment_type=allure.attachment_type.TEXT,
        )
    else:
        _attach(html, name="failure-html", attachment_type=allure.attachment_type.HTML)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Any:
    """Attach a failure screenshot for anything driving a Playwright `page`-like fixture.

    Two funcarg names are understood: `page`, which is what a Playwright plugin
    hands a test, and `browser_session`, this suite's own fixture — from which the
    page is taken through `BrowserSession.page`, a read-only view that does not open
    one. A test that failed before rendering anything therefore has nothing to
    photograph, and says so by having no attachment rather than by opening a browser
    page on its way out.

    A test that opens its own session inside its body is still invisible here, and
    that is a real limitation rather than an oversight: the session is closed by the
    time this runs. The one test that does so (`test_recording_is_off_by_default_and
    _on_when_asked`) opens two sessions with different settings, which is the thing
    it is testing.
    """
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or not report.failed:
        return
    funcarg = item.funcargs.get("page") or item.funcargs.get("browser_session")
    # A `BrowserSession` hands over the page it has open; a `page` funcarg is one.
    page = getattr(funcarg, "page", funcarg)
    if page is None or not hasattr(page, "screenshot"):
        return
    _capture_failure_diagnostics(page)
