#!/usr/bin/env python3
"""Hostile-input tests for the scripts' file and data safety (scripts/safety.py).

1. Output paths (F4): the scripts write only under the private output folder, files 0600 in a 0700
   folder; a path elsewhere (a shell startup file) is refused; an existing non-regular file is refused;
   a shared or loose output folder is refused; --work makes an unpredictable private folder.
2. Reads (F5): in working mode a tracked symlink to a file outside the repository shows its target
   path, never the file's contents; read_in_repo refuses paths that leave the repository.
3. Masking (F6): tokens and assigned secrets in snippets and personal email addresses in prose are
   masked on the page and in the For QA file, the build names them, and the page passes S17.

Runs offline in a private TMPDIR. Prints a last line with the failure count.
"""
import copy
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "plugins", "explain-diff", "skills", "explain-diff")
SCRIPTS = os.path.join(SKILL, "scripts")
FIX = os.path.join(ROOT, "evals", "fixtures", "example")
tmp = os.path.realpath(tempfile.mkdtemp(prefix="explain-diff-safety-"))
ENV = dict(os.environ, TMPDIR=tmp, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
ENV.pop("EXPLAIN_DIFF_ANY_OUT", None)
os.environ["TMPDIR"] = tmp
tempfile.tempdir = None
sys.path.insert(0, SCRIPTS)
import safety  # noqa: E402
import validate_page as V  # noqa: E402

OUT = safety.out_root()
CANARY = "CANARY-OUTSIDE-SECRET-7f3a"
GH_TOKEN = "ghp_" + "Zx9Qw8Er7Ty6Ui5Op4As3Df2Gh1Jk0Lm9Nb8"
failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def run(script, *args, cwd=None):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *args], capture_output=True, text=True, env=ENV, cwd=cwd)


def build(content, snippets, *extra):
    d = tempfile.mkdtemp(dir=OUT)
    cp, sp = os.path.join(d, "content.json"), os.path.join(d, "snippets.json")
    json.dump(content, open(cp, "w"))
    json.dump(snippets, open(sp, "w"))
    return run("build_page.py", "--content", cp, "--target", os.path.join(FIX, "target.json"), "--snippets", sp, "--date", "2026-11-02", *extra)


content = json.load(open(os.path.join(SKILL, "templates", "content.example.json"), encoding="utf-8"))
snippets = json.load(open(os.path.join(FIX, "snippets.json"), encoding="utf-8"))

print("== 1. Output paths ==")
home = os.path.join(tmp, "home")
os.makedirs(home)
rc = build(content, snippets, "--qa-out", os.path.join(home, ".zshrc"))
check("a For QA path outside the output folder is refused", rc.returncode != 0 and not os.path.exists(os.path.join(home, ".zshrc")), rc.stderr.strip()[-160:])
rc = build(content, snippets, "--out", os.path.join(home, "page.html"))
check("a page path outside the output folder is refused", rc.returncode != 0 and not os.path.exists(os.path.join(home, "page.html")), rc.stderr.strip()[-160:])
rc = build(content, snippets)
page = os.path.join(OUT, "2026-11-02-explanation-retry-backoff.html")
qa = os.path.join(OUT, "2026-11-02-for-qa-retry-backoff.md")
check("default outputs land in the output folder", rc.returncode == 0 and os.path.isfile(page) and os.path.isfile(qa), (rc.stdout + rc.stderr)[-200:])
check("the output folder is 0700", stat.S_IMODE(os.stat(OUT).st_mode) == 0o700, oct(os.stat(OUT).st_mode))
check("the page and For QA file are 0600", all(stat.S_IMODE(os.stat(p).st_mode) == 0o600 for p in (page, qa) if os.path.exists(p)), "")
os.unlink(qa)
os.symlink(os.path.join(home, "target"), qa)
rc = build(content, snippets)
check("an existing symlink at the output path is refused", rc.returncode != 0 and not os.path.exists(os.path.join(home, "target")), rc.stderr.strip()[-120:])
os.unlink(qa)
os.chmod(OUT, 0o755)
rc = subprocess.run([sys.executable, "-c", "import safety; safety.out_root()"], capture_output=True, text=True, env=ENV, cwd=SCRIPTS)
check("a loose output folder (0755) is refused", rc.returncode != 0 and "private" in rc.stderr, rc.stderr.strip()[-160:])
os.chmod(OUT, 0o700)

repo = os.path.join(tmp, "repo")
subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True, env=ENV)
os.makedirs(os.path.join(tmp, "outside"))
open(os.path.join(tmp, "outside", "secret.txt"), "w").write(CANARY + "\n")
open(os.path.join(repo, "app.py"), "w").write("def f():\n    return 1\n")
os.symlink("../outside/secret.txt", os.path.join(repo, "link.txt"))
subprocess.run(["git", "-C", repo, "add", "-A"], check=True, env=ENV)
subprocess.run(["git", "-C", repo, "commit", "-qm", "base"], check=True, env=ENV)
open(os.path.join(repo, "app.py"), "w").write("def f():\n    return 2\n")
rc = run("resolve_target.py", "working", "--no-fetch", "--work", cwd=repo)
work = (json.loads(rc.stdout) if rc.returncode == 0 else {}).get("work") or ""
check("--work makes a private folder in the output folder", work and safety.inside(work, OUT) and stat.S_IMODE(os.stat(work).st_mode) == 0o700
      and os.path.isfile(os.path.join(work, "target.json")), work or rc.stderr[-200:])
rc = run("resolve_target.py", "working", "--no-fetch", "--out", os.path.join(home, "target.json"), cwd=repo)
check("resolve_target --out outside the output folder is refused", rc.returncode != 0 and not os.path.exists(os.path.join(home, "target.json")), rc.stderr[-160:])

rc = run("resolve_target.py", "working", "--no-fetch", "--out", os.path.join(OUT, "logs", "x.jsonl"), cwd=repo)
check("a script cannot write the run guard's logs", rc.returncode != 0 and not os.path.exists(os.path.join(OUT, "logs", "x.jsonl")), rc.stderr[-160:])

print("\n== 2. Reads ==")
target = os.path.join(work, "target.json")
refs = os.path.join(work, "refs.json")
json.dump({"s_link": "link.txt:1-1", "s_app": "app.py:2-2"}, open(refs, "w"))
rc = run("extract_snippets.py", "--target", target, "--refs", refs, "--out", os.path.join(work, "snippets.json"), cwd=repo)
data = open(os.path.join(work, "snippets.json")).read() if rc.returncode == 0 else ""
check("working mode: a tracked symlink shows its target path, not the outside file", rc.returncode == 0 and CANARY not in data and "outside/secret.txt" in data,
      (rc.stderr or data)[-200:])
rc = run("gather_facts.py", "--target", target, "--out", os.path.join(work, "facts.json"), cwd=repo)
check("gather_facts runs in working mode", rc.returncode == 0 and CANARY not in open(os.path.join(work, "facts.json")).read(), rc.stderr[-200:])
for bad in ("../outside/secret.txt", os.path.join(tmp, "outside", "secret.txt")):
    try:
        safety.read_in_repo(repo, bad)
        check(f"read_in_repo refuses {bad}", False)
    except OSError:
        check(f"read_in_repo refuses {bad}", True)
check("read_in_repo gives a symlink's target path", safety.read_in_repo(repo, "link.txt") == "../outside/secret.txt")

print("\n== 3. Masking ==")
c2, s2 = copy.deepcopy(content), copy.deepcopy(snippets)
first = next(iter(s2.values()))
first["lines"][0]["text"] = f'GITHUB_TOKEN = "{GH_TOKEN}"'
first["lines"][1]["text"] = 'db_password = "Tr0ub4dor-and-3"'
c2["tldr"] = c2["tldr"] + " Ask jane.doe@acme-corp.io for access."
c2["review"]["forQa"]["items"][0]["try"] = f"Use the token {GH_TOKEN} against staging."
rc = build(c2, s2)
html = open(page, encoding="utf-8").read() if rc.returncode == 0 else ""
qtext = open(qa, encoding="utf-8").read() if rc.returncode == 0 else ""
check("the build succeeds and names what it masked", rc.returncode == 0 and "MASKED" in rc.stdout, (rc.stdout + rc.stderr)[-300:])
check("no token, password or personal email reaches the page", html and GH_TOKEN not in html and "Tr0ub4dor-and-3" not in html and "jane.doe" not in html, "")
check("no token reaches the For QA file", qtext and GH_TOKEN not in qtext, "")
check("the masked line is kept", "[masked GitHub token]" in html and "GITHUB_TOKEN = " in html, "")
res = V.static_checks(page, []) if html else []
check("the masked page passes S17 and S18", {c for c, lvl, _ in res if lvl == "PASS"} >= {"S17-no-secrets", "S18-page-hash"}, str([r for r in res if r[0] in ("S17-no-secrets", "S18-page-hash")]))

import time  # noqa: E402
FORMATS = {
    "AWS secret key with /": 'aws = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"',
    "unquoted YAML password": "  password: hunter2hunter2",
    ".env assignment": "export DB_PASSWORD=Tr0ub4dor-and-3",
    "Go := assignment": 'apiKey := "q8Zr2LmX0pT4vN7c"',
    "Ruby => assignment": 'password => "hunter2Secret99"',
    "a bare value with ;": "DB_PASSWORD=P@ss;w0rd!x",
    "GitLab token": "glpat-abcdefghij0123456789xy",
    "Slack webhook": "https://hooks.slack.com/services/T0000/B0000/XXXXXXXXXXXXXXXXXXXXXXXX",
}
for name, text in FORMATS.items():
    out, kinds = safety.mask(text)
    check(f"masks {name}", kinds and not safety.unmasked(out), out)
for name, text in {"a function call": "token = make_token(user)", "an env lookup": "password = os.environ['DB_PASSWORD']",
                   "a placeholder": "password: ${DB_PASSWORD}", "a temp path": "/var/folders/1y/pgftlhg10v37pbb9b476_gjc0000gn/T/x"}.items():
    check(f"leaves {name} alone", safety.mask(text)[1] == [], str(safety.mask(text)))
for name, text in {"a long key-name run": "_token" * 20000, "a long email-like run": "a." * 50000 + "@"}.items():
    t0 = time.time()
    safety.mask(text)
    safety.unmasked(text)
    check(f"masking {name} (100 KB) takes under 2 s", time.time() - t0 < 2, f"{time.time() - t0:.1f}s")

print("\n== 4. Commit scripts ==")
# A clone whose core.hooksPath points at a tracked folder (husky style): the change's hooks must not run.
qrepo = os.path.join(tmp, "qrepo")
subprocess.run(["git", "init", "-q", "-b", "feature", qrepo], check=True, env=ENV)
os.makedirs(os.path.join(qrepo, ".hooks"))
os.makedirs(os.path.join(qrepo, "intent", "export"))
open(os.path.join(qrepo, "intent", "export", "spec.md"), "w").write("# spec\n")
for hook in ("pre-commit", "prepare-commit-msg", "commit-msg", "post-commit"):
    hp = os.path.join(qrepo, ".hooks", hook)
    open(hp, "w").write(f"#!/bin/sh\ntouch '{tmp}/HOOK-RAN-{hook}'\n")
    os.chmod(hp, 0o755)
subprocess.run(["git", "-C", qrepo, "add", "-A"], check=True, env=ENV)
subprocess.run(["git", "-C", qrepo, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base"], check=True, env=ENV)
subprocess.run(["git", "-C", qrepo, "config", "core.hooksPath", ".hooks"], check=True, env=ENV)
notes = os.path.join(OUT, "2026-11-23-for-qa-export.md")
safety.write_private(notes, "---\nsource: explain-diff\nchange: feature\ncommit: x\n---\n# For QA\n")
rc = run("commit_for_qa.py", "--notes", notes, "--slug", "export", "--branch", "feature", cwd=qrepo)
ran = sorted(f for f in os.listdir(tmp) if f.startswith("HOOK-RAN-"))
check("commit_for_qa commits the notes", rc.returncode == 0 and "artifacts/export/for-qa.md" in rc.stdout, (rc.stdout + rc.stderr)[-200:])
check("commit_for_qa runs none of the repository's hooks", not ran, str(ran))
files = subprocess.run(["git", "-C", qrepo, "show", "--name-only", "--format=", "HEAD"], capture_output=True, text=True).stdout.split()
check("commit_for_qa commits only the notes file", files == ["artifacts/export/for-qa.md"], str(files))
subprocess.run(["git", "-C", qrepo, "reset", "-q", "--hard", "HEAD~1"], check=True, env=ENV)
os.symlink(os.path.join(tmp, "outside-artifacts"), os.path.join(qrepo, "artifacts"))
subprocess.run(["git", "-C", qrepo, "add", "artifacts"], check=True, env=ENV)
subprocess.run(["git", "-C", qrepo, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "link"], check=True, env=ENV)
rc = run("commit_for_qa.py", "--notes", notes, "--slug", "export", "--branch", "feature", cwd=qrepo)
check("a symlinked artifacts folder is refused before anything is created", rc.returncode != 0 and not os.path.exists(os.path.join(tmp, "outside-artifacts")),
      rc.stderr.strip()[-160:])

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{len(failures)} failed")
sys.exit(1 if failures else 0)
