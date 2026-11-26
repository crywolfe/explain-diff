#!/usr/bin/env python3
"""File and data safety rules shared by the explain-diff scripts.

- Outputs: every file a script writes goes under one private per-user folder,
  out_root() (<temp dir>/explain-diff-<uid>, mode 0700). A path outside it is refused
  unless EXPLAIN_DIFF_ANY_OUT=1 is set, which only skill maintenance (the test suites,
  rebuilding the example page) does. Files are written 0600 through a fresh temp file
  and os.replace, so a planted symlink is replaced, never followed. An existing path
  that is not a regular file is refused.
- Reads: a working-tree file is read only when it is inside the repository; a symlink
  gives its target path, as `git show` does, never the file it points to.
- Masking: known token formats, assigned secrets, high-entropy strings and personal
  email addresses are masked in text that goes on the page or into the For QA file.
- Git: every script runs git through git(), which runs no hook, fsmonitor, external diff
  or textconv program, whatever the repository's config or a tracked hooks folder says.
- Run files: target.json is checked with check_target() before its values reach git, so
  a forged or damaged file cannot pass an option (such as --output=<path>) as a revision.
"""
import datetime
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import time

__version__ = "1.5.0"
OVERRIDE = "EXPLAIN_DIFF_ANY_OUT"
ALLOW_GUARD_FILES = False  # set by run_guard.py; every other script is refused runs/ and logs/


def _uid():
    return os.getuid() if hasattr(os, "getuid") else None


def out_root():
    """The private output folder, created 0700 on first use. Refuses one that is not ours or not private."""
    uid = _uid()
    root = os.path.join(tempfile.gettempdir(), f"explain-diff-{uid if uid is not None else 'user'}")
    try:
        os.mkdir(root, 0o700)
    except FileExistsError:
        pass
    st = os.lstat(root)
    if not stat.S_ISDIR(st.st_mode) or (uid is not None and (st.st_uid != uid or st.st_mode & 0o077)):
        raise SystemExit(f"{root} is not a private folder owned by you (a real directory, mode 0700); remove it and run again")
    return os.path.realpath(root)


def temp_bases():
    """Temp folders a run's scripts may resolve to. Hooks and sandboxed Bash commands can see different TMPDIR
    values (Claude Code's sandbox sets its own), so the run guard accepts a root under any of these."""
    uid = _uid()
    bases = [tempfile.gettempdir(), os.environ.get("TMPDIR") or "", "/tmp", "/private/tmp", "/var/tmp",
             f"/tmp/claude-{uid}", f"/private/tmp/claude-{uid}", "/tmp/claude"]
    bases.append(_darwin_user_temp())  # macOS per-user temp, whatever TMPDIR says
    return [b for b in bases if b]


def _darwin_user_temp():
    """macOS's per-user temp folder (/var/folders/.../T/), where an unsandboxed hook writes when TMPDIR is unset.
    Python has no name for _CS_DARWIN_USER_TEMP_DIR, so ask by number (65537), then getconf."""
    if sys.platform != "darwin":
        return ""
    try:
        return os.confstr(65537) or ""
    except (ValueError, OSError):
        pass
    try:
        p = subprocess.run(["/usr/bin/getconf", "DARWIN_USER_TEMP_DIR"], capture_output=True, text=True, timeout=5)
        return p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def known_roots():
    """Every existing private output folder of this user under temp_bases(), this process's own first.
    Each must be a real directory (not a symlink), owned by the user, with no group or other bits. Creates nothing."""
    uid = _uid()
    found = [out_root()]
    for base in temp_bases():
        root = os.path.join(base, f"explain-diff-{uid if uid is not None else 'user'}")
        try:
            st = os.lstat(root)
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode) and (uid is None or (st.st_uid == uid and not st.st_mode & 0o077)):
            real = os.path.realpath(root)
            if real not in found:
                found.append(real)
    return found


def inside(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


# The run files: each is written by one script (or, for content.json and refs.json, by the model through the run
# guard) and read by the next. A script writes only its own name, and reads an input only under its own name, so
# no script output, and no file the model writes, can stand in for another (a model-written content.json passed
# as --target, or a page written over a policy copy).
RUN_FILES = {"target.json", "facts.json", "snippets.json", "content.json", "refs.json"}
POLICY_DIR = "policy"


def check_out(path, own=()):
    """The real path to write for `path`, or SystemExit when it is outside out_root() or not a regular file,
    or when it is another script's run file or in a policy folder (`own` names what this script writes)."""
    full = os.path.abspath(os.path.expanduser(str(path)))
    parent = os.path.realpath(os.path.dirname(full))
    real = os.path.join(parent, os.path.basename(full))
    name = os.path.basename(full)
    if name in RUN_FILES and name not in own:
        raise SystemExit(f"refusing to write {path}: {name} is another step's run file")
    if os.path.basename(parent) == POLICY_DIR and POLICY_DIR not in own:
        raise SystemExit(f"refusing to write {path}: the policy copies are written only by gather_facts.py")
    if os.environ.get(OVERRIDE) != "1":
        root = out_root()
        if not inside(parent, root):
            raise SystemExit(f"refusing to write {path}: explain-diff writes only under {root}")
        if not ALLOW_GUARD_FILES and any(inside(parent, os.path.join(root, d)) for d in ("runs", "logs")):
            raise SystemExit(f"refusing to write {path}: the run guard's state and logs are not outputs")
    if os.path.lexists(real) and not stat.S_ISREG(os.lstat(real).st_mode):
        raise SystemExit(f"refusing to write {path}: it exists and is not a regular file")
    if not os.path.isdir(parent):
        raise SystemExit(f"refusing to write {path}: folder {parent} does not exist")
    return real


def private_tmp(path, text, own=()):
    """Write text to a new 0600 file beside the checked output path; return (temp name, real path)."""
    real = check_out(path, own)
    fd, tmp = tempfile.mkstemp(prefix=".explain-diff-", suffix=os.path.splitext(real)[1], dir=os.path.dirname(real))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
    except BaseException:
        os.unlink(tmp)
        raise
    return tmp, real


def write_private(path, text, own=()):
    tmp, real = private_tmp(path, text, own)
    os.replace(tmp, real)
    return real


def load_run_file(path, name):
    """The JSON of a run file, read only under the name the script that writes it uses (target.json, facts.json,
    snippets.json): the run guard lets the model write content.json and refs.json, never these. Skill maintenance
    (EXPLAIN_DIFF_ANY_OUT=1: the test suites and the example page) may use other names."""
    if os.environ.get(OVERRIDE) != "1":
        if os.path.basename(str(path)) != name:
            raise SystemExit(f"{path} is not a {name}: pass the {name} the skill's script wrote")
        if not os.path.isfile(path) or os.path.islink(path):
            raise SystemExit(f"{path} is not a regular file")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


GUARD_TICKET_SECONDS = 600


def take_guard_ticket(repo):
    """True, once, when the run guard just allowed `resolve_target.py --work` for this repository: it writes a note
    (runs/ticket.<session>.json) before the command runs, and this takes it. The window allows for Claude Code's own
    permission prompt. A session whose guard is not loaded (hooks disabled, for example by a repository's
    .claude/settings.json disableAllHooks, or the plugin's hooks missing) has no note, so the run does not start.
    It is a check against a guard that is switched off, not a boundary: a model with no guard could write a note."""
    now = time.time()
    want = os.path.realpath(repo)
    for root in known_roots():
        folder = os.path.join(root, "runs")
        try:
            names = [n for n in os.listdir(folder) if n.startswith("ticket.") and n.endswith(".json")]
        except OSError:
            continue
        for name in names:
            path = os.path.join(folder, name)
            try:
                with open(path, encoding="utf-8") as f:
                    t = json.load(f)
            except (OSError, ValueError):
                continue
            # A time ahead of now (beyond clock jitter) is not a fresh note, it is a forged or broken file.
            if (isinstance(t, dict) and isinstance(t.get("ts"), (int, float)) and isinstance(t.get("repo"), str)
                    and os.path.realpath(t["repo"]) == want and -5 <= now - t["ts"] <= GUARD_TICKET_SECONDS):
                try:
                    os.unlink(path)
                except OSError:
                    pass  # a sandboxed resolver may not delete in the hook's folder; the time window still applies
                return True
    return False


def new_work(label):
    """A fresh, unpredictable 0700 working folder under out_root()."""
    label = re.sub(r"[^A-Za-z0-9]+", "-", str(label or "change"))[:20].strip("-") or "change"
    return tempfile.mkdtemp(prefix=f"{datetime.date.today().isoformat()}-{label}-", dir=out_root())


# ---- git ----
# No hook or fsmonitor program runs. A clone whose core.hooksPath points at a tracked folder (husky, lefthook)
# would otherwise run the change's own hooks: git fetch runs reference-transaction, commit runs several more.
GIT_NO_PROGRAMS = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                   # and paths always from the repository root: a user's diff.relative=true would make a diff run from
                   # a subfolder print paths relative to it and leave out the files outside it.
                   "-c", "diff.relative=false"]
# Commands that can run an external diff or textconv program configured for a path's diff attribute.
GIT_DIFF_COMMANDS = {"diff", "log", "show"}


def git(*args, cwd=None, timeout=None, binary=False):
    """Run git with no hook, fsmonitor, external diff or textconv program; returns the CompletedProcess.
    Every explain-diff script calls git through here (evals/meta_test.py checks that)."""
    args = list(args)
    if args and args[0] in GIT_DIFF_COMMANDS:
        args[1:1] = ["--no-ext-diff", "--no-textconv"]
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if binary:
        return subprocess.run(["git", *GIT_NO_PROGRAMS, *args], capture_output=True, cwd=cwd, timeout=timeout, env=env)
    return subprocess.run(["git", *GIT_NO_PROGRAMS, *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=cwd, timeout=timeout, env=env)


STATUS = {"A": "added", "D": "deleted", "M": "modified", "R": "renamed", "C": "copied", "T": "type changed"}


def changed_files(diff_args, cwd=None):
    """The changed files with line counts and status, as target.json records them (resolve_target.py writes
    this list; check_target recomputes it), or None when git cannot diff diffArgs. -z keeps paths exact (no quoting
    of spaces or non-ASCII names)."""
    def diff(fmt):
        p = git("diff", fmt, "-z", "-M", "--end-of-options", *diff_args, "--", cwd=cwd)
        if p.returncode != 0:
            raise ValueError(p.stderr.strip()[:300])
        return p.stdout.split("\0")

    try:
        numstat, namestatus = diff("--numstat"), diff("--name-status")
    except ValueError:
        return None
    files, tok, i = {}, numstat, 0
    while i < len(tok):
        head, i = tok[i], i + 1
        if not head:
            continue
        add, dele, path = head.split("\t", 2)
        if not path:  # a rename or copy: the old and new paths follow
            path, i = tok[i + 1], i + 2
        files[path] = {"path": path, "additions": None if add == "-" else int(add), "deletions": None if dele == "-" else int(dele)}
    tok, i = namestatus, 0
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


SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")  # a full object id (SHA-1 or SHA-256)
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def check_target(target):
    """Refuse a target.json whose values could reach git as options or point outside the repository under review.
    It must look exactly as resolve_target.py writes it: diffArgs is [mergeBase, head] as full commit ids, or
    ["HEAD"] for uncommitted changes; every path is relative; no name starts with '-'; repo is the
    repository the script runs in; and files is the list git gives for diffArgs. Raises SystemExit with the reason."""
    def bad(why):
        raise SystemExit(f"target.json is not one resolve_target.py wrote ({why}); run resolve_target.py --work again")

    if not isinstance(target, dict) or target.get("status") != "ok":
        bad("status is not ok")
    working = target.get("mode") == "working"
    args = target.get("diffArgs")
    if working:
        if args != ["HEAD"]:
            bad("diffArgs for uncommitted changes must be [\"HEAD\"]")
    elif not (isinstance(args, list) and len(args) == 2 and all(isinstance(a, str) and SHA.fullmatch(a) for a in args)):
        bad("diffArgs must be two full commit ids")
    head, base = target.get("head") or {}, target.get("base") or {}
    if not isinstance(head, dict) or not isinstance(base, dict):
        bad("head and base must be objects")
    for name, value, optional in (("head.sha", head.get("sha"), working), ("base.sha", base.get("sha"), False),
                                  ("mergeBase", target.get("mergeBase"), False)):
        if value is None and optional:
            continue
        if not (isinstance(value, str) and SHA.fullmatch(value)):
            bad(f"{name} is not a full commit id")
    if working and base.get("sha") != target.get("mergeBase"):
        bad("for uncommitted changes the base is the merge base (HEAD)")
    if not working and args != [target.get("mergeBase"), head.get("sha")]:
        bad("diffArgs is not [mergeBase, head.sha]")
    files = target.get("files")
    if not isinstance(files, list):
        bad("files is not a list")
    for f in files:
        for key in ("path", "oldPath"):
            p = f.get(key) if isinstance(f, dict) else None
            if key == "oldPath" and p is None:
                continue
            if not isinstance(p, str) or not p or p.startswith("/") or "\0" in p:  # paths reach git after -- only
                bad(f"files has a path that is not a relative repository path: {p!r}")
    pr = target.get("pr")
    if pr is not None and not (isinstance(pr, dict) and isinstance(pr.get("number"), int) and not isinstance(pr.get("number"), bool)):
        bad("pr.number is not a number")
    for key in ("remote", "defaultBranch"):
        name = target.get(key)
        if name is not None and not (isinstance(name, str) and name and not name.startswith("-") and not CONTROL_CHARS.search(name)):
            bad(f"{key} is not a ref name (it is empty, starts with '-', or has a control character)")
    repo = target.get("repo")
    top = git("rev-parse", "--show-toplevel")
    if not isinstance(repo, str) or top.returncode != 0 or os.path.realpath(repo) != os.path.realpath(top.stdout.strip()):
        bad("repo is not the repository this script runs in; run it from the repository under review")
    # The file list decides which files get snippets and which count as policy changes: it must be git's.
    if files != changed_files(args, cwd=repo):
        bad("files is not the list git gives for diffArgs" + ("; the working tree may have changed since" if working else ""))
    return target


def read_in_repo(repo, path):
    """A working-tree file's text, confined to the repository. A symlink gives its target path (as git shows it)."""
    root = os.path.realpath(repo)
    full = os.path.join(root, path)
    if os.path.isabs(path) or not inside(os.path.dirname(full), root):
        raise OSError(f"{path} is outside the repository")
    if os.path.islink(full):
        return os.readlink(full)
    if not inside(full, root) or not os.path.isfile(full):
        raise OSError(f"{path} is not a regular file in the repository")
    with open(full, encoding="utf-8", errors="replace") as f:
        return f.read()


# ---- masking ----
TOKENS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}")),
    ("API key", re.compile(r"\bsk-(?:proj-|live-)?[A-Za-z0-9_-]{32,}")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("Stripe key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("GitLab token", re.compile(r"\bgl(?:pat|dt|ptt|rt|cbt|imt|oas|soat|ffct)-[A-Za-z0-9_-]{20,}")),
    ("Slack webhook", re.compile(r"https://hooks\.slack\.com/(?:services|workflows|triggers)/[A-Za-z0-9/_-]{20,}")),
    ("Discord webhook", re.compile(r"https://(?:\w+\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]{20,}")),
    ("npm token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
]
URL_CRED = re.compile(r"(?<=://)([^/\s:@'\"]+):([^/\s@'\"]{3,})(?=@)")
# Key-name runs and value lengths are bounded, so one long line (minified code) cannot make these quadratic.
SECRET_KEY = r"(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|token|client[_-]?secret|private[_-]?key)[A-Za-z0-9_]{0,40}"
ASSIGNED = re.compile(r"(?i)(?<![A-Za-z0-9])" + SECRET_KEY + r"[\"']?\s{0,8}(?::=|=>|[:=])\s{0,8}[\"']([^\"'\s]{8,512})[\"']")
# Unquoted values in YAML, .env and properties files: the whole rest of the line, so code like token = make() is skipped.
ASSIGNED_BARE = re.compile(r"(?im)^[ \t]{0,40}(?:export[ \t]+)?[\"']?(?:[A-Za-z0-9_.-]{0,40}[_.-])?" + SECRET_KEY
                           + r"[\"']?[ \t]{0,8}(?::=|=>|[:=])[ \t]{0,8}([^\s\"'#()\[\]{},]{8,512})[ \t]*(?:#.*)?$")
LONG = re.compile(r"[A-Za-z0-9+/_=-]{32,}")
EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]{1,64})@((?:[A-Za-z0-9-]{1,63}\.){1,8}[A-Za-z]{2,24})\b")
SAFE_DOMAINS = re.compile(r"(?:^|\.)(?:example\.(?:com|org|net)|test|invalid|localhost|example)$", re.I)
SAFE_LOCAL = {"git", "noreply", "no-reply", "dev", "user", "test", "someone", "foo", "bar"}
PLACEHOLDER = re.compile(r"(?i)^(?:x+|\*+|\.+|changeme|change[_-]?me|password|secret|example|dummy|placeholder|redacted|your[_-].*|<.*>|\$\{.*\}|%\(.*\)s|\{\{.*\}\})$")


def entropy(s):
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in (s.count(x) for x in set(s))) if n else 0.0


def secret_value(v):
    """An assigned value that is not a placeholder, already masked, or low-entropy text."""
    return not (PLACEHOLDER.match(v) or v.startswith("[masked") or entropy(v) < 2.5)


def looks_random(s):
    """Long, high-entropy, mixed-case-and-digit text. An absolute path is judged segment by segment, so
    /var/folders/1y/abc.../T/file is not a secret; a key with / in it (base64, an AWS secret key) is judged whole."""
    if s.startswith("/"):
        return any(looks_random(seg) for seg in s.split("/") if len(seg) >= 24)
    return bool(len(s) >= 24 and entropy(s) >= 4.0 and re.search(r"[a-z]", s) and re.search(r"[A-Z]", s)
                and (re.search(r"\d", s) or re.search(r"[+/]", s)))


def mask(text):
    """(masked text, [kinds masked]). Masks keep the line; only the value is replaced."""
    if not isinstance(text, str) or not text:
        return text, []
    found = []

    def sub(kind):
        def f(m):
            found.append(kind)
            return f"[masked {kind}]"
        return f

    for kind, rx in TOKENS:
        text = rx.sub(sub(kind), text)

    def url(m):
        found.append("URL password")
        return f"{m.group(1)}:[masked]"
    text = URL_CRED.sub(url, text)

    def assigned(m):
        v = m.group(1)
        if not secret_value(v):
            return m.group(0)
        found.append("assigned secret")
        return m.group(0).replace(v, "[masked secret]")
    text = ASSIGNED.sub(assigned, text)
    text = ASSIGNED_BARE.sub(assigned, text)

    def long(m):
        if not looks_random(m.group(0)):
            return m.group(0)
        found.append("high-entropy string")
        return "[masked high-entropy string]"
    text = LONG.sub(long, text)

    def email(m):
        local, domain = m.group(1), m.group(2)
        if SAFE_DOMAINS.search(domain) or local.lower() in SAFE_LOCAL:
            return m.group(0)
        found.append("email address")
        return f"[masked email]@{domain}"
    text = EMAIL.sub(email, text)
    return text, found


def mask_tree(obj, where="", hits=None):
    """Mask every string in a JSON tree in place; returns {where: [kinds]} for what was masked."""
    hits = {} if hits is None else hits
    if isinstance(obj, dict):
        for k in list(obj):
            if isinstance(obj[k], str):
                obj[k], kinds = mask(obj[k])
                if kinds:
                    hits.setdefault(f"{where}.{k}".lstrip("."), []).extend(kinds)
            else:
                mask_tree(obj[k], f"{where}.{k}", hits)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                obj[i], kinds = mask(v)
                if kinds:
                    hits.setdefault(f"{where}[{i}]", []).extend(kinds)
            else:
                mask_tree(v, f"{where}[{i}]", hits)
    return hits


def unmasked(text):
    """Secret-shaped values still present in text (for the validator); masked placeholders are ignored."""
    out = []
    for kind, rx in TOKENS:
        out += [f"{kind}: {m.group(0)[:12]}..." for m in rx.finditer(text)]
    out += [f"URL password in {m.group(1)}:..." for m in URL_CRED.finditer(text) if not m.group(2).startswith("[masked")]
    for rx in (ASSIGNED, ASSIGNED_BARE):
        for m in rx.finditer(text):
            if secret_value(m.group(1)):
                out.append(f"assigned secret: {m.group(1)[:4]}...")
    out += [f"high-entropy string: {m.group(0)[:8]}..." for m in LONG.finditer(text) if looks_random(m.group(0))]
    return out
