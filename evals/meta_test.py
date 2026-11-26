#!/usr/bin/env python3
"""Checks that the repository's versions, pins and plugin metadata agree, so a release ships what was tested.

- One Playwright version everywhere: validate_page.py, the CI requirements (with hashes), and the
  run-offline-evals skill.
- The plugin version in plugin.json equals the scripts' version (safety.__version__), CHANGELOG.md has an
  entry for it, and the README's pinned install names its tag.
- A version the CHANGELOG marks released has a model-eval run record in evals/RESULTS.md that meets a bar.
- marketplace.json describes the plugin exactly as plugin.json does.
- Every GitHub Action in the workflows is pinned to a full commit SHA.
- The plugin's hooks.json parses and points at the run guard that exists.
- Every script runs git through safety.git(), which runs no hook, fsmonitor, external diff or textconv program.

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


def meets_row(block):
    """For each table row in `block` (not a header, which a | --- | line follows) whose last cell is Meets."""
    lines = block.splitlines()
    for i, line in enumerate(lines):
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.lstrip().startswith("|") else []
        header = i + 1 < len(lines) and re.match(r"^\s*\|[\s|:-]+\|?\s*$", lines[i + 1])
        if cells and cells[-1] == "Meets" and not header:
            yield True


def release_evidence(changelog, results, version):
    """None when `version` may ship: its CHANGELOG heading says (unreleased), or RESULTS.md has a model-eval run
    record for it ("## Run record (model evals, <version>, ...)") in which a model meets its bar. Else the reason."""
    head = re.search(rf"^## {re.escape(version)}\b(.*)$", changelog, re.M)
    if not head:
        return f"CHANGELOG.md has no heading for {version}"
    if "unreleased" in head.group(1).lower():
        return None
    for m in re.finditer(r"^## Run record \(model evals, ([^,)]+)[^\n]*\n(.*?)(?=^## |\Z)", results, re.M | re.S):
        if m.group(1).strip() == version and any(meets_row(m.group(2))):
            return None
    return f"{version} is released in CHANGELOG.md, but evals/RESULTS.md has no model-eval run record for it that meets a bar"


released_cl = "## 9.9.9\n\nThings.\n"
good_results = "## Run record (model evals, 9.9.9, Dec 1)\n\n| Model | Result |\n| --- | --- |\n| Opus | 24 of 26 | Meets |\n"
check("release check: a released version with no run record fails", release_evidence(released_cl, "## Pending\n", "9.9.9") is not None)
check("release check: a run record for another version does not count",
      release_evidence(released_cl, good_results.replace("9.9.9", "9.9.8"), "9.9.9") is not None)
check("release check: a run record that meets a bar passes", release_evidence(released_cl, good_results, "9.9.9") is None)
check("release check: a header column named Meets does not count",
      release_evidence(released_cl, "## Run record (model evals, 9.9.9, Dec 1)\n\n| Model | Meets |\n| --- | --- |\n| Opus | Misses |\n", "9.9.9") is not None)
check("release check: an unreleased version needs no record", release_evidence("## 9.9.9 (unreleased)\n", "", "9.9.9") is None)
why = release_evidence(read("CHANGELOG.md"), read("evals", "RESULTS.md"), plugin["version"])
check("a released version has a model-eval run record that meets the bar (evals/RESULTS.md)", why is None, why or "")

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
check("hooks.json runs the run guard on PreToolUse, UserPromptSubmit and every SessionStart (clear ends a run, resume re-arms one)",
      set(hooks["hooks"]) == {"PreToolUse", "UserPromptSubmit", "SessionStart"}
      and [m.get("matcher") for m in hooks["hooks"]["SessionStart"]] == [None]
      and paths and all(p.endswith("run_guard.py") and os.path.isfile(p) for p in paths), str(paths))

raw = []
for name in sorted(os.listdir(SCRIPTS)):
    if name.endswith(".py") and name != "safety.py":
        src = open(os.path.join(SCRIPTS, name), encoding="utf-8").read()
        raw += [f"{name}: {m.group(0)}" for m in re.finditer(r"""subprocess\.\w+\(\s*[\[(]\s*["']git["']""", src)]
        raw += [f"{name}: {m.group(0)}" for m in re.finditer(r"""\brun\(\s*["']git["'].*fetch""", src) if "safety.git" not in src]
check("every script runs git through safety.git() (no hooks run)", not raw, "; ".join(raw))

print(f"\n{len(failures)} failed")
sys.exit(1 if failures else 0)
