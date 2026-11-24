#!/usr/bin/env python3
"""Make the REVIEW.md draft branch: a worktree on review-md-setup, and one commit of REVIEW.md in it.

Usage:
  python3 review_md_branch.py prepare --target <work>/target.json
      creates branch review-md-setup from the remote default branch, in a new worktree under the
      explain-diff output folder, and prints the worktree path (review-init writes REVIEW.md there)
  python3 review_md_branch.py commit --worktree <path>
      commits REVIEW.md in that worktree, and nothing else

Run only after the user said yes. The run guard makes Claude Code ask the user before each step runs,
whatever the model reports. The reviewer's checkout and the change's branch are never touched, nothing
is pushed, no hook runs (--no-verify), and the commit carries a Generated-by trailer.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import safety  # noqa: E402

BRANCH = "review-md-setup"
MESSAGE = "Add REVIEW.md draft"

# No hook or fsmonitor program runs: with --no-verify alone, prepare-commit-msg, post-commit and post-checkout still
# run, and a clone whose core.hooksPath points at a tracked folder (husky) would run the change's own hooks.
NO_HOOKS = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]


def git(*args, cwd=None, check=True):
    p = subprocess.run(["git", *NO_HOOKS, *args], capture_output=True, text=True, cwd=cwd)
    if check and p.returncode != 0:
        sys.exit(f"git {args[0]}: {p.stderr.strip()[:300]}")
    return p.stdout.strip() if p.returncode == 0 else None


def prepare(a):
    target = json.load(open(a.target, encoding="utf-8"))
    os.chdir(target["repo"])
    if git("rev-parse", "--verify", "--quiet", f"refs/heads/{BRANCH}", check=False):
        sys.exit(f"branch {BRANCH} already exists; nothing was created")
    remote, default = target.get("remote"), target.get("defaultBranch")
    base = f"{remote}/{default}" if remote and git("rev-parse", "--verify", "--quiet", f"{remote}/{default}", check=False) else default
    if not base:
        sys.exit("no default branch to start from")
    path = tempfile.mkdtemp(prefix="review-md-setup-", dir=safety.out_root())
    os.rmdir(path)  # git worktree add creates it
    git("worktree", "add", "-q", "-b", BRANCH, path, base)
    print(path)


def commit(a):
    wt = os.path.realpath(a.worktree)
    if not safety.inside(wt, safety.out_root()):
        sys.exit("the worktree is not in the explain-diff output folder")
    if git("branch", "--show-current", cwd=wt) != BRANCH:
        sys.exit(f"{wt} is not on {BRANCH}")
    status = [line for line in (git("status", "--porcelain", cwd=wt) or "").splitlines() if line]
    if [line[3:] for line in status] != ["REVIEW.md"]:
        sys.exit(f"the worktree must change only REVIEW.md; it has: {[line[3:] for line in status]}")
    if os.path.islink(os.path.join(wt, "REVIEW.md")):
        sys.exit("REVIEW.md is a symlink")
    git("add", "--", "REVIEW.md", cwd=wt)
    git("commit", "--no-verify", "-q", "-m", MESSAGE, "-m", f"Generated-by: explain-diff/{safety.__version__}", "--", "REVIEW.md", cwd=wt)
    print(f"committed REVIEW.md on {BRANCH} ({git('rev-parse', '--short', 'HEAD', cwd=wt)}) in {wt}; not pushed")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--target", required=True)
    c = sub.add_parser("commit")
    c.add_argument("--worktree", required=True)
    a = ap.parse_args()
    prepare(a) if a.step == "prepare" else commit(a)


if __name__ == "__main__":
    main()
