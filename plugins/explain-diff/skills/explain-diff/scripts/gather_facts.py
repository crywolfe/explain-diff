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

REVIEW.md and the governance documents are read from the BASE commit, never the working
tree or the change, so a change cannot rewrite the rules it is reviewed by. Their text is
written to <folder of --out>/policy/ (.docx and .pdf converted to text, with size and time
limits) and listed under "policy" with each file's sha256. A change that touches a policy or
agent-instruction file (REVIEW.md, docs/governance/, CODEOWNERS, CLAUDE.md, AGENTS.md,
.claude/, SKILL.md, .mcp.json, prompt files) is ranked High and named in policy.changed.

Everything here is a hint for the model to confirm or override. A candidate test only
counts as coverage once the model has read it and seen it reach the changed lines.

Prints a short summary; the full data is in --out.
"""
import argparse
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from extract_snippets import file_rows, hunks  # noqa: E402
import safety  # noqa: E402

# Files that set the rules a review applies, or instruct an AI agent. Changing one changes the review
# itself, so it is High whatever else it looks like (it is checked before the documentation rule).
POLICY_RX = (r"(^|/)(REVIEW\.md|CODEOWNERS|CLAUDE\.md|AGENTS\.md|GEMINI\.md|SKILL\.md|\.mcp\.json|\.cursorrules|copilot-instructions\.md)$"
             r"|(^|/)docs/governance/|(^|/)\.claude/|(^|/)\.cursor/rules/|(^|/)\.github/instructions/|\.prompt(\.[a-z]+)?$")

# Path rules, in order; the first rule that matches a path gives its hint. Tests and docs come
# first, so tests/test_auth.py is test-only, not authentication.
PATH_RULES = [
    ("high", "policy or agent instructions", POLICY_RX),
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
    p = safety.git(*args)
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
                try:  # confined to the repository; a symlink gives its target path, never the file it points to
                    self.cache[path] = safety.read_in_repo(".", path).split("\n")
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
    out = git("log", "--no-merges", "-50", "--format=%h%x1f%s%x1f%b%x1e", "--end-of-options", f"{a}..{rest[-1]}")
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
        p = subprocess.run(["gh", "pr", "view", str(pr["number"]), "--json", "body,labels"], capture_output=True, text=True, timeout=30,
                           env=safety.gh_env())
        d = json.loads(p.stdout) if p.returncode == 0 and p.stdout else {}
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        d = {}
    return {"number": pr["number"], "title": pr.get("title"), "body": (d.get("body") or "")[:4000],
            "labels": [x.get("name") for x in d.get("labels") or []]}


MAX_DOC_BYTES = 20 * 1024 * 1024   # a policy file larger than this is not read
MAX_XML_BYTES = 10 * 1024 * 1024   # word/document.xml inside a .docx, uncompressed
MAX_TEXT = 300_000                 # characters of extracted text kept
PDF_TIMEOUT = 30


def blob(rev, path):
    """(bytes, error) for path at rev, refusing anything that is not a blob or is too large."""
    kind = safety.git("cat-file", "-t", f"{rev}:{path}")
    if kind.returncode != 0:
        return None, "missing"
    if kind.stdout.strip() != "blob":
        return None, "not a file"
    size = safety.git("cat-file", "-s", f"{rev}:{path}").stdout.strip()
    if not size.isdigit() or int(size) > MAX_DOC_BYTES:
        return None, f"larger than {MAX_DOC_BYTES // (1024 * 1024)} MB"
    p = safety.git("cat-file", "blob", f"{rev}:{path}", binary=True)
    return (p.stdout, None) if p.returncode == 0 else (None, "unreadable")


def docx_text(data):
    """Paragraph text of a .docx, without running anything; refuses an oversized or malformed archive."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_XML_BYTES:
                return None, "word/document.xml is too large"
            with z.open(info) as f:
                xml = f.read(MAX_XML_BYTES + 1).decode("utf-8", "replace")
    except (KeyError, zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError) as e:
        return None, f"not a readable Word file ({type(e).__name__})"
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml)), None


def pdf_text(path):
    tool = shutil.which("pdftotext")
    if not tool:
        return None, "pdftotext is not installed: read the copy with the Read tool (pages)"
    try:
        p = subprocess.run([tool, "-layout", "-enc", "UTF-8", path, "-"], capture_output=True, timeout=PDF_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, f"pdftotext took longer than {PDF_TIMEOUT} s"
    text = p.stdout.decode("utf-8", "replace") if p.returncode == 0 else ""
    return (text, None) if text.strip() else (None, "no text layer (scanned, encrypted, or corrupt)")


def documents(target, work):
    """REVIEW.md and the governance documents as they are at the base commit, copied into work/policy/."""
    rev = (target.get("base") or {}).get("sha")
    out = {"reviewMd": None, "governance": [], "governanceOutside": [],
           "policy": {"fromCommit": rev, "fromRef": (target.get("base") or {}).get("ref"), "folder": None, "files": [],
                      "changed": [f["path"] for f in target["files"] if re.search(POLICY_RX, f["path"], re.I)
                                  or (f.get("oldPath") and re.search(POLICY_RX, f["oldPath"], re.I))]}}
    if not rev:
        return out
    folder = os.path.join(work, "policy")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    out["policy"]["folder"] = folder
    out["reviewMd"] = next((p for p in ("REVIEW.md", ".github/REVIEW.md", "docs/REVIEW.md") if blob(rev, p)[1] != "missing"), None)
    listing = safety.git("ls-tree", "-z", rev, "docs/governance/").stdout
    for entry in listing.split("\0"):
        if "\t" not in entry:
            continue
        meta, path = entry.split("\t", 1)
        if meta.split()[1] == "blob" and path.lower().endswith((".md", ".pdf", ".docx")):
            out["governance"].append(path)
    out["governance"].sort()
    out["governanceOutside"] = [p for p in ("GOVERNANCE.md", "docs/GOVERNANCE.md", ".github/GOVERNANCE.md") if blob(rev, p)[1] != "missing"]
    for path in ([out["reviewMd"]] if out["reviewMd"] else []) + out["governance"]:
        fmt = path.rsplit(".", 1)[-1].lower()
        rec = {"path": path, "format": fmt, "sha256": None, "text": None, "copy": None, "readable": False, "note": None}
        data, err = blob(rev, path)
        if data is None:
            rec["note"] = err
            out["policy"]["files"].append(rec)
            continue
        rec["sha256"] = hashlib.sha256(data).hexdigest()
        flat = re.sub(r"[^A-Za-z0-9.]+", "_", path)
        if fmt == "md":
            text, err = data.decode("utf-8", "replace"), None
        elif fmt == "docx":
            text, err = docx_text(data)
        else:
            rec["copy"] = os.path.join(folder, flat)
            safety.write_private(rec["copy"], "", own=("policy",))  # created 0600, then filled with the bytes
            with open(rec["copy"], "wb") as f:
                f.write(data)
            text, err = pdf_text(rec["copy"])
        if text is not None:
            rec["text"] = safety.write_private(os.path.join(folder, flat + ".txt"), text[:MAX_TEXT], own=("policy",))
            rec["readable"] = True
            if len(text) > MAX_TEXT:
                err = f"text cut at {MAX_TEXT} characters"
        rec["note"] = err
        out["policy"]["files"].append(rec)
    return out


def gather(target, work):
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
            "commits": commits(target), "pr": pr_body(target), **documents(target, work)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--target", required=True, help="JSON written by resolve_target.py")
    ap.add_argument("--out", required=True, help="write the facts JSON here")
    a = ap.parse_args()
    target = safety.load_run_file(a.target, "target.json")
    if target.get("status") != "ok":
        sys.exit(f"target is not resolved: {target.get('summary')}")
    safety.check_target(target)  # before any value of it reaches git
    out = safety.check_out(a.out, own=("facts.json",))
    os.chdir(target["repo"])
    facts = gather(target, os.path.dirname(out))
    safety.write_private(out, json.dumps(facts, indent=1, ensure_ascii=False), own=("facts.json",))
    c = facts["counts"]
    print(f"risk tier: {facts['tier']} ({', '.join(facts['tierReasons'][:4]) or 'no specific signal'}); "
          f"hunks: {c['high']} high, {c['medium']} medium, {c['low']} low; {'large change' if facts['large'] else 'small change'}")
    print(f"commits: {len(facts['commits'])}; PR: {'#' + str(facts['pr']['number']) if facts['pr'] else 'none'}; "
          f"REVIEW.md: {facts['reviewMd'] or 'none'}; governance documents: {len(facts['governance'])}"
          + (f"; outside docs/governance/: {', '.join(facts['governanceOutside'])}" if facts["governanceOutside"] else ""))
    pol = facts["policy"]
    print(f"policy: read from {pol['fromRef']} @{(pol['fromCommit'] or '')[:7]} into {pol['folder']}"
          + "".join(f"; {f['path']}: {f['note']}" for f in pol["files"] if f["note"]))
    if pol["changed"]:
        print(f"POLICY CHANGED by this change (ranked High; the rules above are the base version): {', '.join(pol['changed'])}")
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
