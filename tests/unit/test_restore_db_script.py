"""Last night's database comes back from the published site, or the night stops.

`scripts/restore-db.sh` runs before the nightly scrape and reads back the database
the previous publication carried at `data/scrapewatch.sqlite3`. A runner starts
with an empty checkout, and without that file every night would be a first night,
with every record reported as added. It is read over HTTP from `SITE_URL`, the
address the showcase is served from, and these tests point that at a local server
laid out the same way.

There are three outcomes and only three. The file is there: it is restored, byte
for byte. It is not there: that is a first night, and the scrape goes ahead with
nothing to compare against. Anything else — no answer, an error, a body that is
not a database — stops the script and leaves whatever was at the target alone,
because taking it for a first night would publish tonight's database, holding one
night, over the one that holds them all.
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from showcase.build import DATABASE_FILE, PAGE_URL

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "restore-db.sh"

#: Where the nightly job asks for the database to be restored, which is also where
#: the CLI's default storage URL opens it.
TARGET = Path("data") / DATABASE_FILE


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _database(path: Path) -> bytes:
    """A real SQLite file, so what the script accepts is checked against the real thing."""
    connection = sqlite3.connect(path)
    with connection:
        connection.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, taken_at TEXT)")
        connection.execute("INSERT INTO snapshots (taken_at) VALUES ('2026-09-22')")
    connection.close()
    return path.read_bytes()


@dataclass
class Restore:
    """The script, run from a plain directory — not a git checkout of anything."""

    workdir: Path
    env: dict[str, str]

    @property
    def target(self) -> Path:
        return self.workdir / TARGET

    def __call__(self, site_url: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(SCRIPT), str(TARGET)],
            cwd=self.workdir, capture_output=True, text=True, env={**self.env, "SITE_URL": site_url}, timeout=120,
        )


@pytest.fixture
def restore(tmp_path: Path, script_env: dict[str, str]) -> Restore:
    workdir = tmp_path / "runner"
    workdir.mkdir()
    return Restore(workdir=workdir, env=script_env)


# -- the file, read ----------------------------------------------------------------


def test_the_database_is_read_from_where_the_page_publishes_it() -> None:
    """The address is `showcase.build.PAGE_URL` and the name is the builder's own
    `DATABASE_FILE`: the page links the file there and the build stages it there,
    so the restore reads it from exactly there, and the three cannot drift apart."""
    text = _text()
    assert re.findall(r'SITE_URL="\$\{SITE_URL:-([^}"]+)\}"', text) == [PAGE_URL]
    assert re.findall(r'SOURCE="\$\{SITE_URL\}data/([^"]+)"', text) == [DATABASE_FILE]


def test_the_script_runs_no_git_command() -> None:
    """The database used to be read out of a published branch. It is read from the
    published site now, and nothing here needs a checkout, let alone a branch."""
    commands = [line.strip() for line in _text().splitlines() if line.strip() and not line.strip().startswith("#")]
    assert not [line for line in commands if re.search(r"\bgit\b", line)]


# -- the script, actually run ------------------------------------------------------


def test_the_published_database_is_restored_byte_for_byte(restore, published_site, tmp_path) -> None:
    published = _database(tmp_path / "published.sqlite3")
    published_site.publish(f"data/{DATABASE_FILE}", published)

    result = restore(published_site.url)

    assert result.returncode == 0, result.stderr
    assert restore.target.read_bytes() == published
    assert published_site.requested == [f"/data/{DATABASE_FILE}"]
    assert f"recovered {len(published)} bytes" in result.stdout


def test_an_address_given_without_its_trailing_slash_finds_the_same_database(
    restore, published_site, tmp_path
) -> None:
    """Joined onto an address missing its last slash, the file's path would ask the
    server for something beside the site, get a 404 back — and a 404 is a first
    night. A typo must not be able to start the history over."""
    published = _database(tmp_path / "published.sqlite3")
    published_site.publish(f"data/{DATABASE_FILE}", published)

    result = restore(published_site.url.rstrip("/"))

    assert result.returncode == 0, result.stderr
    assert restore.target.read_bytes() == published


def test_a_site_with_no_database_yet_is_a_first_night(restore, published_site) -> None:
    """Nothing published at that path is a legitimate state — the very first night —
    and the scrape goes ahead with nothing to compare against."""
    result = restore(published_site.url)

    assert result.returncode == 0, result.stderr
    assert not restore.target.exists()
    assert "first night" in result.stdout


def test_an_unreachable_site_stops_the_night(restore, unreachable_site_url) -> None:
    """No answer is not an answer. A night that went ahead without its yesterday
    would publish a database holding one night over the one that holds them all."""
    result = restore(unreachable_site_url)

    assert result.returncode != 0
    assert not restore.target.exists()
    assert "Stopping" in result.stderr


def test_a_body_that_is_not_a_database_is_not_restored(restore, published_site) -> None:
    """A site can answer 200 with something else — an error page, a truncated file.
    That stops the night too, and whatever was already at the target is left as it
    was, with no half-written file beside it."""
    published_site.publish(f"data/{DATABASE_FILE}", "<!doctype html><title>Not the database</title>")
    restore.target.parent.mkdir(parents=True)
    restore.target.write_bytes(b"the database that was here before")

    result = restore(published_site.url)

    assert result.returncode != 0
    assert restore.target.read_bytes() == b"the database that was here before"
    assert sorted(path.name for path in restore.target.parent.iterdir()) == [DATABASE_FILE]
    assert "not a SQLite database" in result.stderr
