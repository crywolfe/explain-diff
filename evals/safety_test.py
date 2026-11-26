#!/usr/bin/env python3
"""Hostile-input tests for the scripts' file and data safety (scripts/safety.py).

1. Output paths (F4): the scripts write only under the private output folder, files 0600 in a 0700
   folder; a path elsewhere (a shell startup file) is refused; an existing non-regular file is refused;
   a shared or loose output folder is refused; --work makes an unpredictable private folder.
2. Reads (F5): in working mode a tracked symlink to a file outside the repository shows its target
   path, never the file's contents; read_in_repo refuses paths that leave the repository.
3. Masking (F6): tokens and assigned secrets in snippets and personal email addresses in prose are
   masked on the page and in the For QA file, the build names them, and the page passes S17.
4. Commit scripts: a tracked hooks folder never runs; a symlinked artifacts folder is refused.
5. Run files and git (H1, H2): a forged target.json (an option in diffArgs, another repository, an option
   as the PR number or branch name) is refused before git sees it; a script reads an input only under its
   own name and writes no other script's file; a facts.json whose policy hashes, base commit or run folder
   differ stops the build; no script runs a tracked hook (git fetch included).

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
import time

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
# resolve_target.py --work starts a run only when the run guard just allowed it for that repository. These tests
# run the scripts directly, so run() stands in for the guard: before each --work call it writes the guard's note.
TICKET = os.path.join(OUT, "runs", "ticket.test-session.json")
guard_on = True


def guard_ticket(cwd, ts=None):
    top = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True, env=ENV).stdout.strip()
    os.makedirs(os.path.dirname(TICKET), mode=0o700, exist_ok=True)
    with open(TICKET, "w") as f:
        json.dump({"repo": top or cwd, "ts": time.time() if ts is None else ts}, f)
CANARY = "CANARY-OUTSIDE-SECRET-7f3a"
GH_TOKEN = "ghp_" + "Zx9Qw8Er7Ty6Ui5Op4As3Df2Gh1Jk0Lm9Nb8"
failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def run(script, *args, cwd=None):
    if "--work" in args and guard_on:
        guard_ticket(cwd or os.getcwd())
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *args], capture_output=True, text=True, env=ENV, cwd=cwd)


# A real run for the builds below: a build needs the target and facts the scripts wrote, from one run folder.
brepo = os.path.join(tmp, "brepo")
subprocess.run(["git", "init", "-q", "-b", "main", brepo], check=True, env=ENV)
open(os.path.join(brepo, "a.py"), "w").write("x = 1\n")
subprocess.run(["git", "-C", brepo, "add", "-A"], check=True, env=ENV)
subprocess.run(["git", "-C", brepo, "commit", "-qm", "base"], check=True, env=ENV)
open(os.path.join(brepo, "a.py"), "w").write("x = 2\n")
_rc = run("resolve_target.py", "working", "--no-fetch", "--work", cwd=brepo)
BWORK = json.loads(_rc.stdout)["work"]
subprocess.run([sys.executable, os.path.join(SCRIPTS, "gather_facts.py"), "--target", os.path.join(BWORK, "target.json"),
                "--out", os.path.join(BWORK, "facts.json")], check=True, capture_output=True, env=ENV, cwd=brepo)


def build(content, snippets, *extra):
    d = tempfile.mkdtemp(dir=OUT)
    for name in ("target.json", "facts.json"):
        shutil.copy(os.path.join(BWORK, name), os.path.join(d, name))
    cp, sp = os.path.join(d, "content.json"), os.path.join(d, "snippets.json")
    json.dump(content, open(cp, "w"))
    json.dump(snippets, open(sp, "w"))
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "build_page.py"), "--content", cp, "--target", os.path.join(d, "target.json"),
                           "--snippets", sp, "--facts", os.path.join(d, "facts.json"), "--date", "2026-11-02", *extra],
                          capture_output=True, text=True, env=ENV, cwd=brepo)


content = json.load(open(os.path.join(SKILL, "templates", "content.example.json"), encoding="utf-8"))
snippets = json.load(open(os.path.join(FIX, "snippets.json"), encoding="utf-8"))

print("== 1. Output paths ==")
home = os.path.join(tmp, "home")
os.makedirs(home)
rc = build(content, snippets, "--qa-out", os.path.join(home, ".zshrc"))
check("a For QA path outside the output folder is refused", rc.returncode != 0 and "writes only under" in rc.stderr
      and not os.path.exists(os.path.join(home, ".zshrc")), rc.stderr.strip()[-160:])
rc = build(content, snippets, "--out", os.path.join(home, "page.html"))
check("a page path outside the output folder is refused", rc.returncode != 0 and "writes only under" in rc.stderr
      and not os.path.exists(os.path.join(home, "page.html")), rc.stderr.strip()[-160:])
d = tempfile.mkdtemp(dir=OUT)
shutil.copy(os.path.join(BWORK, "target.json"), os.path.join(d, "target.json"))
json.dump(content, open(os.path.join(d, "content.json"), "w"))
json.dump(snippets, open(os.path.join(d, "snippets.json"), "w"))
rc = run("build_page.py", "--content", os.path.join(d, "content.json"), "--target", os.path.join(d, "target.json"),
         "--snippets", os.path.join(d, "snippets.json"), "--no-validate", cwd=brepo)
check("a build without --facts is refused (it would check neither the target nor the policy)",
      rc.returncode != 0 and "pass --facts" in rc.stderr and not [f for f in os.listdir(OUT) if f.endswith(".html")], rc.stderr.strip()[-160:])
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
check("the guard's note is used up by the run it started", not os.path.exists(TICKET))
if sys.platform == "darwin":
    # Hooks write under the per-user /var/folders/.../T/ when TMPDIR is unset; sandboxed Bash may see another TMPDIR.
    dut = safety._darwin_user_temp()
    check("the macOS per-user temp folder is found and searched", dut.startswith("/") and dut in safety.temp_bases(), dut)
guard_on = False
for why, make in (("no note from the guard", lambda: None),
                  ("a note for another repository", lambda: guard_ticket(brepo)),
                  ("a note older than ten minutes", lambda: guard_ticket(repo, time.time() - 900)),
                  ("a note dated in the future", lambda: guard_ticket(repo, time.time() + 3600))):
    if os.path.exists(TICKET):
        os.remove(TICKET)
    make()
    rc = run("resolve_target.py", "working", "--no-fetch", "--work", cwd=repo)
    check(f"--work refuses to start a run while the run guard is not active ({why})",
          rc.returncode != 0 and "run guard is not active" in rc.stdout, rc.stdout.strip()[-160:])
for name in ("EXPLAIN_DIFF_ANY_OUT", "EXPLAIN_DIFF_GUARD_CONSENT"):
    guard_ticket(repo)
    rc = subprocess.run([sys.executable, os.path.join(SCRIPTS, "resolve_target.py"), "working", "--no-fetch", "--work"],
                        capture_output=True, text=True, env=dict(ENV, **{name: "1"}), cwd=repo)
    check(f"--work refuses to start a run with {name} in its environment", rc.returncode != 0 and name in rc.stdout, rc.stdout.strip()[-160:])
guard_on = True
if os.path.exists(TICKET):
    os.remove(TICKET)
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

print("\n== 5. Run files and git ==")
# A forged target.json (the model, or anything else, rewrote it): git must never see an option in it.
pwned = os.path.join(tmp, "PWNED-output.txt")
good = json.load(open(target))
for desc, forge in (
        ("an option in diffArgs", lambda t: t.update(mode="commit", diffArgs=[t["base"]["sha"], t["base"]["sha"], "--output=" + pwned])),
        ("an option as a revision", lambda t: t.update(mode="commit", diffArgs=["--output=" + pwned, t["base"]["sha"]])),
        ("a short revision", lambda t: t.update(mode="commit", diffArgs=[t["base"]["sha"][:7], t["base"]["sha"]])),
        ("a base that is not the merge base", lambda t: t.update(mode="commit", diffArgs=[t["base"]["sha"], t["base"]["sha"]], mergeBase="f" * 40)),
        ("working mode with extra arguments", lambda t: t.update(diffArgs=["HEAD", "--output=" + pwned])),
        ("an option as the head commit", lambda t: t["head"].update(sha="--output=" + pwned)),
        ("an absolute file path", lambda t: t["files"].append({"path": "/etc/passwd"})),
        ("another repository", lambda t: t.update(repo=os.path.join(tmp, "outside"))),
        ("an option as the PR number", lambda t: t.update(pr={"number": "--web"})),
        ("an option as the default branch", lambda t: t.update(defaultBranch="--orphan=x")),
        ("commits git does not have and no files", lambda t: t.update(mode="commit", diffArgs=["e" * 40, "f" * 40], mergeBase="e" * 40,
                                                                      files=[], head=dict(t["head"], sha="f" * 40)))):
    forged = copy.deepcopy(good)
    forge(forged)
    fp = os.path.join(tempfile.mkdtemp(dir=OUT), "target.json")  # a target.json written by something other than the resolver
    json.dump(forged, open(fp, "w"))
    r1 = run("extract_snippets.py", "--target", fp, "--refs", refs, "--out", os.path.join(work, "forged-snippets.json"), cwd=repo)
    r2 = run("gather_facts.py", "--target", fp, "--out", os.path.join(work, "forged-facts.json"), cwd=repo)
    check(f"a target.json with {desc} is refused by extract_snippets and gather_facts",
          r1.returncode != 0 and r2.returncode != 0 and "resolve_target.py wrote" in r1.stderr + r2.stderr and not os.path.exists(pwned),
          (r1.stderr + r2.stderr).strip()[-160:])
# The file list must be git's: it decides which files get snippets and which count as policy changes.
dropped = copy.deepcopy(good)
dropped["files"] = dropped["files"][1:]
dp = os.path.join(tempfile.mkdtemp(dir=OUT), "target.json")
json.dump(dropped, open(dp, "w"))
r1 = run("extract_snippets.py", "--target", dp, "--list", cwd=repo)
r2 = run("gather_facts.py", "--target", dp, "--out", os.path.join(os.path.dirname(dp), "facts.json"), cwd=repo)
check("a target.json that leaves out a changed file is refused", good["files"] and r1.returncode != 0 and r2.returncode != 0
      and "files is not the list git gives" in r1.stderr + r2.stderr, (r1.stderr + r2.stderr).strip()[-160:])
# A user's diff.relative=true: run from a subfolder, the file list still has every file, from the repository root.
rrepo = os.path.join(tmp, "rrepo")
os.makedirs(os.path.join(rrepo, "sub"))
subprocess.run(["git", "init", "-q", "-b", "main", rrepo], check=True, env=ENV)
for name in ("b.py", "sub/a.py"):
    open(os.path.join(rrepo, name), "w").write("x = 1\n")
subprocess.run(["git", "-C", rrepo, "add", "-A"], check=True, env=ENV)
subprocess.run(["git", "-C", rrepo, "commit", "-qm", "base"], check=True, env=ENV)
subprocess.run(["git", "-C", rrepo, "config", "diff.relative", "true"], check=True, env=ENV)
for name in ("b.py", "sub/a.py"):
    open(os.path.join(rrepo, name), "w").write("x = 2\n")
rc = run("resolve_target.py", "working", "--no-fetch", "--work", cwd=os.path.join(rrepo, "sub"))
res = json.loads(rc.stdout) if rc.stdout.strip().startswith("{") else {}
rtarget = os.path.join(res.get("work") or tmp, "target.json")
rfiles = sorted(f["path"] for f in json.load(open(rtarget))["files"]) if os.path.exists(rtarget) else []
r1 = run("extract_snippets.py", "--target", rtarget, "--list", cwd=os.path.join(rrepo, "sub"))
check("with diff.relative=true, a run from a subfolder lists every file from the root", res.get("status") == "ok"
      and rfiles == ["b.py", "sub/a.py"] and r1.returncode == 0, (str(rfiles) + rc.stderr + r1.stderr).strip()[-200:])
# A change that edits Claude Code's session files, with that version in this checkout, gets a warning: Claude Code
# loaded them before the skill ran. Its own repository, so the shared one keeps its uncommitted change.
crepo = os.path.join(tmp, "crepo")
subprocess.run(["git", "init", "-q", "-b", "main", crepo], check=True, env=ENV)
open(os.path.join(crepo, "a.txt"), "w").write("a\n")
subprocess.run(["git", "-C", crepo, "add", "-A"], check=True, env=ENV)
subprocess.run(["git", "-C", crepo, "commit", "-qm", "base"], check=True, env=ENV)
os.makedirs(os.path.join(crepo, ".claude"))
open(os.path.join(crepo, ".claude", "settings.json"), "w").write('{"env": {"EXPLAIN_DIFF_ANY_OUT": "1"}}\n')
subprocess.run(["git", "-C", crepo, "add", "--", ".claude/settings.json"], check=True, env=ENV)
settings_warned = lambda rc: any(".claude/settings.json" in w and "checked out" in w for w in (json.loads(rc.stdout).get("warnings") or []))
rc = run("resolve_target.py", "working", "--no-fetch", cwd=crepo)
check("an uncommitted change to .claude/settings.json is warned about", settings_warned(rc), rc.stdout.strip()[-200:])
subprocess.run(["git", "-C", crepo, "commit", "-qm", "settings"], check=True, env=ENV)
open(os.path.join(crepo, "later.txt"), "w").write("x\n")
subprocess.run(["git", "-C", crepo, "add", "--", "later.txt"], check=True, env=ENV)
subprocess.run(["git", "-C", crepo, "commit", "-qm", "later"], check=True, env=ENV)
rc = run("resolve_target.py", "HEAD~1", "--no-fetch", cwd=crepo)
check("a settings change still in this checkout (a later commit on top) is warned about", settings_warned(rc), rc.stdout.strip()[-200:])
open(os.path.join(crepo, ".claude", "settings.json"), "w").write("{}\n")
rc = run("resolve_target.py", "HEAD~1", "--no-fetch", cwd=crepo)
check("no settings warning when this checkout does not have the change's version", not settings_warned(rc), rc.stdout.strip()[-200:])
# The change sets its own line endings for the file: the checkout's bytes differ from the commit's, but git says clean.
open(os.path.join(crepo, ".gitattributes"), "w").write(".claude/** text eol=crlf\n")
open(os.path.join(crepo, ".claude", "settings.json"), "w").write('{"hooks": {}}\n')
subprocess.run(["git", "-C", crepo, "add", "-A"], check=True, env=ENV)
subprocess.run(["git", "-C", crepo, "commit", "-qm", "crlf"], check=True, env=ENV)
os.remove(os.path.join(crepo, ".claude", "settings.json"))
subprocess.run(["git", "-C", crepo, "checkout", "--", ".claude/settings.json"], check=True, env=ENV)
rc = run("resolve_target.py", "HEAD", "--no-fetch", cwd=crepo)
check("a settings change checked out with the change's own eol attribute is still warned about",
      b"\r\n" in open(os.path.join(crepo, ".claude", "settings.json"), "rb").read() and settings_warned(rc), rc.stdout.strip()[-200:])
for name in ("claude.md", ".Claude/settings.local.json"):
    os.makedirs(os.path.join(crepo, os.path.dirname(name) or "."), exist_ok=True)
    open(os.path.join(crepo, name), "w").write("x\n")
    subprocess.run(["git", "-C", crepo, "add", "-f", "--", name], check=True, env=ENV)
    rc = run("resolve_target.py", "working", "--no-fetch", cwd=crepo)
    check(f"a checked-out change to {name} is warned about (names are matched without case)",
          any(name.lower() in w.lower() and "checked out" in w for w in (json.loads(rc.stdout).get("warnings") or [])), rc.stdout.strip()[-300:])
    subprocess.run(["git", "-C", crepo, "reset", "-q", "--", name], check=True, env=ENV)
# Files named like options really in the change (added to the index), and legal but unusual branch names.
odd = ["-x.txt", "--output=PWNED-name"]
for name in odd:
    open(os.path.join(repo, name), "w").write("odd\n")
subprocess.run(["git", "-C", repo, "add", "--", *odd], check=True, env=ENV)
rc = run("resolve_target.py", "working", "--no-fetch", "--work", cwd=repo)
owork = (json.loads(rc.stdout) if rc.returncode == 0 else {}).get("work") or ""
op = os.path.join(owork, "target.json")
ok = json.load(open(op)) if owork else {}
ok.update(defaultBranch="rel+1/développement", remote="up-stream")
safety.write_private(op, json.dumps(ok), own=("target.json",))
r1 = run("extract_snippets.py", "--target", op, "--list", cwd=repo)
r2 = run("gather_facts.py", "--target", op, "--out", os.path.join(owork, "facts.json"), cwd=repo)
check("a legal branch name with + or accents, and files named -x.txt and --output=..., are accepted",
      r1.returncode == 0 and r2.returncode == 0 and set(odd) <= {f["path"] for f in ok.get("files", [])}, (rc.stderr + r1.stderr + r2.stderr).strip()[-160:])
check("a file path that looks like an option reaches git only after --", not os.path.exists(os.path.join(repo, "PWNED-name")))
subprocess.run(["git", "-C", repo, "rm", "-q", "--cached", "--", *odd], check=True, env=ENV)
for name in odd:
    os.remove(os.path.join(repo, name))

# A file the model may write (content.json) cannot stand in for a script's file, and no script overwrites another's.
cfake = os.path.join(work, "content.json")
shutil.copy(target, cfake)
r1 = run("gather_facts.py", "--target", cfake, "--out", os.path.join(work, "facts.json"), cwd=repo)
r2 = run("extract_snippets.py", "--target", cfake, "--list", cwd=repo)
check("a target passed under another name (a model-written content.json) is refused", r1.returncode != 0 and r2.returncode != 0
      and "is not a target.json" in r1.stderr + r2.stderr, (r1.stderr + r2.stderr).strip()[-160:])
for script, args in (("resolve_target.py", ["working", "--no-fetch", "--out", os.path.join(work, "facts.json")]),
                     ("gather_facts.py", ["--target", target, "--out", os.path.join(work, "target.json")]),
                     ("extract_snippets.py", ["--target", target, "--refs", refs, "--out", os.path.join(work, "content.json")]),
                     ("gather_facts.py", ["--target", target, "--out", os.path.join(work, "policy", "facts.json")])):
    before = open(args[-1]).read() if os.path.exists(args[-1]) else None
    r = run(script, *args, cwd=repo)
    after = open(args[-1]).read() if os.path.exists(args[-1]) else None
    check(f"{script} refuses to write {os.path.relpath(args[-1], work)}", r.returncode != 0 and before == after, r.stderr.strip()[-120:])
r = run("extract_snippets.py", "--target", target, "--diff", cwd=repo)
check("extract_snippets --diff prints the change", r.returncode == 0 and "+    return 2" in r.stdout, r.stderr.strip()[-160:])

r = run("resolve_target.py", "HEAD~0", "--no-fetch", "--base", "--output=" + pwned, cwd=repo)
check("resolve_target refuses a base that starts with '-'", r.returncode != 0 and not os.path.exists(pwned), r.stdout.strip()[-160:])

# The policy record in the attestation comes from git, not from facts.json.
prepo = os.path.join(tmp, "prepo")
subprocess.run(["git", "init", "-q", "-b", "main", prepo], check=True, env=ENV)
open(os.path.join(prepo, "REVIEW.md"), "w").write("# Review\n## Areas\n- billing: money moves\n")
open(os.path.join(prepo, "app.py"), "w").write("x = 1\n")
subprocess.run(["git", "-C", prepo, "add", "-A"], check=True, env=ENV)
subprocess.run(["git", "-C", prepo, "commit", "-qm", "base"], check=True, env=ENV)
open(os.path.join(prepo, "app.py"), "w").write("x = 2\n")
rc = run("resolve_target.py", "working", "--no-fetch", "--work", cwd=prepo)
pwork = (json.loads(rc.stdout) if rc.returncode == 0 else {}).get("work") or ""
ptarget, pfacts = os.path.join(pwork, "target.json"), os.path.join(pwork, "facts.json")
rc = run("gather_facts.py", "--target", ptarget, "--out", pfacts, cwd=prepo)
facts = json.load(open(pfacts)) if rc.returncode == 0 else {}
check("gather_facts records REVIEW.md at the base", [f["path"] for f in facts.get("policy", {}).get("files", [])] == ["REVIEW.md"], rc.stderr[-160:])


def build_with(f, cwd=prepo, folder=None):
    fp = os.path.join(folder or pwork, "facts.json")
    json.dump(f, open(fp, "w"))
    cp, sp = os.path.join(pwork, "content.json"), os.path.join(pwork, "snippets.json")
    json.dump(content, open(cp, "w"))
    json.dump(snippets, open(sp, "w"))
    return run("build_page.py", "--content", cp, "--target", ptarget, "--snippets", sp, "--facts", fp, "--no-validate",
               "--out", os.path.join(pwork, "page.html"), "--qa-out", os.path.join(pwork, "qa.md"), cwd=cwd)


rc = build_with(facts)
check("build with facts whose policy hashes match git", rc.returncode == 0, (rc.stdout + rc.stderr).strip()[-200:])
bad = copy.deepcopy(facts)
bad["policy"]["files"][0]["sha256"] = "0" * 64
rc = build_with(bad)
check("build refuses a facts.json whose policy hash is not git's", rc.returncode != 0 and "run gather_facts.py again" in rc.stderr, rc.stderr.strip()[-160:])
bad = copy.deepcopy(facts)
bad["policy"]["fromCommit"] = "f" * 40
rc = build_with(bad)
check("build refuses policy read from another commit than the base", rc.returncode != 0 and "not the target's base" in rc.stderr, rc.stderr.strip()[-160:])
rc = build_with(facts, folder=tempfile.mkdtemp(dir=OUT))
check("build refuses facts from another run folder", rc.returncode != 0 and "same run folder" in rc.stderr, rc.stderr.strip()[-160:])
d = tempfile.mkdtemp(dir=OUT)
for args in (["--out", os.path.join(pwork, "policy", "REVIEW.md.txt")], ["--qa-out", os.path.join(pwork, "target.json")]):
    cp, sp = os.path.join(pwork, "content.json"), os.path.join(pwork, "snippets.json")
    before = open(args[1]).read() if os.path.exists(args[1]) else None
    rc = run("build_page.py", "--content", cp, "--target", ptarget, "--snippets", sp, "--no-validate", *args, cwd=prepo)
    after = open(args[1]).read() if os.path.exists(args[1]) else None
    check(f"build refuses to write over {os.path.relpath(args[1], pwork)}", rc.returncode != 0 and before == after, rc.stderr.strip()[-120:])
rc = build_with(facts, cwd=repo)
check("build with facts refuses to run outside the target's repository", rc.returncode != 0 and "repo is not" in rc.stderr, rc.stderr.strip()[-160:])

# git fetch in the resolver: a clone whose core.hooksPath is a tracked folder must not run the change's hooks.
origin, clone = os.path.join(tmp, "origin"), os.path.join(tmp, "clone")
subprocess.run(["git", "init", "-q", "-b", "main", origin], check=True, env=ENV)
open(os.path.join(origin, "a.txt"), "w").write("a\n")
subprocess.run(["git", "-C", origin, "add", "-A"], check=True, env=ENV)
subprocess.run(["git", "-C", origin, "commit", "-qm", "one"], check=True, env=ENV)
subprocess.run(["git", "clone", "-q", origin, clone], check=True, env=ENV)
subprocess.run(["git", "-C", clone, "switch", "-q", "-c", "feature"], check=True, env=ENV)
os.makedirs(os.path.join(clone, ".hooks"))
for hook in ("reference-transaction", "post-checkout", "post-index-change", "fsmonitor-watchman"):
    hp = os.path.join(clone, ".hooks", hook)
    open(hp, "w").write(f"#!/bin/sh\ntouch '{tmp}/FETCH-HOOK-RAN-{hook}'\n")
    os.chmod(hp, 0o755)
open(os.path.join(clone, "a.txt"), "w").write("b\n")
subprocess.run(["git", "-C", clone, "add", "-A"], check=True, env=ENV)
subprocess.run(["git", "-C", clone, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "two"], check=True, env=ENV)
subprocess.run(["git", "-C", clone, "config", "core.hooksPath", ".hooks"], check=True, env=ENV)
open(os.path.join(origin, "b.txt"), "w").write("new\n")
subprocess.run(["git", "-C", origin, "add", "-A"], check=True, env=ENV)
subprocess.run(["git", "-C", origin, "commit", "-qm", "three"], check=True, env=ENV)
rc = run("resolve_target.py", "feature", "--work", cwd=clone)
ran = sorted(f for f in os.listdir(tmp) if f.startswith("FETCH-HOOK-RAN-"))
fetched = subprocess.run(["git", "-C", clone, "-c", "core.hooksPath=/dev/null", "log", "-1", "--format=%s", "origin/main"],
                         capture_output=True, text=True).stdout.strip()
check("resolve_target fetches the remote", rc.returncode == 0 and fetched == "three", (rc.stdout + rc.stderr)[-200:])
check("git fetch in resolve_target runs none of the repository's hooks", not ran, str(ran))
cwork = (json.loads(rc.stdout) if rc.returncode == 0 else {}).get("work") or ""
if cwork:
    rc = run("gather_facts.py", "--target", os.path.join(cwork, "target.json"), "--out", os.path.join(cwork, "facts.json"), cwd=clone)
    json.dump({"s_a": "a.txt:1-1"}, open(os.path.join(cwork, "refs.json"), "w"))
    rc2 = run("extract_snippets.py", "--target", os.path.join(cwork, "target.json"), "--refs", os.path.join(cwork, "refs.json"),
              "--out", os.path.join(cwork, "snippets.json"), cwd=clone)
    ran = sorted(f for f in os.listdir(tmp) if f.startswith("FETCH-HOOK-RAN-"))
    check("gather_facts and extract_snippets run none of the repository's hooks", rc.returncode == 0 and rc2.returncode == 0 and not ran,
          str(ran) + (rc.stderr + rc2.stderr)[-160:])

os.environ.update(GH_REPO="someone/else", GH_FORCE_TTY="1", PAGER="sh -c x")
ge = safety.gh_env()
check("the scripts' gh calls run without GH_REPO, a forced terminal or a pager program",
      not {"GH_REPO", "GH_FORCE_TTY", "PAGER"} & set(ge) and ge.get("GH_PAGER") == "cat" and ge.get("PATH") == os.environ.get("PATH"))
for k in ("GH_REPO", "GH_FORCE_TTY", "PAGER"):
    del os.environ[k]

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{len(failures)} failed")
sys.exit(1 if failures else 0)
