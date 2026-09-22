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

import re
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
