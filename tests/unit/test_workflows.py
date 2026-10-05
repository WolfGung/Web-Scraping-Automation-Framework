"""The workflows keep the promises the README makes about them.

Three files under `.github/workflows/`, and one rule between them. The badge at the
top of the README reads `ci.yml`, and only its push runs on main, so `ci.yml` — and
`checks.yml`, the gate it calls — must never depend on anything outside this
repository: no schedule, no practice site, no publication. Everything that does
lives in `nightly.yml`, which the badge never reads. A schedule or a live check
that crept back into `ci.yml` would make the badge a statement about somebody
else's server again, and nothing would say so until a push went red for a site that
happened to be down.

The night has a promise of its own: a practice site that does not answer is a
notice and a skip, never a failure. The scrape hears about it through flags; the
live checks through `SKIP_REASON_<SITE>`, which `tests/live/conftest.py` reads — a
name spelled in two files, pinned together here, and the skip itself proven by
running the live checks with it set.

The files are read as text, the way `_cron` and `_workflow_site_url` read them:
what is pinned here is a handful of lines a person writes, and a YAML parser would
be one more dependency to read them with.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.live.conftest import skip_reason_variable

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
LIVE = ROOT / "tests" / "live"

#: The workflow the badge reads, the gate it calls, and the night.
CI = "ci.yml"
CHECKS = "checks.yml"
NIGHTLY = "nightly.yml"

#: What the badge shows: `ci.yml`, its push runs, on main — so neither a pull
#: request nor a run on another branch can change its colour.
BADGE = (
    "https://github.com/WolfGung/Web-Scraping-Automation-Framework"
    "/actions/workflows/ci.yml/badge.svg?branch=main&event=push"
)

#: The runner image every job is pinned to. `ubuntu-latest` moves on GitHub's
#: schedule rather than this project's, so a run that was green could go red with
#: no commit behind it.
RUNNER = "ubuntu-24.04"

#: Things a run of the badge's workflow must never do, as they would be written.
REACHES_OUTSIDE = {
    "names a practice site": r"toscrape",
    "runs the live checks": r"-m\s+[\"']?live\b",
    "scrapes": r"scrapewatch\s+scrape\b",
    "reads the published site": r"SITE_URL",
    "publishes": r"deploy-pages|upload-pages-artifact",
}

#: Collecting and skipping two checks takes a second or two; a minute means a hang.
RUN_TIMEOUT_SECONDS = 120


def _path(name: str) -> str:
    return f".github/workflows/{name}"


def _workflows() -> list[str]:
    names = sorted(path.name for path in WORKFLOWS.glob("*.yml"))
    assert names, f"there is no workflow under {WORKFLOWS.relative_to(ROOT)}, so nothing here can be checked."
    return names


def _commands(name: str) -> str:
    """A workflow without its comments: the comments explain the rules, the rest is what runs."""
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#")) + "\n"


def _triggers(name: str) -> set[str]:
    """The events a workflow runs on: the keys of its top-level `on:` block."""
    block = re.search(r"^on:\n((?:[ \t]+.*\n|\n)*)", _commands(name), flags=re.M)
    assert block, f"{_path(name)} has no top-level `on:` block this reader can find."
    events = set(re.findall(r"^  ([a-z_]+):", block.group(1), flags=re.M))
    assert events, f"{_path(name)}'s `on:` block names no event this reader can find."
    return events


def _called(name: str) -> list[str]:
    """The reusable workflows of this repository a workflow calls, by file name."""
    return re.findall(r"^\s+uses:\s*\./\.github/workflows/([\w.-]+)\s*$", _commands(name), flags=re.M)


def _live_sites() -> dict[str, Path]:
    """The site each live check asks, as its module states it in `SITE`."""
    sites: dict[str, Path] = {}
    for module in sorted(LIVE.glob("test_*.py")):
        stated = re.findall(r'^SITE = "([a-z]+)"$', module.read_text(encoding="utf-8"), flags=re.M)
        if stated:
            sites[stated[0]] = module
    assert sites, (
        "no module under tests/live states the site it checks as SITE, so no live check "
        "can be skipped on a night its site did not answer — and the page would read the "
        "failure as drift."
    )
    return sites


# -- the badge, and the workflow behind it -------------------------------------------


def test_the_badge_reads_the_push_runs_of_ci_on_main() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    badges = re.findall(r"https://github\.com/[^()\s]+/badge\.svg[^()\s]*", readme)
    assert badges == [BADGE], (
        f"README.md shows the workflow badge(s) {badges}, not the one this project means: "
        f"{BADGE}. The badge reads the deterministic workflow's push runs on main, so a "
        f"pull request, another branch or the night can never change its colour."
    )


def test_the_badge_workflow_runs_on_push_and_pull_request_only() -> None:
    assert _triggers(CI) == {"push", "pull_request"}, (
        f"{_path(CI)} runs on {sorted(_triggers(CI))}. It is the workflow the badge reads, "
        f"and it runs on every push and pull request, and on nothing else — a schedule "
        f"belongs in {_path(NIGHTLY)}."
    )


def test_the_badge_workflow_reaches_nothing_outside_this_repository() -> None:
    """`ci.yml` and every workflow it calls: no practice site, no live check, no publication."""
    files = [CI, *_called(CI)]
    assert CHECKS in files, f"{_path(CI)} no longer calls {_path(CHECKS)}, so it does not run the gate."
    for called in files[1:]:
        assert _triggers(called) == {"workflow_call"}, (
            f"{_path(called)} runs on {sorted(_triggers(called))}, not only when it is "
            f"called: a workflow the badge's run depends on must not start runs of its own."
        )
    found = [
        f"{_path(name)} {what}"
        for name in files
        for what, pattern in REACHES_OUTSIDE.items()
        if re.search(pattern, _commands(name))
    ]
    assert not found, (
        "the badge's workflow depends on something outside this repository: "
        + "; ".join(found)
        + f". That belongs in {_path(NIGHTLY)}, which the badge never reads."
    )


# -- the night ------------------------------------------------------------------------


def test_only_the_night_runs_on_a_schedule_and_publishes() -> None:
    """One workflow on a schedule, one that deploys, and they are the same file."""
    names = _workflows()
    scheduled = [name for name in names if "schedule" in _triggers(name)]
    deploying = [name for name in names if "actions/deploy-pages@" in _commands(name)]
    assert scheduled == [NIGHTLY] and deploying == [NIGHTLY], (
        f"the workflows on a schedule are {scheduled} and the ones that deploy are "
        f"{deploying}; both should be [{NIGHTLY!r}] alone."
    )
    assert _triggers(NIGHTLY) == {"schedule", "workflow_dispatch"}, (
        f"{_path(NIGHTLY)} runs on {sorted(_triggers(NIGHTLY))}: the schedule, and a "
        f"manual run of the same night, are the two ways it is meant to start."
    )
    assert re.search(r"group:\s*pages\s*\n\s*cancel-in-progress:\s*false", _commands(NIGHTLY)), (
        f"{_path(NIGHTLY)} no longer queues its deployments in the `pages` concurrency "
        f"group without cancelling: a deployment cancelled halfway leaves the site as "
        f"whichever half got there."
    )


def test_every_job_runs_on_the_pinned_image() -> None:
    images = {
        (name, image)
        for name in _workflows()
        for image in re.findall(r"^\s*runs-on:\s*(\S+)", _commands(name), flags=re.M)
    }
    assert images, "no job in any workflow states the image it runs on."
    wrong = sorted((name, image) for name, image in images if image != RUNNER)
    assert not wrong, f"jobs run on {wrong}; every job is pinned to {RUNNER}."


def test_the_night_hands_each_live_check_the_reason_its_site_was_skipped() -> None:
    """The variable the workflow sets is the variable `tests/live/conftest.py` reads."""
    commands = _commands(NIGHTLY)
    probed = re.search(r"for site in ([a-z ]+); do", commands)
    assert probed, f"{_path(NIGHTLY)} no longer probes the practice sites in a loop this reader can find."
    sites = _live_sites()
    assert set(probed.group(1).split()) == set(sites), (
        f"{_path(NIGHTLY)} probes {probed.group(1).split()}, but the live checks ask "
        f"{sorted(sites)}: a site with a check and no probe is never skipped, and a probe "
        f"with no check skips nothing."
    )
    for site in sites:
        variable = skip_reason_variable(site)
        wired = rf"^\s+{variable}:\s*\$\{{\{{\s*needs\.reachable\.outputs\.{site}\s*\}}\}}\s*$"
        assert re.search(wired, commands, flags=re.M), (
            f"{_path(NIGHTLY)} does not set {variable} from the probe's answer for {site}, "
            f"so on a night {site} does not answer its live check fails instead of being "
            f"skipped — and the page reads that failure as drift."
        )


def test_a_live_check_whose_site_did_not_answer_is_skipped_with_the_probes_reason() -> None:
    """Both site checks, run with their reasons set: both skip, saying why, before asking anything.

    A request that got past the skip would leave this machine, so every proxy is
    pointed at a closed loopback port: such a request fails here instead of reaching
    a site, and the check reads as failed rather than skipped. The quotes check would
    also have launched Chromium first, which the gate's job never installs.
    """
    sites = _live_sites()
    reasons = {site: f"{site}.toscrape.com did not answer (set by {Path(__file__).name})" for site in sites}
    closed = "http://127.0.0.1:9"
    env = {
        **os.environ,
        "PYTHONPATH": f"{ROOT / 'src'}{os.pathsep}{ROOT}",
        "HTTP_PROXY": closed,
        "HTTPS_PROXY": closed,
        "ALL_PROXY": closed,
        "NO_PROXY": "",
        **{skip_reason_variable(site): reason for site, reason in reasons.items()},
    }
    with tempfile.TemporaryDirectory() as spool:
        proc = subprocess.run(
            [
                sys.executable, "-m", "pytest", "-m", "live", "-q", "-p", "no:cacheprovider",
                f"--alluredir={spool}", *(str(module.relative_to(ROOT)) for module in sites.values()),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env=env,
            timeout=RUN_TIMEOUT_SECONDS,
        )
        results = [json.loads(path.read_text(encoding="utf-8")) for path in Path(spool).glob("*-result.json")]

    # Allure names a result `<package>.<module>#<test>`; the module is what says which site.
    outcomes = {
        result["fullName"].split("#")[0].rsplit(".", 1)[-1]: (
            result["status"],
            result.get("statusDetails", {}).get("message", ""),
        )
        for result in results
    }
    assert proc.returncode == 0 and len(results) == len(sites), (
        f"running the live checks of {sorted(sites)} with their skip reasons set exited "
        f"{proc.returncode} with {len(results)} result(s):\n{proc.stdout}\n{proc.stderr}"
    )
    for site, module in sites.items():
        status, message = outcomes.get(module.stem, (None, ""))
        assert status == "skipped" and reasons[site] in message, (
            f"the live check in {module.relative_to(ROOT)} came out {status!r} ({message!r}), "
            f"not skipped with the probe's reason {reasons[site]!r}.\n{proc.stdout}"
        )
