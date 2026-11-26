---
name: run-offline-evals
description: Run every offline check for the explain-diff skill (versions and pins, run guard, file and data safety, resolver, facts, page template and build, hostile input, approval gate, graders, model-eval dry run, example page). Run it after changing anything under plugins/explain-diff/ or evals/, or before committing.
disable-model-invocation: true
---

# Run the offline evals

These suites are Python and JavaScript files from this checkout, and they run on your machine. Only you can
start this skill (`/run-offline-evals`): `disable-model-invocation` keeps the model from running it on its own,
for example because text in a branch asked it to.

## 1. Check that you trust the code

Run from the repository root:

```
git log --format='%h %an %s' origin/main..HEAD
git status --short
```

Show the user the list. If any commit or change came from someone else (a contributor's branch, a pull request
checked out with `gh pr checkout`), stop and say so: their `evals/*.py` and `.claude/` run as the user, with
the user's files. CI runs these suites on every pull request, and pull requests from forks get no secrets. Run
them here only after the user says they have read the diff and want to go on.

This check only helps when the session started from a checkout you trust: a branch can rewrite this file too.

## 2. Run the suites

Run this as one Bash command. It installs Playwright from `evals/requirements-ci.txt` with hash checks (as CI
does) into a new private folder, deleted when the command ends, then runs every suite with only `HOME`, `PATH`,
`TMPDIR`, `LANG` and the Chromium location (`CHROMIUM_PATH`, `PLAYWRIGHT_BROWSERS_PATH`) set, so tokens in the
session's environment (`GH_TOKEN`, `ANTHROPIC_API_KEY`, cloud keys) do not reach the suites. `--no-config` keeps uv
from reading the checkout's uv settings. Chromium for Playwright must already be installed
(`uvx playwright@<version in evals/requirements-ci.txt> install chromium-headless-shell`, or `CHROMIUM_PATH`).

```
V="$(mktemp -d)" && trap 'rm -rf "$V"' EXIT && uv venv -q --no-config --python 3.12 "$V/venv" \
  && uv pip install -q --no-config --require-hashes --python "$V/venv/bin/python" -r evals/requirements-ci.txt \
  && env -i HOME="$HOME" PATH="$V/venv/bin:$PATH" TMPDIR="${TMPDIR:-/tmp}" LANG=en_US.UTF-8 V="$V" \
       CHROMIUM_PATH="${CHROMIUM_PATH:-}" PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-}" sh -c '
run() { name=$1; shift; "$@" > "$V/out" 2>&1; rc=$?
  printf "%s: exit %s: %s\n" "$name" "$rc" "$(tail -1 "$V/out")"; rg -m 5 "^SKIP|Traceback|^FAIL" "$V/out"; }
for t in evals/meta_test.py evals/guard_test.py evals/safety_test.py evals/template_test.py evals/target_test.py \
         evals/facts_test.py evals/hostile_snippet_test.py evals/model/test_graders.py; do run "$t" python3 "$t"; done
run gate node --test docs/gate/validate.test.js
run "model-eval dry run" python3 evals/model/run.py --dry-run --all --out "$V/dry"
run "example page" python3 evals/validate_page.py docs/example-explanation.html --browser --canary evil.example --canary __pwned'
```

Every line must show `exit 0` and a failure count of 0 (the gate's last line is its duration; its exit code is
what counts). Any `SKIP`, `Traceback` or `FAIL` line printed under a suite means it did not pass: a `SKIP  browser
checks` line means the browser checks did not run, so report that suite as not run, not as passed. If the install
fails on a hash, stop and report it: don't install Playwright another way.

If `templates/` changed, rebuild the example page first (in the same environment), then run the list above:

```
S=plugins/explain-diff/skills/explain-diff
Q="$(mktemp -d)"
EXPLAIN_DIFF_ANY_OUT=1 python3 $S/scripts/build_page.py --content $S/templates/content.example.json --target evals/fixtures/example/target.json \
  --snippets evals/fixtures/example/snippets.json --out docs/example-explanation.html \
  --qa-out "$Q/2026-11-02-for-qa-retry-backoff.md" --date 2026-11-02
```

Report each suite's line. These checks don't run the live model evals (`evals/model/run.py` without
`--dry-run`), which cost money; say so.
