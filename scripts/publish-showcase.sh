#!/usr/bin/env bash
# scripts/publish-showcase.sh — assemble the site and push it to gh-pages.
#
# What goes out: the page, the Allure report of the whole run, the night's data
# (the exports and the database itself), the change report, and the recording and
# trace of the scroll. History is carried over from the previous publication:
# without it the report shows a single run and no trend.
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

# A prior run that died mid-way (a killed job, a crashed shell) can leave a
# worktree registered without a directory, or a directory without the
# registration; either half-state must not stop this run from starting clean.
# A prior *successful* local run leaves something too: `git checkout --orphan
# gh-pages-new` creates a local branch that removing the worktree does not
# delete, so a second local run collides on the branch name with "fatal: a
# branch named 'gh-pages-new' already exists".
rm -rf "$RESULTS" "$SITE" published publish-tree publish-data
git worktree prune
# A leftover gh-pages-new is only ever ours to delete when its tip commit is one
# we made: same bot identity, same "Publish showcase for ..." subject (both
# hard-coded a few lines below, at the `git commit` this branch comes from).
# Anything else with that name — someone's own work-in-progress branch — is not
# this script's to destroy; stop and say so instead.
if git show-ref --verify --quiet refs/heads/gh-pages-new; then
  branch_author="$(git log -1 --format='%ae' gh-pages-new)"
  branch_subject="$(git log -1 --format='%s' gh-pages-new)"
  if [ "$branch_author" = "41898282+github-actions[bot]@users.noreply.github.com" ] \
     && [[ "$branch_subject" == "Publish showcase for "* ]]; then
    git branch -D gh-pages-new
  else
    echo "publish: a local branch 'gh-pages-new' already exists and its last" >&2
    echo "commit is not this script's own (author: ${branch_author:-none}," >&2
    echo "subject: ${branch_subject:-none}). Refusing to delete it — move it" >&2
    echo "out of the way or remove it yourself, then rerun this script." >&2
    exit 1
  fi
fi

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
  if [ -d "$dir" ]; then
    find "$dir" -mindepth 1 -maxdepth 1 \
      ! -name categories.json ! -name environment.properties \
      -exec cp -t "$RESULTS" {} +
  fi
done
python3 showcase/merge.py categories \
  "$GATE_RESULTS/categories.json" "$E2E_RESULTS/categories.json" "$LIVE_RESULTS/categories.json" \
  --out "$RESULTS/categories.json"
python3 showcase/merge.py environment \
  "$GATE_RESULTS/environment.properties" "$E2E_RESULTS/environment.properties" \
  "$LIVE_RESULTS/environment.properties" \
  --out "$RESULTS/environment.properties"

# `RESET_SHOWCASE_HISTORY` is a documented, off-by-default escape hatch: set it
# and the trend starts over from this publication instead of carrying the
# previous one forward. It exists for exactly one situation — the published
# history needs to be discarded on purpose (contaminated by a run that was never
# a real CI publication, say) — and it is meant to be set for exactly one run and
# then unset again, both as deliberate, explained commits; it is not a normal
# knob left on. With it set, the fetch below is skipped entirely so a stale
# $RESULTS/history from a previous local run in this same directory cannot leak
# into a "fresh" history either.
if [ -n "${RESET_SHOWCASE_HISTORY:-}" ]; then
  echo "publish: RESET_SHOWCASE_HISTORY is set — starting the Allure trend over from this run" >&2
else
  # Fetching the previous publication is allowed to fail quietly: the very first
  # publication has no gh-pages history yet, and that must degrade to a report
  # with no trend, not to a broken run. Copying it once we already have it in
  # hand is a different posture: at that point the source is right there in the
  # worktree, so a failure means something is actually wrong (a permissions
  # problem, a corrupted history directory) and the run should stop rather than
  # silently publish a trendless report while claiming otherwise.
  git fetch origin gh-pages --depth 1 || true
  if git rev-parse --verify origin/gh-pages >/dev/null 2>&1; then
    git worktree add published origin/gh-pages
    if [ -d published/report/history ]; then
      # `cp -r src dst` copies INTO dst when dst already exists, nesting the
      # history at history/history/*.json instead of replacing it — Allure then
      # sees no history at the path it expects, and the report loses its trend
      # even though this step reported success. The destination is cleared first
      # to make the copy a replace, not a merge.
      rm -rf "$RESULTS/history"
      cp -r published/report/history "$RESULTS/history"
      echo "publish: carried over Allure history from the previous publication"
    fi
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
# linked, instead of being shipped to `gh-pages` under a name nothing on the page
# ever mentions. The database is staged under the name the page publishes it
# under, whatever it is called locally; it goes out on purpose, because it is what
# `scripts/restore-db.sh` pulls back before the next run and the only reason
# tomorrow's diff has a yesterday.
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
# selection in this script is what once shipped a real recording to `gh-pages`
# that the page never referenced, in the sibling project this machinery comes
# from; one rule, in one place, is the fix that stayed.
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
if [ -d published ]; then
  git worktree remove --force published
fi
git worktree add --detach publish-tree
cd publish-tree
git checkout --orphan gh-pages-new
git rm -rf . >/dev/null 2>&1 || true
cp -r "../$SITE/." .
# GitHub Pages runs Jekyll over a branch unless this file is there, and Jekyll
# drops every directory whose name starts with an underscore — which is most of
# an Allure report.
touch .nojekyll
git add -A
git -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -m "Publish showcase for ${GITHUB_SHA:-local}"

# The push is opt-in by construction, not unconditional: a local run without this
# gate is exactly what once turned a "dry run" into a real publication of
# synthetic data on a public gh-pages branch, after it turned out the machine
# already had working push credentials — the assumption that a missing credential
# would make the push a no-op does not hold everywhere. `GITHUB_ACTIONS` is set
# to `true` by GitHub Actions on every job, unprompted, so a genuine CI
# publication needs no extra configuration; `PUBLISH_SHOWCASE` is the explicit,
# named way for a person to opt in locally when they mean to actually publish.
# Anything else prints what would have been pushed and stops — assembly still
# succeeded, so this is not a failure, and the result can be inspected in `$SITE`
# or in the diff below without anything having left this machine.
if [ "${GITHUB_ACTIONS:-}" = "true" ] || [ -n "${PUBLISH_SHOWCASE:-}" ]; then
  # Plain --force, not --force-with-lease, and on purpose. gh-pages is a
  # publication, not a history: every single run is meant to replace it
  # completely, including a rerun of the very same commit, so there is no
  # "someone else's work I might clobber" case here for a lease to protect —
  # that is what the workflow's `concurrency` group (see ci.yml) is for, by
  # making sure only one publish is ever running at a time. A lease would also
  # tie this push's success to the early, best-effort `git fetch origin
  # gh-pages` above, which is deliberately allowed to fail quietly (a first
  # publication has no previous gh-pages to fetch). A bare `--force-with-lease`
  # uses that same fetch as its expected value, so a transient network blip on
  # the read side — something this script already shrugs off — would turn into a
  # hard failure on the write side instead: a legitimate publication rejected
  # for a reason that has nothing to do with a race.
  git push --force origin gh-pages-new:gh-pages
else
  echo "publish: not a CI run (GITHUB_ACTIONS is not 'true') and PUBLISH_SHOWCASE is" >&2
  echo "publish: not set — declining to push. The assembled site is in $SITE/ for" >&2
  echo "publish: inspection. This is what would have been published:" >&2
  if git rev-parse --verify origin/gh-pages >/dev/null 2>&1; then
    git --no-pager diff --stat origin/gh-pages HEAD >&2
  else
    git --no-pager show --stat HEAD >&2
  fi
  echo "publish: set PUBLISH_SHOWCASE=1 to push anyway." >&2
fi
cd ..
git worktree remove --force publish-tree
