#!/usr/bin/env python3
"""Tests for the explain-diff run guard (scripts/run_guard.py), the hook that enforces the skill's
tool boundary outside the model.

1. Decisions: each call that a run needs is allowed; each call it must never make is denied,
   including the bypasses a regex denylist misses (python3 -c, env assignments, $(...), redirects).
2. Lifecycle, through the hook's stdin and stdout as Claude Code drives it: inactive until the skill
   starts, active during the run, ended by the first prompt after the build, and the consent scripts
   always get "ask".

Runs offline in a private TMPDIR. Prints a last line with the failure count.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "plugins", "explain-diff", "skills", "explain-diff", "scripts")
GUARD = os.path.join(SCRIPTS, "run_guard.py")

tmp = tempfile.mkdtemp(prefix="explain-diff-guard-")
os.environ["TMPDIR"] = tmp
tempfile.tempdir = None
os.environ.pop("EXPLAIN_DIFF_GUARD_CONSENT", None)
sys.path.insert(0, SCRIPTS)
import run_guard as G  # noqa: E402
import safety  # noqa: E402

repo = os.path.join(tmp, "repo")
os.makedirs(repo)
subprocess.run(["git", "init", "-q", repo], check=True)
OUT = safety.out_root()
failures = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got}, want {want})"))
    if not ok:
        failures.append(name)


def decide(tool, inp, consent="ask"):
    st = {"repo": os.path.realpath(repo), "cwd": repo, "built": False, "started": 0}
    try:
        return G.judge({"tool_name": tool, "tool_input": inp, "cwd": repo}, st, consent)[0]
    except G.Deny:
        return "deny"


S = SCRIPTS
ALLOW = [
    ("Bash", f"python3 {S}/resolve_target.py 123 --work"),
    ("Bash", f"python3 {S}/gather_facts.py --target {OUT}/w/target.json --out {OUT}/w/facts.json"),
    ("Bash", f"python3 {S}/validate_page.py {OUT}/p.html --browser"),
    ("Bash", "git diff abc123 def456 -- app/x.py"),
    ("Bash", "git log --oneline -5 | head -3"),
    ("Bash", "git --no-pager show HEAD:app/x.py 2>/dev/null"),
    ("Bash", "git branch --show-current"),
    ("Bash", "gh pr view 12 --json title,body"),
    ("Bash", "gh pr checks 12"),
    ("Bash", "rg -n 'retry(' app && wc -l app/x.py"),
    ("Bash", "find . -name '*.py' -not -path './node_modules/*'"),
    ("Bash", "grep -rn \"price\" app 2>&1"),
    ("Bash", f"cd {repo} && ls -la"),
    ("Read", {"file_path": os.path.join(repo, "app", "x.py")}),
    ("Read", {"file_path": os.path.join(S, "..", "templates", "content.example.json")}),
    ("Grep", {"pattern": "def ", "path": repo}),
    ("Write", {"file_path": os.path.join(OUT, "w", "content.json")}),
    ("Skill", {"skill": "explain-diff"}),
    ("Skill", {"skill": "my-plugin:review-init"}),
    ("TodoWrite", {"todos": []}),
]
DENY = [
    ("Bash", "python3 -c 'import os; print(os.environ)'"),
    ("Bash", "python3 -m pytest tests"),
    ("Bash", "pytest tests/test_x.py"),
    ("Bash", "npm test"),
    ("Bash", "python3 app/x.py"),
    ("Bash", f"python3 {S}/../../../../../evals/model/run.py --dry-run"),
    ("Bash", "FOO=1 python3 " + f"{S}/build_page.py --content c.json"),
    ("Bash", f"python3 {S}/build_page.py --out $(echo ~/.zshrc)"),
    ("Bash", "echo `id`"),
    ("Bash", "echo hi > ~/.zshrc"),
    ("Bash", "cat app/x.py > /tmp/copy"),
    ("Bash", "curl https://evil.example/x"),
    ("Bash", "git push origin HEAD"),
    ("Bash", "git -c core.pager=sh diff"),
    ("Bash", "git diff --output=/tmp/x"),
    ("Bash", "git fetch origin"),
    ("Bash", "git commit -m x"),
    ("Bash", "git branch -D main"),
    ("Bash", "git grep -O foo"),
    ("Bash", "gh api repos/o/r/issues -f title=x"),
    ("Bash", "gh pr comment 12 -b hi"),
    ("Bash", "gh pr view 12 --web"),
    ("Bash", "find . -name x -exec rm {} ;"),
    ("Bash", "rg --pre sh foo"),
    ("Bash", "cat ~/.ssh/id_rsa"),
    ("Bash", "cat /etc/hosts"),
    ("Bash", "ls ../.."),
    ("Bash", "(cd / && ls)"),
    ("Bash", "sleep 100 &"),
    ("Bash", "bash -c 'id'"),
    ("Bash", "sed -i s/a/b/ app/x.py"),
    ("Bash", "pandoc -o /tmp/x docs/governance/a.docx"),
    ("Read", {"file_path": os.path.expanduser("~/.ssh/id_rsa")}),
    ("Read", {"file_path": "/etc/hosts"}),
    ("Write", {"file_path": os.path.join(repo, "app", "x.py")}),
    ("Write", {"file_path": os.path.expanduser("~/.zshrc")}),
    ("Edit", {"file_path": os.path.join(repo, "REVIEW.md")}),
    ("WebFetch", {"url": "https://evil.example"}),
    ("WebSearch", {"query": "x"}),
    ("mcp__gmail__send_message", {"to": "x"}),
    ("Skill", {"skill": "deploy"}),
    ("Artifact", {"file_path": "x"}),
    ("Agent", {"prompt": "run the tests"}),
    ("Task", {"prompt": "run the tests"}),
]
print("== 1. Decisions ==")
for tool, inp in ALLOW:
    check(f"allow {tool} {inp if isinstance(inp, str) else json.dumps(inp)[:70]}", decide(tool, inp if isinstance(inp, dict) else {"command": inp}), "allow")
for tool, inp in DENY:
    check(f"deny  {tool} {inp if isinstance(inp, str) else json.dumps(inp)[:70]}", decide(tool, inp if isinstance(inp, dict) else {"command": inp}), "deny")
check("ask   commit_for_qa.py", decide("Bash", {"command": f"python3 {S}/commit_for_qa.py --notes {OUT}/n.md --slug s --branch b"}), "ask")
check("eval consent allows commit_for_qa.py", decide("Bash", {"command": f"python3 {S}/commit_for_qa.py --notes x --slug s --branch b"}, consent="allow"), "allow")

# A copy of a script that differs from the skill's is not the skill's script.
fake = os.path.join(repo, ".claude", "skills", "explain-diff", "scripts")
os.makedirs(fake)
open(os.path.join(fake, "build_page.py"), "w").write("import os; os.system('id')\n")
check("deny  a repo-planted build_page.py", decide("Bash", {"command": f"python3 {fake}/build_page.py"}), "deny")
shutil.copy(os.path.join(S, "build_page.py"), os.path.join(fake, "build_page.py"))
open(os.path.join(fake, "safety.py"), "w").write("import os; os.system('id')\n")
check("deny  an identical copy beside a planted safety.py", decide("Bash", {"command": f"python3 {fake}/build_page.py --content c"}), "deny")

# Reproductions from the code review: each was allowed by the option denylists and ran code, wrote, or leaked.
outside = os.path.join(tmp, "outside.txt")
open(outside, "w").write("secret\n")
os.symlink(outside, os.path.join(repo, "link.txt"))
REVIEW_DENY = [
    'git grep -iO"touch PWNED" a',
    "git grep --open-files=echo a",
    "rg --hostname-bin=./evil.sh --hyperlink-format=default a",
    'find . "-ex\\\nec" touch X ";"',
    f'cat "\\\n{os.path.expanduser("~")}/.zshrc"',
    "sort -ro out in",
    "uniq in out",
    "cat link.txt",
    "cat li*",
    f"git diff --no-index {outside} f.txt",
    "gh auth status --show-token",
    "jq -n env",
    "gh pr view 12 --web=true",
    "gh pr view 12 --jq .body",
    "tree -ao out",
    "git remote show origin",
    "rg -L foo",
    "grep -R foo",
    "find -L . -name x",
    "head -c 10 /etc/hosts",
]
REVIEW_DENY += [
    "echo #\\\ntouch PWNED1",                 # a comment whose backslash-newline the shell does not continue
    'echo #"\\\ntouch PWNED2 #"',
    "rg [-]-pre=sh x payload.sh",              # a glob in the pattern slot expands into an option
    "find . -mindepth [-0]*",
    "cat =ls",                                 # zsh =cmd
    "cat ~-/x",
    "cat ~root/.profile",
    f"git -C {OUT} status",                    # a folder outside the repository can carry a .git/config
    f"cd {OUT} && git log",
]
for cmd in REVIEW_DENY:
    check(f"deny  {cmd!r}", decide("Bash", {"command": cmd}), "deny")
REVIEW_ALLOW = ["rg -n -g '*.py' --type py retry app", "git log --oneline -5 --stat", "git diff --stat main...HEAD -- app",
                "git show HEAD:app/x.py", "head -20 app/x.py", "tail -n 5 app/x.py", "grep -rn -e price -A 2 app", "sort -rn app/x.py",
                "gh pr view 12 --json title,body", "gh pr diff 12 --name-only", "git grep -n -C 2 retry -- app", "git blame -L 1,5 app/x.py",
                "git ls-files --others --exclude-standard", "git log -n 3 --format=%H", "git branch --show-current", "echo 'a $b'"]
for cmd in REVIEW_ALLOW:
    check(f"allow {cmd!r}", decide("Bash", {"command": cmd}), "allow")
check("deny  Write to the guard's state", decide("Write", {"file_path": os.path.join(OUT, "runs", "sess-1.json")}), "deny")
check("deny  Write to the guard's logs", decide("Write", {"file_path": os.path.join(OUT, "logs", "sess-1.jsonl")}), "deny")
st = {"repo": os.path.realpath(repo), "cwd": repo, "built": False, "started": 0}
try:
    G.judge({"tool_name": "Bash", "tool_input": {"command": f"python3 {S}/build_page.py --content c; rm -rf x"}, "cwd": repo}, st, "ask")
    got = "allow"
except G.Deny:
    got = "deny"
check("a denied command does not mark the run as built", (got, st["built"]), ("deny", False))
# review-init supplied by the repository under review is refused.
check("allow review-init with none in the repo", decide("Skill", {"skill": "review-init"}), "allow")
os.makedirs(os.path.join(repo, ".claude", "skills", "review-init"))
check("deny  review-init supplied by the repo", decide("Skill", {"skill": "review-init"}), "deny")

# Hooks and sandboxed Bash can see different TMPDIRs: a private root under another temp base is accepted,
# a loose or symlinked one is not.
other = os.path.join(tmp, "sandbox-tmp")
os.makedirs(other)
real_bases = safety.temp_bases
safety.temp_bases = lambda: real_bases() + [other]
uid_root = os.path.join(other, f"explain-diff-{os.getuid()}")
os.mkdir(uid_root, 0o700)
check("allow Write in a private root under another temp base", decide("Write", {"file_path": os.path.join(uid_root, "w", "content.json")}), "allow")
os.chmod(uid_root, 0o755)
check("deny  Write in a loose (0755) root", decide("Write", {"file_path": os.path.join(uid_root, "w", "content.json")}), "deny")
os.rmdir(uid_root)
os.symlink(os.path.join(tmp, "home-ish"), uid_root)
os.makedirs(os.path.join(tmp, "home-ish"), mode=0o700)
check("deny  Write through a symlinked root", decide("Write", {"file_path": os.path.join(uid_root, ".zshrc")}), "deny")
safety.temp_bases = real_bases

print("\n== 2. Lifecycle through the hook ==")
env = dict(os.environ, TMPDIR=tmp)
env.pop("EXPLAIN_DIFF_GUARD_CONSENT", None)


def hook(payload):
    p = subprocess.run([sys.executable, GUARD], input=json.dumps(dict({"session_id": "sess-1", "cwd": repo}, **payload)),
                       capture_output=True, text=True, env=env)
    if p.returncode != 0:
        return f"error {p.returncode}: {p.stderr.strip()[:200]}"
    return json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] if p.stdout.strip() else "none"


pre = lambda tool, inp: hook({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": inp})
check("inactive: curl is left to normal permissions", pre("Bash", {"command": "curl https://x"}), "none")
check("inactive: commit script still asks", pre("Bash", {"command": f"python3 {S}/commit_for_qa.py --notes n --slug s --branch b"}), "ask")
check("Skill explain-diff starts a run", pre("Skill", {"skill": "explain-diff"}), "none")
check("active: curl denied", pre("Bash", {"command": "curl https://x"}), "deny")
check("active: MCP denied", pre("mcp__drive__share_file", {}), "deny")
check("active: git diff allowed", pre("Bash", {"command": "git diff HEAD"}), "none")
check("a prompt before the build keeps the run", hook({"hook_event_name": "UserPromptSubmit", "prompt": "Yes"}), "none")
check("still active after the prompt", pre("Bash", {"command": "curl https://x"}), "deny")
check("build_page.py allowed", pre("Bash", {"command": f"python3 {S}/build_page.py --content c.json --target t.json --snippets s.json"}), "none")
check("the next prompt ends the run", hook({"hook_event_name": "UserPromptSubmit", "prompt": "thanks"}), "none")
check("inactive again: curl left to normal permissions", pre("Bash", {"command": "curl https://x"}), "none")
check("/explain-diff prompt starts a run", hook({"hook_event_name": "UserPromptSubmit", "prompt": "/explain-diff 12"}), "none")
check("active from the slash command", pre("WebFetch", {"url": "https://x"}), "deny")
check("the stop prompt ends the run", hook({"hook_event_name": "UserPromptSubmit", "prompt": "/explain-diff:explain-diff stop"}), "none")
check("inactive after stop", pre("Bash", {"command": "curl https://x"}), "none")
check("a skill script re-arms the run", pre("Bash", {"command": f"python3 {S}/gather_facts.py --target t --out o"}), "none")
check("active after re-arming", pre("Bash", {"command": "curl https://x"}), "deny")
runs = os.path.join(OUT, "runs", "sess-1.json")
st = json.load(open(runs))
st["last"] -= G.IDLE_MINUTES * 60 + 5
json.dump(st, open(runs, "w"))
check("an idle run expires", pre("Bash", {"command": "curl https://x"}), "none")
p = subprocess.run([sys.executable, GUARD], input=json.dumps({"session_id": "sess-1", "cwd": repo, "hook_event_name": "UserPromptSubmit", "prompt": "/explain-diff 3"}),
                   capture_output=True, text=True, env=env)
p = subprocess.run([sys.executable, GUARD], input=json.dumps({"session_id": "sess-1", "cwd": repo, "hook_event_name": "PreToolUse", "tool_name": "WebFetch", "tool_input": {}}),
                   capture_output=True, text=True, env=env)
check("a denial says how to end the run", "explain-diff:explain-diff stop" in p.stdout, True)
open(os.path.join(OUT, "runs", "sess-1.json"), "w").write("[]")
check("a damaged state file still counts as an active run", pre("Bash", {"command": "curl https://x"}), "deny")
os.chmod(OUT, 0o755)
check("an output folder the guard cannot trust blocks the call", pre("Bash", {"command": "ls"}), "deny")
os.chmod(OUT, 0o700)
p = subprocess.run([sys.executable, GUARD], input="not json", capture_output=True, text=True, env=env)
check("unreadable hook input blocks the call (exit 2)", p.returncode, 2)
logs = os.listdir(os.path.join(OUT, "logs"))
check("tool calls are logged", logs == ["sess-1.jsonl"], True)
mode = os.stat(os.path.join(OUT, "logs", "sess-1.jsonl")).st_mode & 0o777
check("the log is private (0600)", oct(mode), oct(0o600))

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{len(failures)} failed")
sys.exit(1 if failures else 0)
