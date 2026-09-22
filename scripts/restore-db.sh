#!/usr/bin/env bash
# scripts/restore-db.sh — bring back the database the previous publication left.
#
# The nightly run compares what it collected tonight with what it collected last
# time, and a runner starts with an empty checkout: without this, every night
# would be a first night and every record would be reported as added. The
# published `gh-pages` branch carries the SQLite file at `data/scrapewatch.sqlite3`
# (see `scripts/publish-showcase.sh`), so the previous publication is also the
# backup, and that is what is read back here.
#
# `SCRAPEWATCH_DB_URL` defaults to `sqlite:///data/scrapewatch.sqlite3`, so the
# CLI picks this file up with no flag and no configuration.
set -euo pipefail

TARGET="${1:-data/scrapewatch.sqlite3}"
SOURCE="origin/gh-pages:data/scrapewatch.sqlite3"

# Allowed to fail, quietly: the very first run has no gh-pages branch to fetch,
# and a first night is a legitimate state — it just has nothing to compare with.
git fetch --depth=1 origin gh-pages >/dev/null 2>&1 || true

if git cat-file -e "$SOURCE" 2>/dev/null; then
  mkdir -p "$(dirname "$TARGET")"
  # `git show`, not a checkout: the branch must not be laid over the working
  # tree, only this one file read out of it.
  git show "$SOURCE" > "$TARGET"
  echo "restore-db: recovered $(wc -c < "$TARGET") bytes from the previous publication into $TARGET"
else
  echo "restore-db: no previous database — first night, the diff starts tomorrow"
fi
