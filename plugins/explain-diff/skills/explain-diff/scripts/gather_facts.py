#!/usr/bin/env python3
"""Gather the facts explain-diff needs about a change in one pass, before any reading.

Usage:
  python3 gather_facts.py --target target.json --out facts.json

target.json is the output of resolve_target.py. For every changed hunk this lists:
  - a risk hint (high, medium, low) with reasons, from the file path and the changed lines,
    applying the skill's Where to look rules;
  - the functions and classes the hunk defines or sits inside;
  - candidate tests: test functions that mention those names;
  - its size and a location ready for extract_snippets.py refs.
It also lists the commits (subject and body), the PR description, REVIEW.md, and the
governance documents, and gives the change a risk tier: the highest hunk risk.

Everything here is a hint for the model to confirm or override. A candidate test only
counts as coverage once the model has read it and seen it reach the changed lines.

Prints a short summary; the full data is in --out.
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from extract_snippets import file_rows, hunks  # noqa: E402

# Path rules, in order; the first rule that matches a path gives its hint. Tests and docs come
# first, so tests/test_auth.py is test-only, not authentication.
PATH_RULES = [
    ("low", "test-only", r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$|(^|/)conftest\.py$"),
    ("low", "documentation", r"\.(md|rst|adoc)$|(^|/)(docs?|documentation)/|(^|/)(LICENSE|NOTICE|CHANGELOG|AUTHORS)[^/]*$"),
    ("high", "CI workflow", r"(^|/)(\.github/workflows|\.circleci|\.buildkite)/|(^|/)(\.gitlab-ci\.ya?ml|Jenkinsfile|azure-pipelines\.ya?ml)$"),
    ("high", "data migration", r"(^|/)(migrations?|alembic|db/migrate)/|\.sql$"),
    ("high", "deploy or infrastructure", r"(^|/)(Dockerfile[^/]*|Containerfile|(docker-)?compose[^/]*\.ya?ml|[^/]*\.tf|[^/]*\.tfvars|Chart\.yaml)$|(^|/)(k8s|kubernetes|helm|terraform|deploy|infra|ansible)/"),
    ("high", "installer", r"(^|/)(install[^/]*\.(sh|ps1|py)|setup\.py|bootstrap[^/]*\.sh)$"),
    ("medium", "dependency manifest", r"(^|/)(requirements[^/]*\.txt|pyproject\.toml|setup\.cfg|Pipfile(\.lock)?|poetry\.lock|uv\.lock|package(-lock)?\.json|yarn\.lock|pnpm-lock\.yaml|go\.(mod|sum)|Cargo\.(toml|lock)|Gemfile(\.lock)?|pom\.xml|build\.gradle)$"),
    ("medium", "build configuration", r"(^|/)(Makefile|Justfile|tox\.ini|noxfile\.py|\.pre-commit-config\.yaml)$"),
]
# Words in a path (split on / _ . -) that mark a sensitive area.
PATH_WORDS = [
    ("authentication or permissions", {"auth", "authn", "authz", "authentication", "authorization", "login", "logout", "oauth", "oidc",
                                       "saml", "sso", "token", "tokens", "session", "sessions", "permission", "permissions", "rbac", "acl",
                                       "role", "roles", "identity", "keycloak", "iam", "policy", "policies", "sudo", "access"}),
    ("secrets handling", {"secret", "secrets", "credential", "credentials", "vault", "kms", "crypto", "encryption", "keystore", "env"}),
    ("money or billing", {"billing", "payment", "payments", "invoice", "invoices", "pricing", "charge", "refund", "subscription", "ledger"}),
    ("prompts or model configuration", {"prompt", "prompts", "llm"}),
    ("caching", {"cache", "caching", "cached"}),
]
CODE_EXT = {"py", "js", "jsx", "ts", "tsx", "mjs", "cjs", "go", "rs", "rb", "java", "kt", "cs", "php", "swift", "scala", "c", "cc", "cpp", "h", "hpp", "sh", "bash"}
# Line rules on added and removed lines; they can raise a hint, never lower it.
LINE_RULES = [
    ("high", "secrets handling", r"(?i)(password|passwd|secret|api[_-]?key|private[_-]?key|client_secret|bearer|credentials?)\b"),
    ("high", "data deletion", r"(?i)\b(DROP\s+(TABLE|DATABASE|COLUMN)|DELETE\s+FROM|TRUNCATE\s+TABLE|rm\s+-rf|shutil\.rmtree|os\.remove|\.unlink\(|deleteMany|destroy\()"),
    ("high", "concurrency", r"\b(threading|Thread\(|Lock\(|RLock|Semaphore|asyncio\.(gather|Lock|create_task)|mutex|goroutine|go\s+func|atomic\.|sync\.Mutex|multiprocessing)\b"),
    ("high", "authentication or permissions", r"(?i)\b(authenticate|authorize|is_admin|permission|grant|revoke|scope|jwt|verify_signature|check_password|role)s?\b"),
    ("medium", "error handling", r"^\s*(except\b|catch\b|raise\b|throw\b|finally\b|rescue\b)|\bpanic\(|\bretry\b"),
    ("medium", "new import or dependency", r"^\s*(import\s+\w|from\s+\S+\s+import\b|require\(|use\s+\w+::)"),
]
DEF_RE = re.compile(r"^(?P<indent>\s*)(?:(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:def|class|function\*?|func(?:\s*\([^)]*\))?)\s+"
                    r"|(?:pub(?:\(crate\))?\s+)?fn\s+|(?:export\s+)?(?:const|let|var)\s+(?=\w+\s*=\s*(?:async\s*)?(?:\(|function)))(?P<name>[A-Za-z_]\w*)")
TEST_DEF_RE = re.compile(r"^\s*(?:async\s+def|def)\s+(test\w*)|^\s*(?:it|test|describe)\(\s*['\"`]([^'\"`]{1,80})|^func\s+(Test\w+)")
TEST_PATHS = [":(glob)**/test/**", ":(glob)**/tests/**", ":(glob)**/__tests__/**", ":(glob)**/spec/**", ":(glob)**/test_*",
              ":(glob)**/*_test.*", ":(glob)**/*.test.*", ":(glob)**/*.spec.*", ":(glob)**/conftest.py"]
COMMON = {"main", "init", "run", "get", "set", "new", "test", "setup", "update", "load", "save", "start", "stop", "call",
          "__init__", "__call__", "__repr__", "__str__", "self", "app", "data", "value", "name", "config"}
RANK = {"low": 0, "medium": 1, "high": 2}
LARGE_HUNK = 60


def git(*args, check=False):
    p = subprocess.run(("git",) + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise RuntimeError(f"git {args[0]}: {p.stderr.strip()[:200]}")
    return p.stdout if p.returncode == 0 else ""


def path_hint(path):
    for level, why, rx in PATH_RULES:
        if re.search(rx, path, re.I if level != "low" else 0):
            return level, why
    words = set(re.split(r"[/_.\-]+", path.lower()))
    for why, keys in PATH_WORDS:
        if words & keys:
            return "high", why
    return None, None


def is_code(path):
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() in CODE_EXT if "." in name else False


def searchable(name):
    """Names distinctive enough to search tests for: not a common word, and long or compound."""
    return name not in COMMON and (len(name) >= 6 or "_" in name.strip("_") or any(c.isupper() for c in name[1:]))


def raise_to(cur, level):
    return level if RANK[level] > RANK[cur] else cur


def symbols(rows, start, end):
    """Names defined on the hunk's changed lines, and the definitions its changed lines sit inside
    (a def line inside the hunk may itself be unchanged context)."""
    names = []
    for r in rows[start:end + 1]:
        if r["kind"] != "ctx" and DEF_RE.match(r["text"]):
            names.append(DEF_RE.match(r["text"]).group("name"))
    for i in range(start, end + 1):
        r = rows[i]
        if r["kind"] == "ctx" or not r["text"].strip() or (i > start and rows[i - 1]["kind"] != "ctx"):
            continue  # only the first line of each run of changed lines
        indent = len(r["text"]) - len(r["text"].lstrip())
        for j in range(i - 1, max(-1, i - 400), -1):
            if rows[j]["kind"] == "del" and r["kind"] != "del":
                continue
            m = DEF_RE.match(rows[j]["text"])
            if m and len(m.group("indent")) < indent:
                names.append(m.group("name"))
                break
    out = []
    for n in names:
        if n not in out and n not in COMMON and len(n) >= 4:
            out.append(n)
    return out[:6]


BODY_LINES = 40


class Tests:
    """Finds test functions that mention a name, at the head commit (or the working tree)."""

    def __init__(self, rev):
        self.rev, self.cache, self.bodies = rev, {}, {}

    def text(self, path):
        if path not in self.cache:
            if self.rev:
                self.cache[path] = git("show", f"{self.rev}:{path}").split("\n")
            else:
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        self.cache[path] = f.read().split("\n")
                except OSError:
                    self.cache[path] = []
        return self.cache[path]

    def find(self, name):
        args = ["grep", "-z", "-n", "-w", "-F", "-I", "-e", name]
        if self.rev:
            args.append(self.rev)
        out = git(*args, "--", *TEST_PATHS)
        found = []
        for line in out.splitlines():
            # -z: path, line and text are NUL-separated and paths are not quoted; git still joins "<rev>:" to the path
            if self.rev and line.startswith(self.rev + ":"):
                line = line[len(self.rev) + 1:]
            parts = line.split("\0", 2)
            if len(parts) < 3:
                continue
            path, num = parts[0], parts[1]
            if not num.isdigit():
                continue
            lines, n = self.text(path), int(num)
            test, start = None, 0
            for i in range(min(n, len(lines)) - 1, -1, -1):
                m = TEST_DEF_RE.match(lines[i])
                if m:
                    test, start = next(g for g in m.groups() if g), i
                    break
            if not test or any(t["file"] == path and t["test"] == test for t in found):
                continue
            body = self.body(path, test, lines, start)
            # "calls": the test calls the name, or uses the module by attribute; otherwise it only mentions it
            calls = bool(re.search(r"(?<![\w.])" + re.escape(name) + r"\s*[(.]|\." + re.escape(name) + r"\s*\(", body))
            found.append({"file": path, "line": n, "test": test, "mentions": name, "calls": calls})
        return found

    def body(self, path, test, lines, start):
        """The test function's source (up to BODY_LINES lines), stored once in self.bodies."""
        key = f"{path}::{test}"
        if key not in self.bodies:
            indent = len(lines[start]) - len(lines[start].lstrip())
            end = start + 1
            while end < len(lines) and (not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) > indent):
                end += 1
            src = lines[start:end]
            while src and not src[-1].strip():
                src.pop()
            self.bodies[key] = "\n".join(src[:BODY_LINES]) + (f"\n# ... {len(src) - BODY_LINES} more lines" if len(src) > BODY_LINES else "")
        return self.bodies[key]


def commits(target):
    mode, (a, *rest) = target["mode"], target["diffArgs"]
    if mode == "working" or not rest:
        return []
    out = git("log", "--no-merges", "-50", "--format=%h%x1f%s%x1f%b%x1e", f"{a}..{rest[-1]}")
    res = []
    for rec in out.split("\x1e"):
        f = rec.strip("\n").split("\x1f")
        if len(f) == 3:
            res.append({"sha": f[0], "subject": f[1], "body": f[2].strip()[:1500]})
    return res


def pr_body(target):
    pr = target.get("pr")
    if not pr:
        return None
    try:
        p = subprocess.run(["gh", "pr", "view", str(pr["number"]), "--json", "body,labels"], capture_output=True, text=True, timeout=30)
        d = json.loads(p.stdout) if p.returncode == 0 and p.stdout else {}
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        d = {}
    return {"number": pr["number"], "title": pr.get("title"), "body": (d.get("body") or "")[:4000],
            "labels": [x.get("name") for x in d.get("labels") or []]}


def documents():
    review = next((p for p in ("REVIEW.md", ".github/REVIEW.md", "docs/REVIEW.md") if os.path.isfile(p)), None)
    gov = []
    if os.path.isdir("docs/governance"):
        gov = sorted(f"docs/governance/{f}" for f in os.listdir("docs/governance")
                     if os.path.isfile(f"docs/governance/{f}") and f.lower().endswith((".md", ".pdf", ".docx")))
    outside = [p for p in ("GOVERNANCE.md", "docs/GOVERNANCE.md", ".github/GOVERNANCE.md") if os.path.isfile(p)]
    return {"reviewMd": review, "governance": gov, "governanceOutside": outside}


def gather(target):
    rev = target["head"].get("sha")
    tests = Tests(rev)
    test_files = {f["path"] for f in target["files"] if path_hint(f["path"])[1] == "test-only"}
    out_hunks, files_out = [], []
    for f in target["files"]:
        path = f["path"]
        plevel, pwhy = path_hint(path)
        entry = {"path": path, "status": f.get("status"), "additions": f.get("additions"), "deletions": f.get("deletions"),
                 "hint": plevel or "medium", "reasons": [pwhy] if pwhy else []}
        try:
            rows = file_rows(target, path)
        except Exception as e:  # binary, unreadable: the path hint is all there is
            entry["note"] = str(e)[:120]
            files_out.append(entry)
            continue
        for n, h in enumerate(hunks(rows), 1):
            level, reasons = plevel or "low", ([pwhy] if pwhy else [])
            changed = [r["text"] for r in rows[h["start"]:h["end"] + 1] if r["kind"] != "ctx"]
            manifest = pwhy == "dependency manifest"
            if plevel != "low" and not manifest:  # tests, docs and dependency lists: the path hint stands
                for lv, why, rx in LINE_RULES:
                    if why in ("error handling", "new import or dependency") and not is_code(path):
                        continue
                    if any(re.search(rx, t) for t in changed):
                        level = raise_to(level, lv)
                        if why not in reasons:
                            reasons.append(why)
                size = h["additions"] + h["deletions"]
                kind = "core logic" if is_code(path) else "configuration"
                if size >= LARGE_HUNK and plevel is None and is_code(path):
                    level = raise_to(level, "medium")
                    reasons.append(f"large hunk ({size} lines)")
                if plevel is None and level == "low":
                    level = "medium" if size >= 20 else "low"
                    reasons.append(kind if size >= 20 else "small change")
            names = symbols(rows, h["start"], h["end"]) if plevel != "low" and is_code(path) else []
            cands = []
            search = [x for x in names if searchable(x)]
            stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            if is_code(path) and plevel != "low" and stem not in COMMON and len(stem) >= 4:
                search.append(stem)  # tests that import or name the module, when the names find nothing
            for name in search:
                if name == stem and cands:
                    break
                for t in tests.find(name):
                    if not any(c["file"] == t["file"] and c["test"] == t["test"] for c in cands):
                        cands.append(t)
            loc = f"{path}:{'old:' if h['old'] else ''}{h['a']}-{h['b']}"
            cands.sort(key=lambda t: not t["calls"])  # tests that call the change first
            out_hunks.append({"loc": loc, "file": path, "n": n, "hint": level, "reasons": reasons, "symbols": names,
                              "additions": h["additions"], "deletions": h["deletions"], "first": h["first"][:90],
                              "candidateTests": cands[:8], "candidateTestsMore": max(0, len(cands) - 8),
                              "testChangedHere": any(t["file"] in test_files for t in cands)})
            entry["hint"] = level if n == 1 else max(entry["hint"], level, key=RANK.get)
        files_out.append(entry)
    tier = max((h["hint"] for h in out_hunks), key=RANK.get, default="low")
    total = (target.get("additions") or 0) + (target.get("deletions") or 0)
    large = len(target["files"]) > 15 or total > 800
    counts = {lv: sum(1 for h in out_hunks if h["hint"] == lv) for lv in ("high", "medium", "low")}
    return {"tier": tier, "tierReasons": sorted({r for h in out_hunks if h["hint"] == tier for r in h["reasons"]}),
            "large": large, "counts": counts, "hunks": sorted(out_hunks, key=lambda h: (-RANK[h["hint"]], -(h["additions"] + h["deletions"]))),
            "files": files_out,
            "testBodies": {k: v for k, v in tests.bodies.items() if any(f"{t['file']}::{t['test']}" == k for h in out_hunks for t in h["candidateTests"])},
            "commits": commits(target), "pr": pr_body(target), **documents()}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--target", required=True, help="JSON written by resolve_target.py")
    ap.add_argument("--out", required=True, help="write the facts JSON here")
    a = ap.parse_args()
    target = json.load(open(a.target, encoding="utf-8"))
    if target.get("status") != "ok":
        sys.exit(f"target is not resolved: {target.get('summary')}")
    os.chdir(target.get("repo", "."))
    facts = gather(target)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(facts, f, indent=1, ensure_ascii=False)
    c = facts["counts"]
    print(f"risk tier: {facts['tier']} ({', '.join(facts['tierReasons'][:4]) or 'no specific signal'}); "
          f"hunks: {c['high']} high, {c['medium']} medium, {c['low']} low; {'large change' if facts['large'] else 'small change'}")
    print(f"commits: {len(facts['commits'])}; PR: {'#' + str(facts['pr']['number']) if facts['pr'] else 'none'}; "
          f"REVIEW.md: {facts['reviewMd'] or 'none'}; governance documents: {len(facts['governance'])}"
          + (f"; outside docs/governance/: {', '.join(facts['governanceOutside'])}" if facts["governanceOutside"] else ""))
    by_file = {}
    for h in facts["hunks"]:
        by_file.setdefault(h["file"], []).append(h)
    rows = []
    for path, hs in by_file.items():
        hint = max((h["hint"] for h in hs), key=RANK.get)
        reasons = list(dict.fromkeys(r for h in hs if h["hint"] == hint for r in h["reasons"]))
        syms = list(dict.fromkeys(x for h in hs for x in h["symbols"]))
        tests = list(dict.fromkeys(f"{t['file'].rsplit('/', 1)[-1]}::{t['test']}" for h in hs for t in h["candidateTests"]))
        size = sum(h["additions"] + h["deletions"] for h in hs)
        rows.append((RANK[hint], size, path, hint, hs, reasons, syms, tests))
    rows.sort(key=lambda r: (-r[0], -r[1]))
    low = [r[2] for r in rows if r[3] == "low"]
    for _, size, path, hint, hs, reasons, syms, tests in rows:
        if hint == "low":
            continue
        t = ", ".join(tests[:3]) + (f" +{len(tests) - 3}" if len(tests) > 3 else "")
        print(f"{hint:6}  {path}  {len(hs)} hunk(s), {size} lines  {'; '.join(reasons[:3])}  [{', '.join(syms[:3]) or '-'}]  tests: {t or 'none found'}")
    if low:
        print(f"low     {len(low)} file(s): {', '.join(low)[:500]}")
    print(f"facts: {a.out}")


if __name__ == "__main__":
    main()
