"""Merging three CI jobs' Allure results must not silently drop any of them.

`categories.json` and `environment.properties` are the two fixed-name files every
job writes into its own `--alluredir` (see `tests/conftest.py`). Copying three jobs'
results into one directory by filename — the way an artefact download naturally
would — lets the last copy win over the others without a trace, and the published
report's Environment panel would then describe one leg of a run that had three.
`showcase/merge.py` is what the publish step calls instead; these tests are the ones
that would fail if it went back to silently preferring a side.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from showcase.merge import merge_categories, merge_environment_properties

pytestmark = pytest.mark.unit


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_categories_from_a_single_source_is_used_as_is(tmp_path: Path) -> None:
    source = _write(tmp_path / "gate" / "categories.json", '[{"name": "Markup drift"}]')
    target = tmp_path / "merged" / "categories.json"

    merge_categories([source], target)

    assert target.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_identical_categories_from_every_job_merge_without_complaint(tmp_path: Path) -> None:
    payload = '[{"name": "Markup drift"}]'
    sources = [
        _write(tmp_path / job / "categories.json", payload) for job in ("gate", "e2e", "live")
    ]
    target = tmp_path / "merged" / "categories.json"

    merge_categories(sources, target)

    assert target.read_text(encoding="utf-8") == payload


def test_categories_that_disagree_between_jobs_stop_the_merge(tmp_path: Path) -> None:
    """All three copy the same file out of the repository, so a difference means
    something else wrote one of them — and picking a side would hide that."""
    gate = _write(tmp_path / "gate" / "categories.json", '[{"name": "Markup drift"}]')
    live = _write(tmp_path / "live" / "categories.json", '[{"name": "Something else"}]')

    with pytest.raises(ValueError, match="categories.json differs"):
        merge_categories([gate, live], tmp_path / "merged" / "categories.json")


def test_a_missing_categories_file_is_not_an_error(tmp_path: Path) -> None:
    """Writing it is a courtesy to the report, not a guarantee: `tests/conftest.py`
    warns rather than failing when it cannot copy the file into a results
    directory, so the merge must tolerate the result of that."""
    (tmp_path / "gate").mkdir()
    target = tmp_path / "merged" / "categories.json"

    merge_categories([tmp_path / "gate" / "categories.json"], target)

    assert not target.exists()


def test_environment_properties_from_every_job_are_unioned(tmp_path: Path) -> None:
    gate = _write(tmp_path / "gate" / "environment.properties", "gate.python=3.12.14\ngate.ci=true")
    e2e = _write(tmp_path / "e2e" / "environment.properties", "e2e.browser=chromium 141\ne2e.ci=true")
    live = _write(tmp_path / "live" / "environment.properties", "live.browser=chromium 141\nlive.ci=true")
    target = tmp_path / "merged" / "environment.properties"

    merge_environment_properties([gate, e2e, live], target)

    assert set(target.read_text(encoding="utf-8").splitlines()) == {
        "gate.python=3.12.14",
        "gate.ci=true",
        "e2e.browser=chromium 141",
        "e2e.ci=true",
        "live.browser=chromium 141",
        "live.ci=true",
    }


def test_the_same_key_and_value_in_two_jobs_is_not_a_collision(tmp_path: Path) -> None:
    """Which is what a local run produces: `tests/conftest.py` only qualifies keys
    by job when `GITHUB_JOB` is set, so outside CI every job writes plain keys."""
    gate = _write(tmp_path / "gate" / "environment.properties", "python=3.12.14")
    e2e = _write(tmp_path / "e2e" / "environment.properties", "python=3.12.14")
    target = tmp_path / "merged" / "environment.properties"

    merge_environment_properties([gate, e2e], target)

    assert target.read_text(encoding="utf-8").splitlines() == ["python=3.12.14"]


def test_an_unqualified_key_that_disagrees_between_jobs_is_loud_not_silent(tmp_path: Path) -> None:
    gate = _write(tmp_path / "gate" / "environment.properties", "browser=none")
    e2e = _write(tmp_path / "e2e" / "environment.properties", "browser=chromium 141")

    with pytest.raises(ValueError, match="environment.properties key 'browser' disagrees"):
        merge_environment_properties([gate, e2e], tmp_path / "merged" / "environment.properties")


def test_a_missing_environment_properties_file_is_not_an_error(tmp_path: Path) -> None:
    (tmp_path / "gate").mkdir()
    e2e = _write(tmp_path / "e2e" / "environment.properties", "e2e.browser=chromium 141")
    target = tmp_path / "merged" / "environment.properties"

    merge_environment_properties([tmp_path / "gate" / "environment.properties", e2e], target)

    assert target.read_text(encoding="utf-8").splitlines() == ["e2e.browser=chromium 141"]


def test_no_files_at_all_writes_nothing(tmp_path: Path) -> None:
    (tmp_path / "gate").mkdir()
    target = tmp_path / "merged" / "environment.properties"

    merge_environment_properties([tmp_path / "gate" / "environment.properties"], target)

    assert not target.exists()


# A missing *file* is ordinary; a missing *directory* is a job that never delivered.


@pytest.mark.parametrize("merge", [merge_categories, merge_environment_properties])
def test_a_missing_results_directory_stops_the_merge_and_names_it(tmp_path: Path, merge) -> None:
    """The realistic case: the live job died before its artefact was uploaded, or a
    download step was dropped from the workflow. Merging the two directories that
    did arrive would publish a report — and a figure on the page — missing a whole
    leg of the suite, which is exactly the kind of quiet wrong number this page
    exists not to have."""
    present = _write(tmp_path / "gate" / "environment.properties", "gate.ci=true")
    _write(tmp_path / "gate" / "categories.json", "[]")
    missing = tmp_path / "live" / present.name

    with pytest.raises(ValueError, match=r"no results directory at .*live"):
        merge([tmp_path / "gate" / missing.name, missing], tmp_path / "merged" / missing.name)


def test_an_empty_results_directory_is_not_a_missing_one(tmp_path: Path) -> None:
    """A job that ran and produced nothing (every test deselected, say) leaves a
    directory with no files in it. That is a fact about the run, not a delivery
    failure, and it must not stop a publication."""
    gate = _write(tmp_path / "gate" / "environment.properties", "gate.ci=true")
    (tmp_path / "live").mkdir()
    target = tmp_path / "merged" / "environment.properties"

    merge_environment_properties([gate, tmp_path / "live" / "environment.properties"], target)

    assert target.read_text(encoding="utf-8").splitlines() == ["gate.ci=true"]
