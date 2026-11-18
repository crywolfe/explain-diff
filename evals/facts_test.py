#!/usr/bin/env python3
"""Tests gather_facts.py: risk hints, changed names, candidate tests, commits.

Builds a throwaway repository whose feature branch touches one file of each kind
the risk rules distinguish, then checks the hint, reasons, names and candidate
tests gather_facts.py reports for each. Also checks uncommitted changes.

Usage: python3 facts_test.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "plugins", "explain-diff", "skills", "explain-diff", "scripts")
failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True, text=True).stdout.strip()


def write(repo, files):
    for rel, text in files.items():
        p = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)


def facts(repo, target, tmp, name):
    t, f = os.path.join(tmp, name + "-target.json"), os.path.join(tmp, name + "-facts.json")
    subprocess.run([sys.executable, os.path.join(SCRIPTS, "resolve_target.py"), target, "--no-fetch", "--out", t], cwd=repo, capture_output=True)
    p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "gather_facts.py"), "--target", t, "--out", f], cwd=repo, capture_output=True, text=True)
    if p.returncode != 0:
        print(p.stdout, p.stderr)
        return None, p.stdout
    return json.load(open(f, encoding="utf-8")), p.stdout


def by_file(d, path):
    return [h for h in d["hunks"] if h["file"] == path]


def main():
    tmp = tempfile.mkdtemp(prefix="explain-diff-facts-")
    os.environ.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    repo = os.path.join(tmp, "repo")
    git(tmp, "init", "-q", "-b", "main", repo)
    base = {
        "src/auth/login.py": "def check_password(user, given):\n    return user.password == given\n",
        "src/keyboard.py": "LAYOUT = 'qwerty'\n",
        "src/runbook.py": "def pick(steps):\n    return steps[:3]\n",
        "src/core.py": "def helper():\n    return 1\n",
        "tests/test_login.py": "from src.auth.login import check_password\n\n\ndef test_wrong_password_is_refused():\n    assert not check_password(U, 'x')\n",
        "tests/test_other.py": "def test_unrelated():\n    assert 1 + 1 == 2\n",
        "tests/test_runbook.py": "from src import runbook\n\n\ndef test_pick_caps_steps():\n    assert runbook.pick([1, 2, 3, 4]) == [1, 2, 3]\n",
        "tests/test_auth_extra.py": "def test_placeholder():\n    pass\n",
        "tests/tést: unicode.py": "def test_accented_path_finds_it():\n    check_password(U, 'y')\n",
        "requirements.txt": "requests==2.31.0\n",
        "docs/guide.md": "# Guide\n",
        ".github/workflows/ci.yml": "on: push\njobs: {}\n",
        "config/settings.yaml": "name: demo\n",
    }
    write(repo, base)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "feature")
    core = "def helper():\n    return 1\n\n\ndef reconcile_ledger_rows(rows):\n" + "".join(f"    step_{i} = rows[{i}] if len(rows) > {i} else None\n" for i in range(70)) + "    return rows\n"
    write(repo, {
        "src/auth/login.py": "import hmac\n\n\ndef check_password(user, given):\n    return hmac.compare_digest(user.password, given)\n",
        "src/keyboard.py": "LAYOUT = 'dvorak'\n",
        "src/runbook.py": "def pick(steps):\n    return steps[:5]\n",
        "src/core.py": core,
        "tests/test_auth_extra.py": "def test_placeholder():\n    assert True\n",
        "requirements.txt": "requests==2.32.0\nthreading-utils==1.0\n",
        "docs/guide.md": "# Guide\n\nSet the auth token and the secret here.\n",
        ".github/workflows/ci.yml": "on: [push, pull_request]\njobs: {}\n",
        "config/settings.yaml": "name: demo\n" + "".join(f"default_modes_{i}: on\n" for i in range(25)),
    })
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Harden password check", "-m", "Use a constant-time comparison.")

    d, out = facts(repo, "main...feature", tmp, "branch")
    check("facts run on a branch", d is not None, out[-200:])
    if d is None:
        sys.exit(1)
    hint = {h["file"]: h["hint"] for h in d["hunks"]}
    check("CI workflow is high", hint.get(".github/workflows/ci.yml") == "high", str(by_file(d, ".github/workflows/ci.yml")[:1]))
    login = by_file(d, "src/auth/login.py")
    check("auth path is high", login and login[0]["hint"] == "high", str(login[:1]))
    names = {t["test"] for h in login for t in h["candidateTests"]}
    check("the test that calls the changed function is a candidate", "test_wrong_password_is_refused" in names, str(names))
    direct = [t for h in login for t in h["candidateTests"] if t["test"] == "test_wrong_password_is_refused"]
    check("a test that calls the function is flagged calls", direct and direct[0]["calls"], str(direct))
    body = d["testBodies"].get("tests/test_login.py::test_wrong_password_is_refused", "")
    check("the candidate's source is in testBodies, and only its own", "check_password(U, 'x')" in body and "test_unrelated" not in body, repr(body))
    check("testBodies holds only candidates", all("::" in k for k in d["testBodies"]) and not any("test_unrelated" in k for k in d["testBodies"]), str(list(d["testBodies"])))
    check("a test in a non-ASCII path with a colon is found", "test_accented_path_finds_it" in names, str(names))
    check("an unrelated test is not a candidate", "test_unrelated" not in names, str(names))
    check("the changed function is named", any("check_password" in h["symbols"] for h in login), str([h["symbols"] for h in login]))
    check("a test file is low even with auth in its name", hint.get("tests/test_auth_extra.py") == "low", hint.get("tests/test_auth_extra.py"))
    check("docs stay low even when they mention tokens and secrets", hint.get("docs/guide.md") == "low", hint.get("docs/guide.md"))
    req = by_file(d, "requirements.txt")
    check("dependency manifest is medium, not raised by a package name", req and req[0]["hint"] == "medium" and req[0]["reasons"] == ["dependency manifest"], str(req[:1]))
    check("keyboard.py is not secrets handling", hint.get("src/keyboard.py") == "low", str(by_file(d, "src/keyboard.py")[:1]))
    core_h = by_file(d, "src/core.py")
    check("a large new function is medium, named", core_h and core_h[0]["hint"] == "medium" and "reconcile_ledger_rows" in core_h[0]["symbols"], str(core_h[:1])[:200])
    cfg = by_file(d, "config/settings.yaml")
    check("config keys are not read as definitions", cfg and not cfg[0]["symbols"] and cfg[0]["reasons"] == ["configuration"], str(cfg[:1])[:200])
    rb = by_file(d, "src/runbook.py")
    check("short names fall back to tests that use the module", any(t["test"] == "test_pick_caps_steps" for h in rb for t in h["candidateTests"]), str(rb[:1])[:300])
    check("tier is the highest hint", d["tier"] == "high", d["tier"])
    check("commits carry subject and body", d["commits"] and d["commits"][0]["subject"] == "Harden password check" and "constant-time" in d["commits"][0]["body"], str(d["commits"][:1]))
    check("summary names the tier and the auth file", "risk tier: high" in out and "src/auth/login.py" in out, out[:200])
    check("every hunk has a ref-ready location", all(":" in h["loc"] and "-" in h["loc"].rsplit(":", 1)[1] for h in d["hunks"]), "")

    # Uncommitted changes: candidates come from the working tree, there are no commits.
    git(repo, "checkout", "-q", "main")
    write(repo, {"src/auth/login.py": "def check_password(user, given):\n    return bool(given) and user.password == given\n",
                 "tests/test_login.py": base["tests/test_login.py"] + "\n\ndef test_empty_password_is_refused():\n    assert not check_password(U, '')\n"})
    d, out = facts(repo, "working", tmp, "working")
    names = {t["test"] for h in (d or {"hunks": []})["hunks"] for t in h["candidateTests"]}
    check("working mode: a new uncommitted test is a candidate", "test_empty_password_is_refused" in names, str(names))
    check("working mode: no commits", d is not None and d["commits"] == [], "")
    git(repo, "checkout", "-q", "--", ".")

    print(f"\n{len(failures)} failed" + (f": {failures}" if failures else ""))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
