#!/usr/bin/env python3
"""Decide exactly which change explain-diff explains, and against what base.

Usage:
  python3 resolve_target.py [TARGET] [--base REF] [--no-fetch] [--out FILE]

TARGET (optional) is one of:
  123, #123, or a pull request URL   the pull request (base and head from gh); seven or more
                                     digits that name a commit are read as a commit sha
  A...B                              B's changes since it split from A (merge-base)
  A..B                               the exact difference between A and B
  a branch name                      that branch since it split from the default branch
  a commit sha                       that one commit against its parent
  working                            uncommitted changes (staged and unstaged) against HEAD
  (nothing)                          see "No target" below

No target: the current branch's open pull request if there is one; otherwise the
current branch since it split from origin/<default>; otherwise, on the default
branch with uncommitted changes, those changes. On the default branch with a
clean tree there is nothing to infer, so status is "ambiguous" with candidates,
and the caller asks the user.

A branch base is always the remote default branch (origin/main), fetched first,
never a local branch that may be stale. --base REF overrides the base.

Prints JSON (and writes it to --out): status, mode, change, base, head,
mergeBase, diffArgs (pass to git diff), files with line counts, pr, warnings,
and summary, the line to show the user before any other work.
Exit code: 0 ok, 2 ambiguous, 1 error.
"""
import argparse
import json
import re
import subprocess
import sys

PR_URL = re.compile(r"^https?://[^/]+/[^/]+/[^/]+/pull/(\d+)(?:[/?#].*)?$")


def run(*args, check=True, timeout=60, strip=True):
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])}: {p.stderr.strip()[:300]}")
    if p.returncode != 0:
        return None
    return p.stdout.strip() if strip else p.stdout


def git(*args, check=True):
    return run("git", *args, check=check)


def sha(ref):
    return git("rev-parse", "--verify", "--quiet", ref + "^{commit}", check=False)


def short(s):
    return s[:7] if s else s


def gh_json(*args):
    try:
        out = run("gh", *args, check=False, timeout=30)
        return json.loads(out) if out else None
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return None


def default_branch(remote):
    if remote:
        ref = git("symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD", check=False)
        if ref:
            return ref.split("/", 1)[1]
        info = gh_json("repo", "view", "--json", "defaultBranchRef")
        if info and info.get("defaultBranchRef", {}).get("name"):
            return info["defaultBranchRef"]["name"]
    for name in ("main", "master", "trunk", "develop"):
        if sha(f"{remote}/{name}" if remote else name):
            return name
    return None


def base_ref(remote, branch):
    """The remote copy of a branch when there is one: a local copy may be behind."""
    if remote and sha(f"{remote}/{branch}"):
        return f"{remote}/{branch}"
    return branch if sha(branch) else None


STATUS = {"A": "added", "D": "deleted", "M": "modified", "R": "renamed", "C": "copied", "T": "type changed"}


def file_stats(diff_args):
    """Changed files with line counts. -z keeps paths exact (no quoting of spaces or non-ASCII names)."""
    files = {}
    tok = (run("git", "diff", "--numstat", "-z", "-M", *diff_args, check=False, strip=False) or "").split("\0")
    i = 0
    while i < len(tok):
        head, i = tok[i], i + 1
        if not head:
            continue
        add, dele, path = head.split("\t", 2)
        if not path:  # a rename or copy: the old and new paths follow
            path, i = tok[i + 1], i + 2
        files[path] = {"path": path, "additions": None if add == "-" else int(add), "deletions": None if dele == "-" else int(dele)}
    tok = (run("git", "diff", "--name-status", "-z", "-M", *diff_args, check=False, strip=False) or "").split("\0")
    i = 0
    while i < len(tok):
        st, i = tok[i], i + 1
        if not st:
            continue
        if st[0] in "RC":
            old, path, i = tok[i], tok[i + 1], i + 2
        else:
            old, path, i = None, tok[i], i + 1
        if path in files:
            files[path]["status"] = STATUS.get(st[0], st)
            if old:
                files[path]["oldPath"] = old
    return list(files.values())


def untracked():
    out = git("ls-files", "--others", "--exclude-standard", check=False) or ""
    return [p for p in out.splitlines() if p]


def dirty():
    return bool(git("status", "--porcelain", "--untracked-files=no", check=False))


def resolve(target, base_override, fetch):
    warnings = []
    root = git("rev-parse", "--show-toplevel")
    remotes = (git("remote", check=False) or "").split()
    remote = "origin" if "origin" in remotes else (remotes[0] if remotes else None)
    if fetch and remote:
        try:
            run("git", "fetch", "--quiet", remote, check=True, timeout=90)
        except Exception as e:  # offline is fine; say so
            warnings.append(f"could not fetch {remote}; remote branches may be stale ({str(e)[:120]})")
    default = default_branch(remote)
    current = git("branch", "--show-current", check=False) or None
    head_sha = sha("HEAD")
    out = {"status": "ok", "repo": root, "remote": remote, "defaultBranch": default, "currentBranch": current,
           "mode": None, "change": None, "base": None, "head": None, "mergeBase": None, "diffArgs": None,
           "pr": None, "files": [], "additions": 0, "deletions": 0, "warnings": warnings, "candidates": [], "summary": ""}

    if default and remote and sha(default) and sha(f"{remote}/{default}") and sha(default) != sha(f"{remote}/{default}"):
        behind = git("rev-list", "--count", f"{default}..{remote}/{default}", check=False)
        if behind and behind != "0":
            warnings.append(f"local {default} is {behind} commit(s) behind {remote}/{default}; {remote}/{default} is used as the base")

    def branch_change(head_ref, head_label):
        b = base_override or base_ref(remote, default)
        if not b:
            raise RuntimeError("no default branch found; pass --base")
        h = sha(head_ref)
        mb = git("merge-base", b, h, check=False)
        if not mb:
            raise RuntimeError(f"{head_label} shares no history with {b}")
        out.update(mode="branch", change=head_label, base={"ref": b, "sha": sha(b)}, head={"ref": head_label, "sha": h},
                   mergeBase=mb, diffArgs=[mb, h])

    def pr_change(number, url=None):
        pr = gh_json("pr", "view", url or str(number), "--json", "number,title,url,state,baseRefName,headRefName,headRefOid,isCrossRepository,mergeCommit,commits")
        if not pr:
            raise RuntimeError(f"gh could not read pull request #{number} (is gh installed and signed in?)")
        if url:  # a URL names its repository; never explain the same number in this one instead
            here = gh_json("repo", "view", "--json", "url")
            if not here or not str(pr.get("url", "")).lower().startswith(here.get("url", "").lower().rstrip("/") + "/pull/"):
                raise RuntimeError(f"{url} is not a pull request of this repository ({(here or {}).get('url', 'unknown')}); run from a clone of its repository")
        h = pr["headRefOid"]
        if not sha(h) and remote:
            run("git", "fetch", "--quiet", remote, f"pull/{pr['number']}/head", check=False, timeout=90)
        if not sha(h):
            raise RuntimeError(f"head commit {short(h)} of #{pr['number']} is not available locally; fetch it and retry")
        b = base_override or base_ref(remote, pr["baseRefName"])
        if not b:
            raise RuntimeError(f"base branch {pr['baseRefName']} not found locally")
        mb = git("merge-base", b, h, check=False)
        if not mb:
            raise RuntimeError(f"#{pr['number']} shares no history with {b} (a shallow clone? run git fetch --unshallow)")
        merge = (pr.get("mergeCommit") or {}).get("oid")
        base_info = {"ref": b, "sha": sha(b)}
        if pr.get("state") == "MERGED" and merge and sha(merge):
            # After a merge the base contains the head, so diff against the base as it was when merged.
            parents = (git("rev-list", "--parents", "-n", "1", merge) or "").split()[1:]
            if parents:
                base_info = {"ref": f"{b} before #{pr['number']} merged", "sha": parents[0]}
            if len(parents) > 1:
                mb = git("merge-base", parents[0], h, check=False)
            elif parents:
                # Squash merge: one commit holds the whole change. Rebase merge: the PR's commits were
                # replayed onto the base, ending at the merge commit; their subjects match the PR's.
                n = len(pr.get("commits") or [])
                subjects = (git("log", f"-{n}", "--format=%s", merge, check=False) or "").splitlines() if n > 1 else []
                pr_subjects = [c.get("messageHeadline", "") for c in pr.get("commits") or []]
                start = sha(f"{merge}~{n}") if n > 1 else None
                if start and sorted(subjects) == sorted(pr_subjects):
                    h, mb = merge, start
                    base_info = {"ref": f"{b} before #{pr['number']} merged", "sha": start}
                    warnings.append(f"#{pr['number']} was rebase merged; its {n} replayed commits ending at {short(merge)} are explained")
                else:
                    h, mb = merge, parents[0]
                    warnings.append(f"#{pr['number']} was squash merged; its merge commit {short(merge)} is explained")
        out.update(mode="pr", change=f"#{pr['number']} {pr['headRefName']}", base=base_info,
                   head={"ref": pr["headRefName"], "sha": h}, mergeBase=mb, diffArgs=[mb, h],
                   pr={k: pr.get(k) for k in ("number", "title", "url", "state", "baseRefName", "headRefName")})
        if pr.get("state") and pr["state"] != "OPEN":
            warnings.append(f"#{pr['number']} is {pr['state'].lower()}")
        if current == pr["headRefName"] and head_sha != h:
            warnings.append(f"your checkout of {current} is at {short(head_sha)}, the PR head is {short(h)}; the PR head is explained")
        if current == pr["headRefName"] and dirty():
            warnings.append("uncommitted changes in your checkout are not part of the PR and are not explained")

    def working_change():
        out.update(mode="working", change=f"uncommitted changes on {current or short(head_sha)}",
                   base={"ref": "HEAD", "sha": head_sha}, head={"ref": "working tree", "sha": None},
                   mergeBase=head_sha, diffArgs=["HEAD"])
        new = untracked()
        if new:
            warnings.append(f"{len(new)} untracked file(s) are not in the diff until added: {', '.join(new[:5])}")

    t = (target or "").strip()
    if t.startswith("-"):
        raise RuntimeError(f"{t!r} is not a target; a target cannot start with '-'")
    m = PR_URL.match(t)
    # Bare digits are a PR number when short; seven or more digits that name a commit are a sha (0703007).
    digits_sha = re.fullmatch(r"\d{7,40}", t) and sha(t)
    if m or re.fullmatch(r"#\d+", t) or (re.fullmatch(r"\d+", t) and not digits_sha):
        pr_change(int(m.group(1) if m else t.lstrip("#")), url=t if m else None)
    elif t in ("working", "uncommitted", "staged", "worktree"):
        working_change()
    elif "..." in t:
        a, b = t.split("...", 1)
        a, b = a or "HEAD", b or "HEAD"
        if not sha(a) or not sha(b):
            raise RuntimeError(f"cannot resolve {t}")
        mb = git("merge-base", a, b)
        out.update(mode="range", change=t, base={"ref": a, "sha": sha(a)}, head={"ref": b, "sha": sha(b)}, mergeBase=mb, diffArgs=[mb, sha(b)])
    elif ".." in t:
        a, b = t.split("..", 1)
        a, b = a or "HEAD", b or "HEAD"
        if not sha(a) or not sha(b):
            raise RuntimeError(f"cannot resolve {t}")
        out.update(mode="range", change=t, base={"ref": a, "sha": sha(a)}, head={"ref": b, "sha": sha(b)}, mergeBase=sha(a), diffArgs=[sha(a), sha(b)])
        if git("merge-base", "--is-ancestor", sha(a), sha(b), check=False) is None:
            warnings.append(f"{a} is not an ancestor of {b}: the diff includes changes made on {a} since they split; use {a}...{b} for {b}'s changes only")
    elif t:
        # HEAD (and HEAD~1, abc123) name commits; origin/HEAD must not turn HEAD into a branch.
        is_branch = not t.startswith("HEAD") and (sha(f"refs/heads/{t}") or (remote and sha(f"refs/remotes/{remote}/{t}")))
        if not is_branch and "/" in t and not t.endswith("/HEAD") and sha(f"refs/remotes/{t}"):  # origin/feature
            branch_change(f"refs/remotes/{t}", t)
        elif is_branch:
            ref = f"{remote}/{t}" if remote and sha(f"refs/remotes/{remote}/{t}") and not sha(f"refs/heads/{t}") else t
            if remote and sha(f"refs/heads/{t}") and sha(f"refs/remotes/{remote}/{t}") and sha(t) != sha(f"{remote}/{t}"):
                warnings.append(f"local {t} ({short(sha(t))}) differs from {remote}/{t} ({short(sha(remote + '/' + t))}); the local branch is explained")
            branch_change(ref, t)
        elif sha(t):
            c = sha(t)
            parents = (git("rev-list", "--parents", "-n", "1", c) or "").split()[1:]
            if not parents:
                raise RuntimeError(f"{t} is a root commit; pass a range")
            if len(parents) > 1:
                warnings.append(f"{short(c)} is a merge commit; it is compared with its first parent")
            out.update(mode="commit", change=f"commit {short(c)}", base={"ref": f"{short(c)}^", "sha": parents[0]},
                       head={"ref": short(c), "sha": c}, mergeBase=parents[0], diffArgs=[parents[0], c])
        else:
            raise RuntimeError(f"{t!r} is not a pull request, branch, commit, or range in this repository")
    else:
        on_default = current is not None and current == default
        if current and not on_default:
            pr = gh_json("pr", "view", "--json", "number,state")
            if pr and pr.get("number") and pr.get("state", "OPEN") == "OPEN":
                pr_change(pr["number"])
            else:
                branch_change(current, current)
                if dirty() and out["mergeBase"] == out["head"]["sha"]:
                    # The branch has no commits of its own yet: its uncommitted changes are the change.
                    split_from = out["base"]["ref"]
                    working_change()
                    warnings.append(f"{current} has no commits beyond {split_from}, so its uncommitted changes are explained")
                elif dirty():
                    warnings.append("uncommitted changes are not included; pass 'working' to explain them instead")
        elif dirty():
            working_change()
        else:
            out["status"] = "ambiguous"
            cands = []
            if head_sha:
                subj = git("log", "-1", "--format=%s", head_sha, check=False) or ""
                cands.append({"target": head_sha[:12], "label": f"the latest commit on {current or 'HEAD'}: {subj[:70]}"})
            for p in (gh_json("pr", "list", "--limit", "5", "--json", "number,title,headRefName") or []):
                cands.append({"target": str(p["number"]), "label": f"PR #{p['number']} {p['headRefName']}: {p['title'][:60]}"})
            recent = git("for-each-ref", "--sort=-committerdate", "--count=6", "--format=%(refname:short)", "refs/heads", check=False) or ""
            for b in recent.splitlines():
                if b != default and b != current:
                    cands.append({"target": b, "label": f"branch {b}"})
            out["candidates"] = cands[:8]
            out["summary"] = (f"Nothing to infer: {current or 'HEAD'} is the default branch with no uncommitted changes. "
                              "Ask which change to explain: " + "; ".join(c["label"] for c in cands[:5]))
            return out

    out["files"] = file_stats(out["diffArgs"])
    out["additions"] = sum(f.get("additions") or 0 for f in out["files"])
    out["deletions"] = sum(f.get("deletions") or 0 for f in out["files"])
    head_label = f"{out['head']['ref']} @{short(out['head']['sha'])}" if out["head"]["sha"] else out["head"]["ref"]
    base_label = f"{out['base']['ref']} @{short(out['base']['sha'])}"
    via = f" (merge-base {short(out['mergeBase'])})" if out["mergeBase"] and out["mergeBase"] != out["base"]["sha"] else ""
    what = f"PR #{out['pr']['number']}: {out['pr']['title']}. " if out["pr"] else ""
    out["summary"] = (f"{what}Explaining {head_label} against {base_label}{via}: {len(out['files'])} file(s), "
                      f"+{out['additions']}/-{out['deletions']}.")
    if not out["files"]:
        out["status"] = "error"
        if out["head"]["sha"] and out["mergeBase"] == out["head"]["sha"]:
            out["summary"] += (f" {out['head']['ref']} is already contained in {out['base']['ref']}, so nothing is left to explain. "
                               "Pass the pull request number, a commit, or a range instead.")
        else:
            out["summary"] += " The diff is empty."
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("target", nargs="?", default="")
    ap.add_argument("--base", help="compare against this ref instead of the default branch or PR base")
    ap.add_argument("--no-fetch", action="store_true", help="do not fetch the remote first")
    ap.add_argument("--out", help="also write the JSON here")
    ap.add_argument("--quiz", help=argparse.SUPPRESS)  # the skill's quiz-length option; accepted here so a leftover one is harmless
    a = ap.parse_args()
    try:
        res = resolve(a.target, a.base, not a.no_fetch)
    except Exception as e:
        res = {"status": "error", "summary": str(e), "warnings": []}
    text = json.dumps(res, indent=2)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    sys.exit({"ok": 0, "ambiguous": 2}.get(res["status"], 1))


if __name__ == "__main__":
    main()
