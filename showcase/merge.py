"""Merge several CI jobs' raw Allure results into one directory before generating.

The gate, the browser job and the nightly live job each produce their own
`--alluredir`. The per-test result, container and attachment files in each are
named by a random UUID per run, so copying every set into one directory cannot
collide. Two fixed-name companion files are a different story: `categories.json`
is copied verbatim from the same file in the repository by every job (see
`tests/conftest.py::_copy_categories`), so it is identical by construction, but
`environment.properties` is written per job from what that job actually did (see
`_write_environment_properties`), so a job that opened no browser and one that
drove one genuinely disagree on it -- and anything else that varies between them
varies here.

Copying several results directories into the same target by filename the way an
artefact download naturally would makes the last job's copy of each fixed-name
file silently win over the others. For `categories.json` that loses nothing. For
`environment.properties` it means the merged report's Environment panel describes
only whichever download landed last, with nothing on the page to say the other
jobs' context was dropped. This module is the one place that decides what
"merged" means for those two files, so the publish script does not have to guess,
and a collision it cannot resolve fails loudly instead of quietly picking a side.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def _present(sources: list[Path]) -> list[Path]:
    """The sources that exist, with a missing *directory* treated as a failure.

    A file that is not there is ordinary: a job may legitimately not have written
    its `categories.json` (see `tests/conftest.py`, where every write is a courtesy
    to the report and never a reason to fail a run). A directory that is not there
    is a different thing entirely — it means the job that should have uploaded those
    results did not, or the artefact never came down — and merging the two jobs that
    did arrive would quietly publish a report missing a third of the suite. That is
    worth stopping for, and the message names the path that was expected.
    """
    for source in sources:
        if not source.parent.is_dir():
            raise ValueError(
                f"no results directory at {source.parent}; the job that should have "
                f"produced {source.name} did not, so the merged report would be "
                f"missing its results entirely"
            )
    return [source for source in sources if source.is_file()]


def merge_categories(sources: list[Path], target: Path) -> None:
    """Copy `categories.json` from whichever source has it.

    All sources are expected to carry the *same* file. The merge is really only a
    sanity check that this is still true, not a real combination: two sources that
    disagree are not silently resolved by preferring one over the other, because
    that would hide a change in the categories file without a trace -- it stops the
    run instead.
    """
    present = _present(sources)
    if not present:
        return
    first = present[0]
    for other in present[1:]:
        if first.read_bytes() != other.read_bytes():
            raise ValueError(
                f"categories.json differs between {first} and {other}; "
                "the merge cannot silently prefer one over the other"
            )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(first, target)


def merge_environment_properties(sources: list[Path], target: Path) -> None:
    """Union the jobs' environment panels into one file.

    Each source is expected to already carry keys qualified by job (see
    `tests/conftest.py::_write_environment_properties`, which prefixes every key
    with `$GITHUB_JOB.` when running in CI), so a genuine merge should never see
    the same key twice with two different values. A key that does turn up twice
    with different values anyway -- sources written outside CI, say, where keys are
    not qualified -- is a real collision this function cannot resolve on its own,
    and it is louder to fail than to keep whichever value happened to be read last.
    The same key with the *same* value in more than one source is not a collision;
    it is merged into one line.
    """
    present = _present(sources)
    if not present:
        return
    merged: dict[str, str] = {}
    for source in present:
        for raw_line in source.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key in merged and merged[key] != value:
                raise ValueError(
                    f"environment.properties key {key!r} disagrees between "
                    f"results ({merged[key]!r} vs {value!r} from {source}); "
                    "the merge cannot silently prefer one over the other"
                )
            merged[key] = value
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(f"{key}={value}" for key, value in merged.items()) + "\n",
        encoding="utf-8",
    )


_MERGERS = {
    "categories": merge_categories,
    "environment": merge_environment_properties,
}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=sorted(_MERGERS))
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        _MERGERS[args.kind](args.sources, args.out)
    except ValueError as exc:
        print(f"publish: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
