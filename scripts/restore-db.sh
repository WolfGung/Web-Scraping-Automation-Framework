#!/usr/bin/env bash
# scripts/restore-db.sh — bring back the database the previous publication left.
#
# The nightly run compares what it collected tonight with what it collected last
# time, and a runner starts with an empty checkout: without this, every night
# would be a first night and every record would be reported as added. The
# published site carries the SQLite file at `data/scrapewatch.sqlite3` (see
# `scripts/build-showcase.sh`), so the previous publication is also the backup,
# and that is what is read back here, over HTTPS from `SITE_URL`.
#
# `SCRAPEWATCH_DB_URL` defaults to `sqlite:///data/scrapewatch.sqlite3`, so the
# CLI picks this file up with no flag and no configuration.
set -euo pipefail

TARGET="${1:-data/scrapewatch.sqlite3}"
# Where the previous publication is served; CI sets it. It names a directory, so
# it ends in exactly one slash whatever it was given — joined onto an address
# without one, the file's path would ask for something beside the site, and the
# 404 that came back would read as a first night.
SITE_URL="${SITE_URL:-https://wolfgung.github.io/Web-Scraping-Automation-Framework/}"
SITE_URL="${SITE_URL%/}/"
SOURCE="${SITE_URL}data/scrapewatch.sqlite3"

mkdir -p "$(dirname "$TARGET")"
# Downloaded beside the target and moved over it only once it has been checked,
# so a failed or partial download never replaces what was there.
PART="$TARGET.part"
trap 'rm -f "$PART"' EXIT

status=0
code="$(curl --silent --show-error --location --retry 3 --max-time 120 \
  --output "$PART" --write-out '%{http_code}' "$SOURCE")" || status=$?

if [ "$status" -eq 0 ] && [ "$code" = "404" ]; then
  # Absent is a legitimate state — the very first night, before anything was
  # ever published — and such a night just has nothing to compare with.
  echo "restore-db: no previous database — first night, the diff starts tomorrow"
  exit 0
fi

# Anything else that is not the file itself stops the night rather than passing
# for a first one. Starting over here would publish tonight's database, holding
# one night, over the one that holds every night kept so far — that history would
# be gone for good, while a site that could not be read tonight can very likely be
# read tomorrow.
if [ "$status" -ne 0 ] || [ "$code" != "200" ]; then
  echo "restore-db: $SOURCE could not be read (HTTP $code, curl exit $status). Stopping" >&2
  echo "restore-db: rather than starting the history over from an empty database." >&2
  exit 1
fi
if [ ! -s "$PART" ] || [ "$(head -c 15 "$PART" | tr -d '\0')" != "SQLite format 3" ]; then
  echo "restore-db: $SOURCE answered, but what came back is not a SQLite database." >&2
  echo "restore-db: Stopping rather than restoring it." >&2
  exit 1
fi

mv "$PART" "$TARGET"
# BSD wc pads the count with spaces; stripped, the message reads the same everywhere.
bytes="$(wc -c < "$TARGET" | tr -d ' ')"
echo "restore-db: recovered $bytes bytes from the previous publication into $TARGET"
