#!/usr/bin/env bash
# scripts/build-showcase.sh — assemble the showcase site in site/, and stop there.
#
# What goes in: the page, the Allure report of the whole run, the night's data
# (the exports and the database itself), the change report, and the recording and
# trace of the scroll. The report's trend comes from the previous publication: its
# Allure history is read back from the published site (`SITE_URL`) before the
# report is generated, and without it the report shows a single run and no trend.
#
# Nothing is published from here. The CI run uploads site/ as a GitHub Pages
# artifact and deploys it (the `showcase` and `deploy` jobs in
# .github/workflows/ci.yml), so this script has no branch to write and no commit
# to make. Run locally, it leaves the assembled site in site/ and nothing else.
set -euo pipefail

GATE_RESULTS="${1:-allure-results-gate}"
E2E_RESULTS="${2:-allure-results-e2e}"
LIVE_RESULTS="${3:-allure-results}"
RUN_DIR="${4:-data/run}"
MEDIA="${5:-media}"
EXPORTS="${6:-data/exports}"
DATABASE="${7:-data/scrapewatch.sqlite3}"
RESULTS="allure-results-merged"
SITE="site"
ALLURE_VERSION="2.30.0"
# Where the previous publication is served. CI sets it; the default is this
# repository's own GitHub Pages address. It names a directory, so it ends in
# exactly one slash whatever it was given: a path joined onto it without one
# would ask for something beside the site instead of inside it.
SITE_URL="${SITE_URL:-https://wolfgung.github.io/Web-Scraping-Automation-Framework/}"
SITE_URL="${SITE_URL%/}/"

# A prior run that died mid-way can leave any of these behind; this one starts
# clean rather than building on top of them.
rm -rf "$RESULTS" "$SITE" publish-data

# Three jobs, three raw results directories: the gate, the browser job and the
# nightly live run. The per-test result/container/attachment files are named by a
# random UUID per run, so copying every set straight into one directory cannot
# collide. `categories.json` and `environment.properties` are the two fixed-name
# exceptions — the first is identical by construction, the second genuinely
# differs between a job that opened no browser and one that did — and
# `showcase/merge.py` is what decides what "merged" means for those two, rather
# than letting whichever job's artefact this script copies last win by accident
# (the way a naive `cp -r` of all three into the same path, or an artefact
# download to a shared path, would).
mkdir -p "$RESULTS"
for dir in "$GATE_RESULTS" "$E2E_RESULTS" "$LIVE_RESULTS"; do
  # A missing directory is a job that did not upload its results, or an artefact
  # that never came down. Merging the ones that did arrive would publish a report
  # missing a whole leg of the suite, with a figure on the page to match, so this
  # stops instead — the same rule `showcase/merge.py` applies a few lines below,
  # stated here because this loop would otherwise skip the directory silently.
  if [ ! -d "$dir" ]; then
    echo "build-showcase: no results directory at $dir — the job that should have uploaded" >&2
    echo "build-showcase: it did not, so this run would publish a partial report. Stopping." >&2
    exit 1
  fi
  find "$dir" -mindepth 1 -maxdepth 1 \
    ! -name categories.json ! -name environment.properties \
    -exec cp -t "$RESULTS" {} +
done
python3 showcase/merge.py categories \
  "$GATE_RESULTS/categories.json" "$E2E_RESULTS/categories.json" "$LIVE_RESULTS/categories.json" \
  --out "$RESULTS/categories.json"
python3 showcase/merge.py environment \
  "$GATE_RESULTS/environment.properties" "$E2E_RESULTS/environment.properties" \
  "$LIVE_RESULTS/environment.properties" \
  --out "$RESULTS/environment.properties"

# True when the file $1 holds JSON whose top level is $2: an "object" or an
# "array". Parsing alone is not enough — `null` parses, and Allure 2.30.0 stops
# with a NullPointerException when that is what the per-test history holds — so
# a history file has to be of the shape Allure itself writes there.
json_of_shape() {
  python3 - "$1" "$2" <<'PY'
import json
import sys

path, shape = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as handle:
        value = json.load(handle)
except (OSError, ValueError):
    sys.exit(1)
sys.exit(0 if isinstance(value, dict if shape == "object" else list) else 1)
PY
}

# `RESET_SHOWCASE_HISTORY` is a documented, off-by-default escape hatch: set it
# and the trend starts over from this publication instead of carrying the
# previous one forward. It exists for exactly one situation — the published
# history needs to be discarded on purpose (contaminated by a run that was never
# a real CI publication, say) — and it is meant to be set for exactly one run and
# then unset again, both as deliberate, explained commits; it is not a normal
# knob left on. With it set, the published site is not even asked.
if [ -n "${RESET_SHOWCASE_HISTORY:-}" ]; then
  echo "build-showcase: RESET_SHOWCASE_HISTORY is set — starting the Allure trend over from this run" >&2
else
  # The previous publication's history, read back file by file from the site it
  # was deployed to. Any of it is allowed to be missing, and so is the site: the
  # very first publication has no history, a runner can fail to reach the site,
  # and either must degrade to a report with no trend rather than to a broken
  # run. What does arrive is written into place only once it has been checked, so
  # a body that is not what Allure wrote — an error page served with a 200, a
  # download cut short — never reaches the report as if it were history.
  mkdir -p "$RESULTS/history"
  carried=0
  for entry in history:object history-trend:array duration-trend:array \
               categories-trend:array retry-trend:array; do
    name="${entry%%:*}"
    shape="${entry#*:}"
    url="${SITE_URL}report/history/$name.json"
    part="$RESULTS/history/$name.json.part"
    status=0
    code="$(curl --silent --show-error --location --retry 2 --max-time 30 \
      --output "$part" --write-out '%{http_code}' "$url")" || status=$?
    if [ "$status" -ne 0 ] && [ "$code" = "000" ]; then
      # No HTTP answer at all: a DNS failure, a refused connection, a timeout.
      # The other files live on the same site and would fail the same way.
      rm -f "$part"
      echo "build-showcase: $SITE_URL did not answer (curl exit $status), so no history comes from it tonight" >&2
      break
    elif [ "$status" -ne 0 ] || [ "$code" != "200" ]; then
      echo "build-showcase: $url came back as HTTP $code (curl exit $status) — left out" >&2
    elif ! json_of_shape "$part" "$shape"; then
      echo "build-showcase: $url is not the JSON $shape Allure writes there — left out" >&2
    else
      mv "$part" "$RESULTS/history/$name.json"
      carried=$((carried + 1))
      continue
    fi
    rm -f "$part"
  done
  if [ "$carried" -gt 0 ]; then
    echo "build-showcase: carried over $carried Allure history files from the previous publication"
  else
    rmdir "$RESULTS/history"
    echo "build-showcase: no Allure history came back from ${SITE_URL}report/history/ — the trend starts at this run" >&2
  fi
fi

# The Allure command line, in the order it is likely to be available: an explicit
# override, then a real installation on PATH, then the npm package fetched on the
# spot. A GitHub runner has node and no Allure, so it lands on the last one; a
# developer's machine often has Allure installed and no interest in npx.
if [ -n "${ALLURE_CMD:-}" ]; then
  read -r -a allure_cmd <<< "$ALLURE_CMD"
elif command -v allure >/dev/null 2>&1; then
  allure_cmd=(allure)
else
  allure_cmd=(npx -y "allure-commandline@$ALLURE_VERSION")
fi
"${allure_cmd[@]}" generate "$RESULTS" --clean -o "$SITE/report"

# The night's data is staged beside the site, not into it: `build.py` is what
# copies a data file into `$SITE/data` and links it, and it copies only the names
# it knows (see `DATA_FILES`). Staging keeps that the only rule — an extra file
# that finds its way into the exports directory is then neither published nor
# linked, instead of being published under a name nothing on the page ever
# mentions. The database is staged under the name the page publishes it under,
# whatever it is called locally; it goes out on purpose, because it is what
# `scripts/restore-db.sh` reads back from the published site before the next run
# and the only reason tomorrow's diff has a yesterday.
mkdir -p publish-data
if [ -d "$EXPORTS" ]; then
  find "$EXPORTS" -mindepth 1 -maxdepth 1 -type f -exec cp -t publish-data {} +
fi
if [ -f "$DATABASE" ]; then
  cp "$DATABASE" publish-data/scrapewatch.sqlite3
fi

# `showcase/` is not part of the `scrapewatch` package that `pip install .` puts
# on site-packages — setuptools is configured to find packages under `src` only
# (pyproject.toml, [tool.setuptools.packages.find] where = ["src"]) — so the
# builder is only importable with the repository root on PYTHONPATH. This has no
# effect on a developer's own shell, where the checkout itself is usually already
# the working tree of an editable install; a CI runner has neither, so leaving
# this out fails there and nowhere else.
#
# `--media-dir` and `--data-dir` are passed and nothing is selected here:
# `build.py` picks up `scroll.webm`, `scroll-trace.zip` and the data files, copies
# them into the site and links exactly what it copied. A second, independent
# selection in this script is what once published a real recording that the page
# never referenced, in the sibling project this machinery comes from; one rule, in
# one place, is the fix that stayed.
PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" python3 showcase/build.py \
  --stats "$RUN_DIR/run-stats.json" \
  --changes "$RUN_DIR/change-report.json" \
  --results "$RESULTS" \
  --out "$SITE" \
  --media-dir "$MEDIA" \
  --data-dir publish-data \
  --revision "${GITHUB_SHA:-local}" \
  --run-url "${RUN_URL:-}"

rm -rf publish-data
# Harmless under a deployment from Actions, which serves the files as they are.
# Kept so the same directory still works served from a branch, where GitHub Pages
# would run Jekyll over it and drop every directory whose name starts with an
# underscore — which is most of an Allure report.
touch "$SITE/.nojekyll"
echo "build-showcase: the site is assembled in $SITE/"
