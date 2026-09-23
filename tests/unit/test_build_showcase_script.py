"""The build script assembles the site, and that is all it does.

`scripts/build-showcase.sh` puts `site/` together — the page, the Allure report of
the whole run, the night's data, the change report and the media — and stops. The
CI run uploads that directory as a GitHub Pages artifact and deploys it, so nothing
is published from the script, and it has no git to do: no branch, no worktree, no
commit, no push. The one thing it reads from outside the checkout is the previous
publication's Allure history, fetched from the published site (`SITE_URL`), which
is what lets the report's trend run on from one night to the next.

The first half of this module reads the file. `showcase/build.py` decides which
recording, which trace and which data files are published, and the page links
exactly those. A second, independent rule in the shell script — a glob, a
hardcoded file name, a copy straight into the site — is how a real file ends up
published under a name nothing on the page ever mentions: it happened in the
sibling project this machinery is ported from, and it is why the script stages
data beside the site and lets the builder select from it. These tests do not
re-implement the selection rule; they make sure there is nowhere else for a second
one to live, and that the script hands the builder the real directories instead of
relying on defaults that only happen to be right.

The second half runs the script, against a local server standing in for the
published site, because the two things that matter most cannot be proven by
reading: that it leaves git exactly as it found it, and what it does with the
history the site sends back — or does not.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from showcase.build import DATA_FILES, DATABASE_FILE, PAGE_URL, TRACE_NAME, VIDEO_NAME

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build-showcase.sh"
ROOT = SCRIPT.parents[1]


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _commands() -> list[str]:
    """The script's actual command lines: comments explain the rules, code breaks them."""
    return [
        line.strip()
        for line in _text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


# -- the file, read ----------------------------------------------------------------


def test_the_script_has_no_media_glob_or_file_name_of_its_own() -> None:
    """`showcase/build.py` publishes `scroll.webm` and `scroll-trace.zip` by name and
    applies the size floor that decides a truncated file is not a video. A second
    rule here could not be kept in step with that one."""
    commands = _commands()
    assert not any("*.webm" in line for line in commands)
    assert not any("*.zip" in line for line in commands)
    assert not any(VIDEO_NAME in line for line in commands)
    assert not any(TRACE_NAME in line for line in commands)


def test_the_script_copies_nothing_into_the_sites_media_directory() -> None:
    """Only the builder may write there; a `cp` to the same path from here is the
    conflicting write the divergence came from."""
    assert not any("$SITE/media" in line for line in _commands())


def test_the_script_gives_the_builder_the_real_media_directory() -> None:
    text = _text()
    assert "showcase/build.py" in text
    assert '--media-dir "$MEDIA"' in text


def test_the_script_copies_no_data_file_into_the_site_itself() -> None:
    """The data is staged into `publish-data/` and the builder copies from there
    into `site/data`, linking exactly what it copied. A copy straight into the site
    would publish a file the page never mentions — which is the same bug as the
    recording's, on a second artefact."""
    assert not any("$SITE/data" in line for line in _commands())


def test_the_script_stages_the_data_and_hands_the_staging_directory_over() -> None:
    text = _text()
    assert "mkdir -p publish-data" in text
    assert "--data-dir publish-data" in text


def test_the_staging_directory_is_cleaned_up() -> None:
    """It sits in the repository root while the site is assembled; leaving it behind
    would put untracked build output in a developer's checkout."""
    assert "rm -rf publish-data" in _text()


def test_the_script_names_no_export_the_page_does_not_link() -> None:
    """Every published data file is named by `showcase.build.DATA_FILES`. The script
    copies the exports directory wholesale and never spells one out — with a single
    exception, the database, which it renames on the way in and which the next test
    pins to the builder's own constant."""
    spelled_out = [name for name, _ in DATA_FILES if name != DATABASE_FILE]
    commands = _commands()
    for name in spelled_out:
        assert not any(name in line for line in commands), (
            f"{name} is named in the build script; the one place that decides what "
            f"is published is showcase.build.DATA_FILES"
        )


def test_the_database_is_staged_under_the_name_the_page_publishes() -> None:
    """The script does rename the database, because the local path is a setting and
    the published name is a contract: `scripts/restore-db.sh` reads it back by that
    name and the page links it by that name. Pinned here so the two cannot drift."""
    assert f"publish-data/{DATABASE_FILE}" in _text()
    assert DATABASE_FILE in {name for name, _ in DATA_FILES}


def test_the_script_merges_every_jobs_results_through_the_tested_module() -> None:
    """`showcase/merge.py` is what `tests/unit/test_showcase_merge.py` exercises; the
    script has to be the one calling it, or that coverage says nothing about what
    ships."""
    text = _text()
    assert "showcase/merge.py categories" in text
    assert "showcase/merge.py environment" in text


def test_the_script_stops_when_a_jobs_results_never_arrived() -> None:
    """Three jobs produce results and all three are merged. A directory that is not
    there means a job did not deliver, and publishing the rest would show a report —
    and a figure on the page — missing a whole leg of the suite."""
    text = _text()
    assert 'if [ ! -d "$dir" ]; then' in text
    assert "no results directory at $dir" in text


def test_the_script_runs_no_git_command() -> None:
    """Nothing is published from here, so there is nothing for git to do.

    The site used to reach GitHub Pages as a commit that replaced a whole branch
    every night. It is deployed from the CI run now, as an artifact, and a git
    command in this script would be the old mechanism finding its way back.
    """
    git = [line for line in _commands() if re.search(r"\bgit\b", line)]
    assert not git, f"the build script runs git: {git}"


def test_the_history_is_read_from_the_address_the_page_is_built_for() -> None:
    """`SITE_URL` defaults to `showcase.build.PAGE_URL`, the address the page links
    its own trace from. One site, stated in two files, pinned together."""
    assert re.findall(r'SITE_URL="\$\{SITE_URL:-([^}"]+)\}"', _text()) == [PAGE_URL]


def _workflow_site_url() -> str:
    """The address CI hands both scripts, as `.github/workflows/ci.yml` spells it."""
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    values = re.findall(r"^\s*SITE_URL:\s*[\"']?([^\"'\s]+)[\"']?\s*$", workflow, flags=re.M)
    assert len(values) == 1, (
        f".github/workflows/ci.yml sets SITE_URL {len(values)} time(s), not once: {values}. "
        f"Both scripts read it, so this test needs to know which value they are given."
    )
    return values[0]


def test_ci_hands_the_scripts_the_address_the_page_is_built_for() -> None:
    """CI overrides the scripts' default, so its value is the one the night uses. A
    typo there would fail nothing: every history file would come back 404, which
    reads as a publication with no trend yet, and so would the database, which reads
    as a first night — and the next publication would start the history over."""
    assert _workflow_site_url() == PAGE_URL


# -- the script, actually run ------------------------------------------------------

#: An Allure result the page builder can summarise: one passed `unit` case.
_RESULT = {
    "uuid": "11111111-1111-1111-1111-111111111111",
    "historyId": "one",
    "name": "a test",
    "fullName": "tests/unit/test_x.py::a_test",
    "status": "passed",
    "start": 1,
    "stop": 2,
    "labels": [{"name": "package", "value": "tests.unit"}, {"name": "tag", "value": "unit"}],
}

#: One source's block of `run-stats.json`, enough for the builder to read a run.
_STATS = {
    "generated_at": "2026-09-22T05:12:00+00:00",
    "sources": {
        "demo": {
            "records": 40, "pages": 4, "requests": 6, "retries": 0, "bytes": 1024, "seconds": 0.4,
            "kind": "local", "skipped": False, "reason": None, "parse_errors": 0, "parse_error_reasons": [],
        }
    },
}
_CHANGES = {
    "generated_at": "2026-09-22T05:12:00+00:00",
    "sources": {"demo": {"added": 0, "removed": 0, "changed": 2, "changes": []}},
}

#: Stands in for the Allure command line: the report's contents are not what these
#: tests are about, and generating a real one would need a JVM to prove a shell script.
#: It does keep the one habit of Allure's the trend depends on: whatever history is in
#: the results directory when it runs goes into the report — so history that turned
#: up only after `generate` never reaches the report, here as with the real thing.
_FAKE_ALLURE = """#!/bin/sh
results=""
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift ;;
    generate|--clean) ;;
    *) results="$1" ;;
  esac
  shift
done
mkdir -p "$out"
echo "<!doctype html><title>report</title>" > "$out/index.html"
if [ -d "$results/history" ]; then
  mkdir -p "$out/history"
  cp -R "$results/history/." "$out/history/"
fi
"""

#: The history Allure writes beside a report, one file per name, in the shape it
#: writes it: the per-test history is an object keyed by history id, and each of
#: the four trends is an array with an entry per publication.
HISTORY: dict[str, object] = {
    "history": {"one": {"statistic": {"passed": 1, "total": 1}, "items": [{"uid": "a", "status": "passed"}]}},
    "history-trend": [{"data": {"passed": 267, "total": 267}}],
    "duration-trend": [{"data": {"duration": 30234}}],
    "categories-trend": [{"data": {}}],
    "retry-trend": [{"data": {"run": 267, "retry": 0}}],
}

_NO_GLOBAL_GIT_CONFIG = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, env={**os.environ, **_NO_GLOBAL_GIT_CONFIG}
    )


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A committed checkout holding everything the build script reads, with a bare
    `origin` beside it that this process could certainly push to."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copytree(ROOT / "showcase", repo / "showcase", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(SCRIPT, repo / "scripts" / SCRIPT.name)

    for name in ("allure-results-gate", "allure-results-e2e", "allure-results"):
        results = repo / name
        results.mkdir()
        (results / f"{name}-result.json").write_text(json.dumps({**_RESULT, "uuid": name}), encoding="utf-8")
        (results / "categories.json").write_text('[{"name": "Markup drift"}]', encoding="utf-8")
        (results / "environment.properties").write_text(f"{name}.python=3.12\n", encoding="utf-8")

    run_dir = repo / "data" / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "run-stats.json").write_text(json.dumps(_STATS), encoding="utf-8")
    (run_dir / "change-report.json").write_text(json.dumps(_CHANGES), encoding="utf-8")
    (run_dir / "change-report.html").write_text("<!doctype html><body>changes</body>", encoding="utf-8")
    (repo / "data" / "exports").mkdir()
    (repo / "data" / "exports" / "demo.json").write_text("[]", encoding="utf-8")
    (repo / "data" / DATABASE_FILE).write_bytes(b"SQLite format 3\x00")
    (repo / "media").mkdir()

    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "test@example.invalid"),
        ("config", "user.name", "Test"),
        ("add", "-A"),
        ("commit", "-qm", "the checkout the build runs in"),
    ):
        assert _git(*args, cwd=repo).returncode == 0
    assert _git("init", "-q", "--bare", str(tmp_path / "origin.git"), cwd=tmp_path).returncode == 0
    assert _git("remote", "add", "origin", str(tmp_path / "origin.git"), cwd=repo).returncode == 0
    return repo


@pytest.fixture
def build(checkout: Path, tmp_path: Path, script_env: dict[str, str]) -> Callable[..., subprocess.CompletedProcess]:
    """Run the build script in `checkout` against the site at `site_url`, with a fake Allure."""
    allure = tmp_path / "fake-allure"
    allure.write_text(_FAKE_ALLURE, encoding="utf-8")
    allure.chmod(0o755)

    def run(site_url: str, **extra_env: str) -> subprocess.CompletedProcess:
        env = {**script_env, **_NO_GLOBAL_GIT_CONFIG, "ALLURE_CMD": str(allure), "SITE_URL": site_url}
        env.pop("RESET_SHOWCASE_HISTORY", None)
        env.update(extra_env)
        return subprocess.run(
            ["bash", "scripts/build-showcase.sh"],
            cwd=checkout, capture_output=True, text=True, env=env, timeout=120,
        )

    return run


def _publish_history(site, bodies: dict[str, str]) -> None:
    for name, body in bodies.items():
        site.publish(f"report/history/{name}.json", body)


def _as_served() -> dict[str, str]:
    return {name: json.dumps(value) for name, value in HISTORY.items()}


def _history_files(directory: Path) -> dict[str, str]:
    history = directory / "history"
    if not history.is_dir():
        return {}
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(history.iterdir())}


def _carried(checkout: Path) -> dict[str, str]:
    """The history the build handed Allure, by file name, as it was written there."""
    return _history_files(checkout / "allure-results-merged")


def _reported(checkout: Path) -> dict[str, str]:
    """The history the generated report carries forward for the next publication."""
    return _history_files(checkout / "site" / "report")


def test_the_build_assembles_the_site_and_leaves_git_as_it_found_it(checkout, build, published_site, tmp_path) -> None:
    """Exercised rather than read: a build that publishes nothing pushes nothing.

    Both switches that used to let the old script push are set — `GITHUB_ACTIONS`,
    which every CI job carries, and the explicit `PUBLISH_SHOWCASE` opt-in — and
    `origin` is a repository this process can certainly push to. `git` itself is
    wrapped so that every call is written down: the build may make none, and least
    of all a push. What it leaves behind is the site, and nothing in git.
    """
    head = _git("rev-parse", "HEAD", cwd=checkout).stdout
    calls = tmp_path / "git-calls.log"
    wrapped = tmp_path / "bin"
    wrapped.mkdir()
    (wrapped / "git").write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{calls}"\nexec "{shutil.which("git")}" "$@"\n', encoding="utf-8"
    )
    (wrapped / "git").chmod(0o755)

    result = build(
        published_site.url,
        GITHUB_ACTIONS="true",
        PUBLISH_SHOWCASE="1",
        PATH=f"{wrapped}{os.pathsep}{os.environ['PATH']}",
    )

    assert result.returncode == 0, result.stderr
    assert (checkout / "site" / "index.html").is_file()
    assert (checkout / "site" / "report" / "index.html").is_file()
    assert (checkout / "site" / "data" / DATABASE_FILE).is_file(), "tomorrow's restore reads it back from there"
    assert (checkout / "site" / ".nojekyll").is_file()
    made = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    assert not [call for call in made if call.split()[:1] == ["push"]], f"the build pushed: {made}"
    assert made == [], f"the build ran git, which it has no reason to do: {made}"
    assert _git("rev-parse", "HEAD", cwd=checkout).stdout == head, "the build made a commit"
    assert _git("branch", "--list", "--format=%(refname:short)", cwd=checkout).stdout.split() == ["main"]
    worktrees = _git("worktree", "list", "--porcelain", cwd=checkout).stdout
    assert len([line for line in worktrees.splitlines() if line.startswith("worktree ")]) == 1
    assert _git("show-ref", cwd=tmp_path / "origin.git").stdout == "", "something reached origin"


def test_the_previous_publications_history_is_read_back_from_the_site(checkout, build, published_site) -> None:
    """All five of Allure's history files come down from `SITE_URL` into the merged
    results, where `allure generate` looks for them, each exactly as it was served —
    and they are there before it runs, so the generated report carries them on."""
    served = _as_served()
    _publish_history(published_site, served)

    result = build(published_site.url)

    assert result.returncode == 0, result.stderr
    expected = {f"{name}.json": body for name, body in served.items()}
    assert _carried(checkout) == expected
    assert _reported(checkout) == expected, "the history arrived after the report was generated"
    assert sorted(published_site.requested) == sorted(f"/report/history/{name}.json" for name in HISTORY)


def test_an_address_given_without_its_trailing_slash_reaches_the_same_history(
    checkout, build, published_site
) -> None:
    """`SITE_URL` names a directory. Written without its last slash, a path joined
    onto it would land beside the site rather than inside it, and the trend would be
    lost to a typo; the script puts the slash back."""
    _publish_history(published_site, _as_served())

    result = build(published_site.url.rstrip("/"))

    assert result.returncode == 0, result.stderr
    assert set(_carried(checkout)) == {f"{name}.json" for name in HISTORY}


def test_a_history_file_that_is_not_json_is_left_out(checkout, build, published_site) -> None:
    """A body that does not parse — a download cut short, an error page served with
    a 200 — is not history, and it is not handed to Allure as if it were. The files
    that did arrive whole still carry the trend."""
    served = _as_served()
    served["history-trend"] = '[{"data": {"passed": 26'
    _publish_history(published_site, served)

    result = build(published_site.url)

    assert result.returncode == 0, result.stderr
    assert _carried(checkout) == {f"{name}.json": body for name, body in served.items() if name != "history-trend"}
    assert "history-trend.json" in result.stderr, "a file left out is named in the log"


def test_a_history_file_of_the_wrong_shape_is_left_out(checkout, build, published_site) -> None:
    """Parsing is not enough. `null` is valid JSON, and Allure 2.30.0 stops with a
    NullPointerException when that is what the per-test history holds — the night
    would lose its whole report to one bad file. So a file whose top level is not
    what Allure writes there is refused the same way as one that does not parse."""
    served = _as_served()
    served["history"] = "null"
    served["retry-trend"] = '{"data": {"run": 267, "retry": 0}}'
    _publish_history(published_site, served)

    result = build(published_site.url)

    assert result.returncode == 0, result.stderr
    assert set(_carried(checkout)) == {"history-trend.json", "duration-trend.json", "categories-trend.json"}


def test_a_site_with_no_history_yet_gives_a_report_with_no_trend(checkout, build, published_site) -> None:
    """The very first publication has nothing to carry over, and that is not a failure."""
    result = build(published_site.url)

    assert result.returncode == 0, result.stderr
    assert _carried(checkout) == {}
    assert "no Allure history" in result.stderr
    assert (checkout / "site" / "index.html").is_file()


def test_an_unreachable_site_gives_a_report_with_no_trend(checkout, build, unreachable_site_url) -> None:
    """A runner that cannot reach the site at all still builds the page: the trend
    starts again from this run, and the log says why rather than leaving it to be
    noticed on the chart."""
    result = build(unreachable_site_url)

    assert result.returncode == 0, result.stderr
    assert _carried(checkout) == {}
    assert "did not answer" in result.stderr
    assert (checkout / "site" / "index.html").is_file()


def test_starting_the_trend_over_asks_the_site_for_nothing(checkout, build, published_site) -> None:
    """`RESET_SHOWCASE_HISTORY` is the documented, off-by-default way to discard the
    published trend on purpose, for exactly one publication. Set, it must not carry
    any of that history over, and it does not even ask for it."""
    _publish_history(published_site, _as_served())

    result = build(published_site.url, RESET_SHOWCASE_HISTORY="1")

    assert result.returncode == 0, result.stderr
    assert _carried(checkout) == {}
    assert published_site.requested == []
