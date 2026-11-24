#!/usr/bin/env python3
"""Tests resolve_target.py: which change is explained, and against what base.

Builds throwaway repositories (a bare "origin", a clone with a stale local main,
a feature branch, uncommitted edits) and checks each way a target can be named.
The case that motivated the script: local main is behind origin/main, and the
feature branch must still be compared with origin/main from where it split off,
not with the stale local main and not with the tip of origin/main.

Usage: python3 target_test.py
"""
import json
import os
import subprocess
import sys
import tempfile

# Test outputs go to throwaway folders, not the skill's private output folder.
os.environ["EXPLAIN_DIFF_ANY_OUT"] = "1"

HERE = os.path.dirname(os.path.abspath(__file__))
RESOLVE = os.path.join(HERE, "..", "plugins", "explain-diff", "skills", "explain-diff", "scripts", "resolve_target.py")
sys.path.insert(0, os.path.join(HERE, "model"))
from run import GH_SHIM  # noqa: E402

failures = []
GH_REPLAY = r"""#!/usr/bin/env python3
import os, sys
d = os.environ["GH_REPLAY_DIR"]
a = sys.argv[1:]
name = "pr.json" if a[:2] == ["pr", "view"] else "repo.json" if a[:2] == ["repo", "view"] else None
if not name:
    sys.exit(1)
print(open(os.path.join(d, name)).read())
"""


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, path, text, msg):
    p = os.path.join(repo, path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)
    git(repo, "add", path)
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


def resolve(repo, *args, env=None):
    p = subprocess.run([sys.executable, RESOLVE, *args], cwd=repo, capture_output=True, text=True, env=env)
    return p.returncode, json.loads(p.stdout)


def main():
    tmp = tempfile.mkdtemp(prefix="explain-diff-target-")
    env0 = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    os.environ.update(env0)
    # No gh at all for most cases: an empty bin directory first on PATH hides any real gh.
    nogh = os.path.join(tmp, "nogh")
    os.makedirs(nogh)
    open(os.path.join(nogh, "gh"), "w").write("#!/bin/sh\necho 'gh: not available' >&2\nexit 1\n")
    os.chmod(os.path.join(nogh, "gh"), 0o755)
    env = dict(env0, PATH=nogh + os.pathsep + os.environ["PATH"])

    origin = os.path.join(tmp, "origin.git")
    seed = os.path.join(tmp, "seed")
    git(tmp, "init", "-q", "--bare", "-b", "main", origin)
    git(tmp, "init", "-q", "-b", "main", seed)
    commit(seed, "app.py", "a = 1\n", "base")
    git(seed, "remote", "add", "origin", origin)
    git(seed, "push", "-q", "origin", "main")

    repo = os.path.join(tmp, "repo")
    git(tmp, "clone", "-q", origin, repo)
    split = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "feature")
    feat = commit(repo, "app.py", "a = 1\nb = 2\n", "add b")
    git(repo, "push", "-q", "origin", "feature")
    # origin/main moves on after the branch split; local main stays behind.
    commit(seed, "other.py", "x = 1\n", "unrelated work on main")
    git(seed, "push", "-q", "origin", "main")

    rc, d = resolve(repo, env=env)
    check("no target on a feature branch: branch mode", d["mode"] == "branch" and rc == 0, d["summary"])
    check("base is origin/main, not the stale local main", d["base"]["ref"] == "origin/main", d["base"]["ref"])
    check("diff starts at the merge-base, so main's later work is not included",
          d["mergeBase"] == split and [f["path"] for f in d["files"]] == ["app.py"], str([f["path"] for f in d["files"]]))
    check("stale local main is reported", any("behind origin/main" in w for w in d["warnings"]), str(d["warnings"]))
    check("summary names head and base with commits", f"feature @{feat[:7]}" in d["summary"] and "origin/main @" in d["summary"], d["summary"])

    rc, d = resolve(repo, "feature", env=env)
    check("branch name target", d["mode"] == "branch" and d["mergeBase"] == split, d["summary"])

    rc, d = resolve(repo, "HEAD", env=env)
    rc2, d2 = resolve(repo, "HEAD", "--quiz", "8", env=env)
    check("a leftover --quiz N does not change the target", rc2 == rc and d2.get("head") == d.get("head"), str(d2)[:160])
    check("commit target: that commit against its parent", d["mode"] == "commit" and d["head"]["sha"] == feat and d["base"]["sha"] == split, d["summary"])

    # A short sha made only of digits is a commit, not PR #<digits>.
    git(repo, "checkout", "-q", "-b", "digits", feat)
    n = 0
    while not git(repo, "rev-parse", "--short=7", "HEAD").isdigit() and n < 400:
        n += 1
        commit(repo, "n.txt", str(n), f"try {n}")
    digit_sha = git(repo, "rev-parse", "--short=7", "HEAD")
    if digit_sha.isdigit():
        rc, d = resolve(repo, digit_sha, env=env)
        check("all-digit short sha is a commit, not a PR number", d.get("mode") == "commit", d["summary"])
    git(repo, "checkout", "-q", "feature")

    rc, d = resolve(repo, "origin/main...feature", env=env)
    check("three-dot range uses the merge-base", d["mode"] == "range" and d["mergeBase"] == split and len(d["files"]) == 1, d["summary"])

    rc, d = resolve(repo, "origin/main..feature", env=env)
    check("two-dot range is the exact difference, with a warning",
          len(d["files"]) == 2 and any("not an ancestor" in w for w in d["warnings"]), d["summary"])

    rc, d = resolve(repo, "--base", "main", env=env)
    check("--base overrides the base", d["base"]["ref"] == "main", d["summary"])

    open(os.path.join(repo, "app.py"), "a").write("c = 3\n")
    rc, d = resolve(repo, env=env)
    check("uncommitted edits on a branch are named, not silently mixed in", any("uncommitted" in w for w in d["warnings"]), str(d["warnings"]))
    rc, d = resolve(repo, "working", env=env)
    check("working target: uncommitted changes against HEAD", d["mode"] == "working" and d["base"]["sha"] == feat and d["files"], d["summary"])
    git(repo, "checkout", "-q", "--", "app.py")

    git(repo, "checkout", "-q", "-b", "fresh", "origin/main")
    open(os.path.join(repo, "app.py"), "a").write("e = 5\n")
    rc, d = resolve(repo, env=env)
    check("new branch with no commits yet: its uncommitted changes", d.get("mode") == "working" and any("no commits beyond" in w for w in d["warnings"]), d["summary"])
    git(repo, "checkout", "-q", "--", "app.py")

    git(repo, "checkout", "-q", "main")
    rc, d = resolve(repo, env=env)
    check("clean default branch with no target: ambiguous, with candidates", rc == 2 and d["status"] == "ambiguous" and d["candidates"], d["summary"])
    check("candidates include the feature branch", any(c["target"] == "feature" for c in d["candidates"]), str(d["candidates"]))

    open(os.path.join(repo, "app.py"), "a").write("d = 4\n")
    rc, d = resolve(repo, env=env)
    check("dirty default branch: explains the uncommitted changes", d["mode"] == "working", d["summary"])
    git(repo, "checkout", "-q", "--", "app.py")

    rc, d = resolve(repo, "no-such-thing", env=env)
    check("unknown target is an error that names it", rc == 1 and "no-such-thing" in d["summary"], d["summary"])

    # A branch already merged into origin/main has nothing left to explain.
    git(seed, "fetch", "-q", "origin")
    git(seed, "merge", "-q", "--no-edit", "origin/feature")
    git(seed, "push", "-q", "origin", "main")
    rc, d = resolve(repo, "feature", env=env)
    check("merged branch: error that says so", rc == 1 and "already contained" in d["summary"], d["summary"])

    # Pull request through the eval gh shim (the same one the model evals use).
    shim = os.path.join(tmp, "shim")
    os.makedirs(shim)
    open(os.path.join(shim, "gh"), "w").write(GH_SHIM)
    os.chmod(os.path.join(shim, "gh"), 0o755)
    git(repo, "checkout", "-q", "-b", "pr-branch", split)
    pr_head = commit(repo, "app.py", "a = 2\n", "change a")
    penv = dict(env0, PATH=shim + os.pathsep + os.environ["PATH"], ED_EVAL_GH_LOG=os.path.join(tmp, "gh.jsonl"),
                ED_EVAL_BRANCH="pr-branch", ED_EVAL_PR_TITLE="Change a", ED_EVAL_PR_BODY="")
    rc, d = resolve(repo, "7", "--no-fetch", env=penv)
    check("PR number: base and head from gh", d["mode"] == "pr" and d["head"]["sha"] == pr_head and d["pr"]["number"] == 7, d["summary"])
    check("PR base is the remote base branch", d["base"]["ref"] == "origin/main", d["base"]["ref"])
    rc, d = resolve(repo, "--no-fetch", env=penv)
    check("no target on a branch with an open PR: the PR", d["mode"] == "pr", d["summary"])

    # Remote-qualified branch names and paths git would quote.
    git(repo, "checkout", "-q", "-b", "odd-names", split)
    commit(repo, "dir with space/\u00e9t\u00e9.py", "x = 1\n", "odd file name")
    git(repo, "push", "-q", "origin", "odd-names")
    rc, d = resolve(repo, "origin/odd-names", "--no-fetch", env=env)
    check("origin/<branch> is a branch, not one commit", d.get("mode") == "branch" and d["mergeBase"] == split, d["summary"])
    check("non-ASCII and spaced paths are kept exactly", [f["path"] for f in d.get("files", [])] == ["dir with space/\u00e9t\u00e9.py"],
          str([f["path"] for f in d.get("files", [])]))
    rc, d = resolve(repo, "--", "--output=/tmp/x", env=env)
    check("a target starting with '-' is refused", rc == 1 and "cannot start" in d["summary"], d["summary"])

    # Merged pull requests, through a gh stub that replays canned JSON.
    stub = os.path.join(tmp, "stub")
    os.makedirs(stub)
    open(os.path.join(stub, "gh"), "w").write(GH_REPLAY)
    os.chmod(os.path.join(stub, "gh"), 0o755)
    senv = dict(env0, PATH=stub + os.pathsep + os.environ["PATH"], GH_REPLAY_DIR=tmp)

    def gh_says(pr, repo_url="https://github.com/example/repo"):
        json.dump(pr, open(os.path.join(tmp, "pr.json"), "w"))
        json.dump({"url": repo_url}, open(os.path.join(tmp, "repo.json"), "w"))

    git(seed, "fetch", "-q", "origin")
    git(seed, "checkout", "-q", "main")
    git(seed, "reset", "-q", "--hard", "origin/main")
    before = git(seed, "rev-parse", "HEAD")
    commit(seed, "r1.txt", "1\n", "rebased one")
    rebased = commit(seed, "r2.txt", "2\n", "rebased two")
    git(seed, "push", "-q", "origin", "main")
    git(repo, "fetch", "-q", "origin")
    pr = {"number": 9, "title": "Two commits", "url": "https://github.com/example/repo/pull/9", "state": "MERGED", "baseRefName": "main",
          "headRefName": "two", "headRefOid": rebased, "isCrossRepository": False, "mergeCommit": {"oid": rebased},
          "commits": [{"messageHeadline": "rebased one"}, {"messageHeadline": "rebased two"}]}
    gh_says(pr)
    rc, d = resolve(repo, "9", "--no-fetch", env=senv)
    check("rebase-merged PR: all of its commits, not only the last", sorted(f["path"] for f in d.get("files", [])) == ["r1.txt", "r2.txt"]
          and d["base"]["sha"] == before, d["summary"])
    pr["commits"] = [{"messageHeadline": "squash me"}, {"messageHeadline": "and me"}]
    gh_says(pr)
    rc, d = resolve(repo, "9", "--no-fetch", env=senv)
    check("squash-merged PR: its one merge commit", [f["path"] for f in d.get("files", [])] == ["r2.txt"] and any("squash" in w for w in d["warnings"]), d["summary"])

    pr.update(state="OPEN", mergeCommit=None, url="https://github.com/other/thing/pull/9")
    gh_says(pr)
    rc, d = resolve(repo, "https://github.com/other/thing/pull/9", "--no-fetch", env=senv)
    check("PR URL from another repository is refused", rc == 1 and "not a pull request of this repository" in d["summary"], d["summary"])
    pr["url"] = "https://github.com/example/repo/pull/9"
    gh_says(pr)
    rc, d = resolve(repo, "https://github.com/example/repo/pull/9", "--no-fetch", env=senv)
    check("PR URL from this repository resolves", d.get("mode") == "pr", d["summary"])

    git(repo, "checkout", "-q", "odd-names")
    gh_says({"number": 3, "state": "MERGED"})
    rc, d = resolve(repo, "--no-fetch", env=senv)
    check("no target: a merged PR for the branch is not used", d.get("mode") == "branch", d["summary"])

    print(f"\n{len(failures)} failed" + (f": {failures}" if failures else ""))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
