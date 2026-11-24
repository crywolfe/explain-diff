#!/usr/bin/env python3
"""Commit the For QA notes to the change's branch: exactly one file, one local commit, never pushed.

Usage:
  python3 commit_for_qa.py --notes <out root>/<date>-for-qa-<slug>.md --slug <slug> --branch <change branch>

Run only after the user said yes. The run guard makes Claude Code ask the user before this script runs,
whatever the model reports. It refuses unless:
  - the notes file is inside the explain-diff output folder and starts with the explain-diff front matter;
  - <branch> is checked out and its tracked files are clean;
  - intent/<slug>/ exists (the spec layout /qa uses) and artifacts/<slug>/for-qa.md does not.
It commits only artifacts/<slug>/for-qa.md, with --no-verify so no hook from the change's branch runs,
and a Generated-by trailer so the commit is attributable to the skill.
"""
import argparse
import os
import re
import stat
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import safety  # noqa: E402

MESSAGE = "Add For QA notes from review"

# No hook or fsmonitor program runs: with --no-verify alone, prepare-commit-msg, post-commit and post-checkout still
# run, and a clone whose core.hooksPath points at a tracked folder (husky) would run the change's own hooks.
NO_HOOKS = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]


def git(*args, check=True):
    p = subprocess.run(["git", *NO_HOOKS, *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        sys.exit(f"git {args[0]}: {p.stderr.strip()[:300]}")
    return p.stdout.strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--notes", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--branch", required=True)
    a = ap.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,59}", a.slug):
        sys.exit("slug must be lowercase letters, digits and hyphens")
    notes = os.path.realpath(a.notes)
    if not safety.inside(notes, safety.out_root()) or not stat.S_ISREG(os.lstat(a.notes).st_mode):
        sys.exit(f"{a.notes} is not a file in the explain-diff output folder")
    text = open(notes, encoding="utf-8").read()
    if not text.startswith("---\nsource: explain-diff\n"):
        sys.exit(f"{a.notes} is not an explain-diff For QA file")
    os.chdir(git("rev-parse", "--show-toplevel"))
    if git("branch", "--show-current") != a.branch:
        sys.exit(f"{a.branch} is not checked out")
    if git("status", "--porcelain", "--untracked-files=no"):
        sys.exit("the working tree has uncommitted changes; commit nothing")
    if not os.path.isdir(os.path.join("intent", a.slug)):
        sys.exit(f"intent/{a.slug}/ does not exist")
    rel = f"artifacts/{a.slug}/for-qa.md"
    if os.path.lexists(rel):
        sys.exit(f"{rel} already exists; it is not overwritten")
    for part in ("artifacts", os.path.dirname(rel)):  # checked before anything is created
        if os.path.islink(part) or (os.path.lexists(part) and not os.path.isdir(part)):
            sys.exit(f"{part} is not a folder in the repository")
    os.makedirs(os.path.dirname(rel), exist_ok=True)
    if not safety.inside(os.path.dirname(rel), "."):
        sys.exit(f"{os.path.dirname(rel)} is not a folder in the repository")
    with open(rel, "x", encoding="utf-8") as f:
        f.write(text)
    git("add", "--", rel)
    git("commit", "--no-verify", "-q", "-m", MESSAGE, "-m", f"Generated-by: explain-diff/{safety.__version__}", "--", rel)
    print(f"committed {rel} on {a.branch} ({git('rev-parse', '--short', 'HEAD')}); not pushed")


if __name__ == "__main__":
    main()
