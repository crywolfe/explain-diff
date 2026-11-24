#!/usr/bin/env python3
"""Checks that the repository's versions, pins and plugin metadata agree, so a release ships what was tested.

- One Playwright version everywhere: validate_page.py, the CI requirements (with hashes), and the
  run-offline-evals skill.
- The plugin version in plugin.json equals the scripts' version (safety.__version__), CHANGELOG.md has an
  entry for it, and the README's pinned install names its tag.
- marketplace.json describes the plugin exactly as plugin.json does.
- Every GitHub Action in the workflows is pinned to a full commit SHA.
- The plugin's hooks.json parses and points at the run guard that exists.

Prints a last line with the failure count.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugins", "explain-diff")
SCRIPTS = os.path.join(PLUGIN, "skills", "explain-diff", "scripts")
sys.path.insert(0, SCRIPTS)
import safety  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


pw_validator = re.search(r'PLAYWRIGHT = "playwright==([\d.]+)"', read(SCRIPTS, "validate_page.py")).group(1)
pw_ci = re.search(r"^playwright==([\d.]+)", read("evals", "requirements-ci.txt"), re.M).group(1)
pw_skill = set(re.findall(r"playwright==([\d.]+)", read(".claude", "skills", "run-offline-evals", "SKILL.md")))
check("one Playwright version: validator, CI and the offline-evals skill", pw_validator == pw_ci and pw_skill == {pw_validator},
      f"validator {pw_validator}, CI {pw_ci}, skill {sorted(pw_skill)}")
req = read("evals", "requirements-ci.txt")
pkgs = re.findall(r"^([A-Za-z0-9_.-]+)==", req, re.M)
check("every CI requirement carries hashes", pkgs and all(re.search(rf"^{re.escape(p)}==[^\n]*\\\n\s+--hash=sha256:", req, re.M) for p in pkgs), str(pkgs))

plugin = json.loads(read(PLUGIN, ".claude-plugin", "plugin.json"))
market = json.loads(read(".claude-plugin", "marketplace.json"))
entry = next((p for p in market["plugins"] if p["name"] == plugin["name"]), {})
check("plugin.json version equals the scripts' version", plugin["version"] == safety.__version__, f"{plugin['version']} vs {safety.__version__}")
check("marketplace.json describes the plugin as plugin.json does", entry.get("description") == plugin["description"], "")
check("CHANGELOG.md has an entry for this version", re.search(rf"^## {re.escape(plugin['version'])}\b", read("CHANGELOG.md"), re.M), plugin["version"])
check("the README's pinned install names this version's tag", f"#v{plugin['version']}" in read("README.md"), f"#v{plugin['version']}")

wf_dir = os.path.join(ROOT, ".github", "workflows")
loose = []
for name in sorted(os.listdir(wf_dir)) + ["../../docs/gate/explain-diff-gate.yml"]:
    path = os.path.join(wf_dir, name)
    if not os.path.exists(path):
        continue
    for m in re.finditer(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", open(path, encoding="utf-8").read(), re.M):
        ref = m.group(1)
        if not ref.startswith("./") and not re.search(r"@[0-9a-f]{40}$", ref):
            loose.append(f"{name}: {ref}")
check("every action is pinned to a commit SHA", not loose, "; ".join(loose))

hooks = json.loads(read(PLUGIN, "hooks", "hooks.json"))
cmds = [h["command"] for ev in hooks["hooks"].values() for grp in ev for h in grp["hooks"]]
paths = [c.split('"')[1].replace("${CLAUDE_PLUGIN_ROOT}", PLUGIN) for c in cmds if '"' in c]
check("hooks.json runs the run guard on PreToolUse and UserPromptSubmit", set(hooks["hooks"]) == {"PreToolUse", "UserPromptSubmit"}
      and paths and all(p.endswith("run_guard.py") and os.path.isfile(p) for p in paths), str(paths))

print(f"\n{len(failures)} failed")
sys.exit(1 if failures else 0)
