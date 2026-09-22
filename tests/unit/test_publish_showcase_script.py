"""The publish script must not keep its own copy of what gets published.

`showcase/build.py` decides which recording, which trace and which data files reach
`gh-pages`, and the page links exactly those. A second, independent rule in the
shell script — a glob, a hardcoded file name, a copy straight into the site — is how
a real file ends up published under a name nothing on the page ever mentions: it
happened in the sibling project this machinery is ported from, and it is why the
script now stages data beside the site and lets the builder select from it.

These tests do not re-implement the selection rule. They make sure there is nowhere
else for a second one to live, that the script hands the builder the real
directories instead of relying on defaults that only happen to be right, and that
the safety rails the script's own comments describe are still in the file.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from showcase.build import DATA_FILES, DATABASE_FILE, TRACE_NAME, VIDEO_NAME

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "publish-showcase.sh"


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _commands() -> list[str]:
    """The script's actual command lines: comments explain the rules, code breaks them."""
    return [
        line.strip()
        for line in _text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


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
    would put a file on `gh-pages` that the page never mentions — which is the same
    bug as the recording's, on a second artefact."""
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
            f"{name} is named in the publish script; the one place that decides what "
            f"is published is showcase.build.DATA_FILES"
        )


def test_the_database_is_staged_under_the_name_the_page_publishes() -> None:
    """The script does rename the database, because the local path is a setting and
    the published name is a contract: `scripts/restore-db.sh` reads it back by that
    name and the page links it by that name. Pinned here so the two cannot drift."""
    assert f"publish-data/{DATABASE_FILE}" in _text()
    assert DATABASE_FILE in {name for name, _ in DATA_FILES}


def test_the_script_refuses_to_push_outside_ci_by_default() -> None:
    """A local run must not be able to publish: the push is gated on either running
    under GitHub Actions or an explicit, named opt-in, not on whether this machine
    happens to have push credentials."""
    text = _text()
    assert "GITHUB_ACTIONS" in text
    assert "PUBLISH_SHOWCASE" in text


def test_the_script_replaces_the_published_history_rather_than_nesting_in_it() -> None:
    """gh-pages is a publication, not a history: the force-push of the orphan branch
    is what makes every run replace it completely."""
    assert "git push --force origin gh-pages-new:gh-pages" in _text()


def test_the_script_cleans_up_the_branch_it_creates() -> None:
    """The branch outlives the worktree that held it, and a leftover one collides
    with the next run in the same checkout."""
    assert "git branch -D gh-pages-new" in _text()


def test_the_published_branch_gets_a_nojekyll_file() -> None:
    """GitHub Pages runs Jekyll otherwise, and Jekyll drops every directory whose
    name begins with an underscore — which is most of an Allure report."""
    assert "touch .nojekyll" in _text()


def test_the_script_supports_starting_the_trend_over() -> None:
    """A documented, off-by-default escape hatch for discarding accumulated history,
    meant to be set for exactly one publication."""
    assert "RESET_SHOWCASE_HISTORY" in _text()


def test_the_carried_over_history_replaces_rather_than_nests() -> None:
    """`cp -r src dst` copies INTO an existing dst, which would leave the trend at
    `history/history/*.json`, where Allure does not look."""
    text = _text()
    assert 'rm -rf "$RESULTS/history"' in text
    assert re.search(r'cp -r published/report/history "\$RESULTS/history"', text)


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


# -- the script, actually run ----------------------------------------------------
#
# Everything above reads the file. These two run it, in a throwaway repository with
# a bare repository standing in for `origin`, because the one thing that cannot be
# proven by reading is the gate: a script that publishes when it should not have is
# the failure that already happened once in the sibling project this came from, and
# a comment saying it will not is not evidence.

ROOT = SCRIPT.parents[1]

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
#: tests are about, and generating a real one would need a JVM to prove a shell gate.
_FAKE_ALLURE = """#!/bin/sh
out=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; fi
  shift
done
mkdir -p "$out"
echo "<!doctype html><title>report</title>" > "$out/index.html"
"""


def _git(*args: str, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"},
    )


@pytest.fixture
def assembled(tmp_path):
    """A repository with everything the publish script reads, and a bare `origin`."""
    repo, origin = tmp_path / "repo", tmp_path / "origin.git"
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
    (repo / "media").mkdir()

    allure = tmp_path / "fake-allure"
    allure.write_text(_FAKE_ALLURE, encoding="utf-8")
    allure.chmod(0o755)

    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@example.invalid", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "the checkout the publish step runs in", cwd=repo)
    _git("init", "-q", "--bare", str(origin), cwd=tmp_path)
    _git("remote", "add", "origin", str(origin), cwd=repo)
    return repo, origin, allure


def _publish(repo, allure, **extra_env) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "ALLURE_CMD": str(allure),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    env.pop("GITHUB_ACTIONS", None)
    env.pop("PUBLISH_SHOWCASE", None)
    env.update(extra_env)
    return subprocess.run(
        ["bash", "scripts/publish-showcase.sh"], cwd=repo, capture_output=True, text=True, env=env
    )


def _refs(origin) -> str:
    return subprocess.run(
        ["git", "show-ref"], cwd=origin, capture_output=True, text=True
    ).stdout


def test_a_local_run_assembles_the_site_and_publishes_nothing(assembled) -> None:
    """The gate, exercised rather than read.

    A local run that turned out to have push credentials is how a "dry run" once
    published synthetic data to a real gh-pages branch. Neither `GITHUB_ACTIONS` nor
    `PUBLISH_SHOWCASE` is set here, and `origin` is a real repository this process
    can certainly push to — so the only thing stopping it is the gate itself.
    """
    repo, origin, allure = assembled

    result = _publish(repo, allure)

    assert result.returncode == 0, result.stderr
    assert "declining to push" in result.stderr
    assert "gh-pages" not in _refs(origin), "the gate let a local run publish"
    assert (repo / "site" / "index.html").is_file(), "assembly still has to happen; only the push is gated"
    # The orphan branch the script creates outlives the worktree that held it, and a
    # leftover collides with the next run in the same checkout.
    assert "gh-pages-new" not in _git("branch", "--list", cwd=repo).stdout


def test_an_explicit_opt_in_publishes_the_assembled_site(assembled) -> None:
    """And the other half: asked to publish, it does — `.nojekyll` and the page."""
    repo, origin, allure = assembled

    result = _publish(repo, allure, PUBLISH_SHOWCASE="1")

    assert result.returncode == 0, result.stderr
    assert "refs/heads/gh-pages" in _refs(origin)
    published = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "gh-pages"], cwd=origin, capture_output=True, text=True
    ).stdout.splitlines()
    assert ".nojekyll" in published, "GitHub Pages runs Jekyll otherwise, which eats the Allure report"
    assert "index.html" in published
    assert "gh-pages-new" not in _git("branch", "--list", cwd=repo).stdout
