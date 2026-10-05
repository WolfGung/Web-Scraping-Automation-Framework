"""A practice site the night's probe found unreachable has its live check skipped, saying why.

The nightly workflow asks both practice sites for a status code before it scrapes
anything (the `reachable` job in `.github/workflows/nightly.yml`). A site that did
not answer HTTP 200 is dropped from the scrape with the probe's sentence as its
reason, and the same sentence reaches this suite as `SKIP_REASON_<SITE>` —
`SKIP_REASON_BOOKS`, `SKIP_REASON_QUOTES`. Without it, the check against that site
would fail on a connection error, the published page would count the failure as a
red live check — markup drift — and the night would announce drift on a site that
simply was not there. A skipped check is what the page says it is: not a verdict on
a site.

A module states which site it checks as `SITE`; a module without one (the check of
the night's catalogue sizes, which reads `run-stats.json` and asks no site anything)
is left alone. Locally neither variable is set, and `pytest -m live` runs every
check against the real sites, as it always has.
"""
from __future__ import annotations

import os

import pytest

#: The environment variable carrying the probe's reason for one site, by its name.
SKIP_REASON = "SKIP_REASON_{site}"


def skip_reason_variable(site: str) -> str:
    """The variable the nightly workflow sets when `site` did not answer the probe."""
    return SKIP_REASON.format(site=site.upper())


@pytest.fixture(autouse=True)
def _skip_a_site_the_probe_found_unreachable(request: pytest.FixtureRequest) -> None:
    """Skip before the check asks the site anything — or opens a browser to ask it.

    Autouse fixtures are set up before the other fixtures of their scope, so the
    quotes check is skipped before `browser_session` launches Chromium for it.
    """
    site = getattr(request.module, "SITE", None)
    if site is None:
        return
    reason = os.environ.get(skip_reason_variable(site), "").strip()
    if reason:
        pytest.skip(reason)
