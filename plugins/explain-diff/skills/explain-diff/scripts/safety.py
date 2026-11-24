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
"""
import datetime
import math
import os
import re
import stat
import tempfile

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
    try:
        bases.append(os.confstr("CS_DARWIN_USER_TEMP_DIR"))  # macOS per-user temp, whatever TMPDIR says
    except (AttributeError, ValueError, OSError):
        pass
    return [b for b in bases if b]


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


def check_out(path):
    """The real path to write for `path`, or SystemExit when it is outside out_root() or not a regular file."""
    full = os.path.abspath(os.path.expanduser(str(path)))
    parent = os.path.realpath(os.path.dirname(full))
    real = os.path.join(parent, os.path.basename(full))
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


def private_tmp(path, text):
    """Write text to a new 0600 file beside the checked output path; return (temp name, real path)."""
    real = check_out(path)
    fd, tmp = tempfile.mkstemp(prefix=".explain-diff-", suffix=os.path.splitext(real)[1], dir=os.path.dirname(real))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
    except BaseException:
        os.unlink(tmp)
        raise
    return tmp, real


def write_private(path, text):
    tmp, real = private_tmp(path, text)
    os.replace(tmp, real)
    return real


def new_work(label):
    """A fresh, unpredictable 0700 working folder under out_root()."""
    label = re.sub(r"[^A-Za-z0-9]+", "-", str(label or "change"))[:20].strip("-") or "change"
    return tempfile.mkdtemp(prefix=f"{datetime.date.today().isoformat()}-{label}-", dir=out_root())


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
