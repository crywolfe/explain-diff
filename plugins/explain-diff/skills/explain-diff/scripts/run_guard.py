#!/usr/bin/env python3
"""Run guard for explain-diff: enforces the skill's tool boundary outside the model.

A PreToolUse and UserPromptSubmit hook, registered by the plugin's hooks/hooks.json. Plugin hooks
run in every session, so the guard does nothing unless an explain-diff run is active in this session.

A run starts when the Skill tool invokes explain-diff, when a prompt starts with /explain-diff, or when
one of the skill's scripts is run (so a run that timed out is re-armed by its next script). It ends at the
first user prompt after build_page.py has run, after IDLE_MINUTES with no tool call, RUN_HOURS after it
started, or on the prompt `/explain-diff:explain-diff stop`. The state is one file per session under
<out root>/runs/.

While a run is active every tool call is checked against an allowlist and logged (JSONL) under
<out root>/logs/:
  - the skill's own scripts, verified byte for byte against this copy;
  - read-only git subcommands, and gh pr view|diff|checks|list, gh repo view, gh auth status; not git status or a
    git diff against the working tree, which refresh the index and can run a tracked post-index-change hook
    (a diff between commits must end in --, with option values attached and plain revisions: see reads_worktree);
  - read-only shell utilities (ls, cat, head, tail, wc, rg, grep, find, sort, cut, diff, ...);
  every command's options are an exact allowlist, and every path argument must resolve (symlinks followed)
  inside the repository, the skill or the output folder, with no unquoted glob;
  - Read, Glob and Grep inside the repository, the skill and the output folder;
  - Write and Edit only to content.json and refs.json in a run's working folder (and REVIEW.md in the
    review-md-setup worktree); the scripts' own files (target.json, facts.json, policy copies) are not writable;
  - review-init through the Skill tool, unless the repository under review supplies it.
Everything else is denied: network tools, MCP tools, subagents, other skills, git writes, git outside the
repository, gh writes, python -c, environment assignments, command substitution, redirection to files,
unquoted globs, shell comments, zsh =cmd and ~user forms, and every other command.

In every session, active or not, the two commit scripts (commit_for_qa.py, review_md_branch.py) get
"ask": Claude Code asks the user before each one runs, whatever the model says the user said. A harness
that answers for the user (the model evals) sets EXPLAIN_DIFF_GUARD_CONSENT=allow.

If the guard itself fails (bad input, a damaged state file, an output folder it cannot use), it denies the
call; a state file that is not valid counts as an active run. No tool may write the guard's own state or logs.
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import safety  # noqa: E402

safety.ALLOW_GUARD_FILES = True  # only this process may write the guard's own runs/ and logs/

SKILL_DIR = os.path.dirname(HERE)
SCRIPTS = {"resolve_target.py", "gather_facts.py", "extract_snippets.py", "build_page.py", "validate_page.py"}
CONSENT_SCRIPTS = {"commit_for_qa.py", "review_md_branch.py"}
SKILL_NAMES = {"explain-diff", "explain-diff:explain-diff"}
RUN_HOURS = 4
IDLE_MINUTES = 30
STOP_RX = re.compile(r"/(?:explain-diff:)?explain-diff\s+(?:stop|end)\s*$")
STOP_HINT = "end the run early with /explain-diff:explain-diff stop"

# Subagents (Agent, Task) are not here: the skill does not use them, and their tool calls may not reach this guard.
ALWAYS_OK = {"TodoWrite", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "AskUserQuestion", "ToolSearch"}
READ_TOOLS = {"Read", "Glob", "Grep"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
WRITABLE = {"content.json", "refs.json"}  # the only files the model writes; the scripts write the rest

# Every command's options are an exact allowlist: an option, a bundle of short flags, or a long option that is
# not listed is denied. (Denylists of bad options miss bundles like -iO and options added in new versions.)
# Spec: (short flags, short flags that take a value, long flags, long options that take a value, positional mode)
# Positional modes: "paths" (each is checked to resolve inside the allowed roots, with no unquoted glob),
# "pattern" (the first is a search pattern unless -e/-f/--files gave one, the rest are paths), "text" (not paths).
UTILS = {
    "ls": ("laAhRtrS1dFpi", "", {"--all", "--almost-all", "--human-readable", "--recursive", "--classify"}, set(), "paths"),
    "cat": ("nbsvetA", "", set(), set(), "paths"),
    "head": ("qv", "nc", set(), {"--lines", "--bytes"}, "paths"),
    "tail": ("qvr", "nc", set(), {"--lines", "--bytes"}, "paths"),
    "wc": ("lwcm", "", {"--lines", "--words", "--bytes", "--chars"}, set(), "paths"),
    "rg": ("inwlcSFvoHNIuxqsU0.", "gtTABCmefM",
           {"--line-number", "--no-line-number", "--ignore-case", "--smart-case", "--case-sensitive", "--word-regexp", "--files-with-matches",
            "--files-without-match", "--count", "--count-matches", "--fixed-strings", "--invert-match", "--only-matching", "--with-filename",
            "--no-filename", "--no-heading", "--heading", "--hidden", "--no-ignore", "--files", "--json", "--multiline", "--trim", "--stats",
            "--unrestricted", "--vimgrep", "--null", "--column", "--no-column", "--quiet", "--passthru", "--byte-offset"},
           {"--glob", "--iglob", "--type", "--type-not", "--context", "--before-context", "--after-context", "--max-count", "--max-columns",
            "--regexp", "--file", "--sort", "--sortr", "--color", "--max-depth", "--max-filesize", "--replace", "--context-separator"}, "pattern"),
    "grep": ("inrlcvwEFHhoqsxILZz", "eABCmf",
             {"--line-number", "--ignore-case", "--recursive", "--count", "--files-with-matches", "--files-without-match", "--no-filename",
              "--with-filename", "--fixed-strings", "--extended-regexp", "--word-regexp", "--invert-match", "--only-matching", "--quiet"},
             {"--include", "--exclude", "--exclude-dir", "--color", "--regexp", "--file", "--max-count", "--context", "--before-context",
              "--after-context", "--binary-files"}, "pattern"),
    "file": ("bi", "", {"--brief", "--mime"}, set(), "paths"),
    "stat": ("", "fc", set(), {"--format"}, "paths"),
    "sort": ("rnufbdhVsz", "kt", {"--reverse", "--numeric-sort", "--unique", "--ignore-case", "--human-numeric-sort", "--version-sort", "--stable"},
             {"--key", "--field-separator"}, "paths"),
    "cut": ("s", "dfcb", {"--only-delimited"}, {"--delimiter", "--fields", "--characters", "--bytes"}, "paths"),
    "diff": ("uqwbBiNat", "U", {"--brief", "--ignore-all-space", "--ignore-space-change", "--ignore-blank-lines", "--text"}, {"--unified"}, "paths"),
    "cmp": ("ls", "", set(), set(), "paths"),
    "pwd": ("LP", "", set(), set(), "none"),
    "true": ("", "", set(), set(), "text"),
    "echo": None, "printf": None, "basename": None, "dirname": None, "tr": None,  # write to stdout only; arguments are text
}
FIND_TESTS_VALUE = {"-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename", "-type", "-maxdepth", "-mindepth", "-size", "-mtime",
                    "-mmin", "-regex", "-iregex", "-newer"}
FIND_TESTS = {"-print", "-print0", "-prune", "-empty", "-not", "!", "-o", "-a", "-and", "-or", "-true", "-false", "-depth", "-xdev", "(", ")"}
PATH_VALUE_OPTS = {"-f", "--file", "-newer"}
PATTERN_OPTS = {"-e", "-f", "--regexp", "--file", "--files"}

GIT_READ = {"diff", "log", "show", "status", "rev-parse", "merge-base", "ls-files", "ls-tree", "grep", "blame", "cat-file",
            "describe", "shortlog", "rev-list", "name-rev", "for-each-ref", "show-ref", "diff-tree", "branch", "remote", "worktree"}
GIT_GLOBAL_OK = {"--no-pager", "-P", "--no-optional-locks"}
GIT_LONG = {
    "--stat", "--shortstat", "--numstat", "--name-only", "--name-status", "--oneline", "--patch", "--no-patch", "--raw", "--summary", "--cached",
    "--staged", "--merge-base", "--no-color", "--abbrev-commit", "--no-abbrev-commit", "--decorate", "--no-decorate", "--graph", "--reverse",
    "--first-parent", "--no-merges", "--merges", "--all", "--follow", "--full-index", "--ignore-all-space", "--ignore-space-change",
    "--ignore-blank-lines", "--function-context", "--show-toplevel", "--abbrev-ref", "--verify", "--quiet", "--short", "--git-dir",
    "--is-inside-work-tree", "--symbolic-full-name", "--is-ancestor", "--fork-point", "--others", "--exclude-standard", "--modified",
    "--deleted", "--stage", "--error-unmatch", "--long", "--full-tree", "--full-name", "--line-number", "--ignore-case", "--word-regexp",
    "--files-with-matches", "--count", "--extended-regexp", "--fixed-strings", "--porcelain", "--branch", "--tags", "--always", "--heads",
    "--no-commit-id", "--left-right", "--cherry-pick", "--boundary", "--exit-code", "--no-renames", "--find-renames", "--find-copies",
    "--show-current", "--list", "--remotes", "--verbose", "--root", "--no-ext-diff", "--no-textconv", "--minimal", "--histogram",
    "--patience", "--no-prefix", "--text", "--null", "--ignored", "--word-diff", "--relative"}
GIT_LONG_VALUE = {
    "--format", "--pretty", "--stat", "--unified", "--diff-filter", "--since", "--until", "--after", "--before", "--author", "--committer",
    "--grep", "--max-count", "--skip", "--abbrev", "--date", "--sort", "--count", "--find-renames", "--porcelain", "--untracked-files",
    "--relative", "--word-diff", "--color", "--decorate", "--contains", "--merged", "--no-merged", "--points-at", "--short", "--max-depth"}
GIT_SHORT = "pPqsrtdzlnwicEFhIvbuaoMWRBCAx0123456789"
GIT_SHORT_VALUE = {"": "ULeGSf", "grep": "ULeGSfABCm", "log": "ULeGSfn", "rev-list": "ULeGSfn", "shortlog": "ULeGSfn"}
GIT_BRANCH_OK = {"--show-current", "--list", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--contains", "--merged", "--no-merged",
                 "--sort", "--format", "--points-at"}
# gh: (flags, options that take a value) per command. No --web, --jq, --template or --show-token.
GH = {("pr", "view"): ({"--comments", "-c"}, {"--json", "--repo", "-R"}),
      ("pr", "diff"): ({"--name-only", "--patch"}, {"--color", "--repo", "-R"}),
      ("pr", "checks"): ({"--required"}, {"--json", "--repo", "-R"}),
      ("pr", "list"): (set(), {"--state", "-s", "--limit", "-L", "--head", "-H", "--base", "-B", "--author", "-A", "--search", "-S",
                               "--json", "--label", "-l", "--repo", "-R"}),
      ("repo", "view"): (set(), {"--json"}),
      ("auth", "status"): (set(), {"--hostname", "-h"})}
SENSITIVE = re.compile(r"/\.(?:ssh|aws|gnupg|netrc|npmrc|pypirc|docker|kube)(?:/|$)|/\.config/(?:gh|gcloud)(?:/|$)|/\.git-credentials$|^/etc/(?:passwd|shadow)$")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
REDIRECT_RX = re.compile(r"(?:2>/dev/null|2>&1|&>/dev/null|>/dev/null)(?=[\s;|&]|$)")


class Deny(Exception):
    pass


# ---- state ----
def state_path(session):
    d = os.path.join(safety.out_root(), "runs")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return os.path.join(d, re.sub(r"[^A-Za-z0-9_-]", "_", str(session or "nosession"))[:80] + ".json")


def load_state(session, cwd=None):
    """The run's state, or None when no run is active. A state file that exists but is not a valid state counts
    as an active run (it is reset), so a damaged file can never switch the guard off."""
    path = state_path(session)
    if not os.path.lexists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        valid = (isinstance(st, dict) and isinstance(st.get("repo"), str) and isinstance(st.get("started"), (int, float))
                 and isinstance(st.get("last", 0), (int, float)) and isinstance(st.get("built", False), bool))
    except (OSError, ValueError):
        valid = False
    if not valid:
        st = _fresh_state(cwd)
        save_state(session, st)
        return st
    now = time.time()
    if now - st.get("started", 0) > RUN_HOURS * 3600 or now - st.get("last", st.get("started", 0)) > IDLE_MINUTES * 60:
        end(session)
        return None
    return st


def save_state(session, st):
    safety.write_private(state_path(session), json.dumps(st))


def end(session):
    try:
        os.unlink(state_path(session))
    except OSError:
        pass


def repo_root(cwd):
    p = safety.git("-C", cwd or ".", "rev-parse", "--show-toplevel")
    return p.stdout.strip() if p.returncode == 0 else os.path.realpath(cwd or ".")


def _fresh_state(cwd):
    return {"started": time.time(), "last": time.time(), "built": False, "repo": repo_root(cwd), "cwd": cwd}


def start(session, cwd):
    st = load_state(session)
    if not st:
        st = _fresh_state(cwd)
        save_state(session, st)
    return st


def writable(path):
    """Writes go only to the files the model authors: content.json and refs.json in a run's working folder, and
    REVIEW.md in the review-md-setup worktree (written by review-init). Everything else in the output folder is
    written by the skill's scripts (target.json, facts.json, the policy copies, snippets.json, the page) and is
    their input or output: a model that could rewrite it could pass options to git (target.json diffArgs), swap
    the rules the review applies, or make the attestation name a change the page does not explain."""
    parent, name = os.path.split(path)
    folder = os.path.basename(parent).lower()  # macOS folders are case-insensitive: RUNS is runs
    in_child = any(os.path.dirname(parent) == r for r in safety.known_roots())
    if in_child and folder not in ("runs", "logs", safety.POLICY_DIR) and name in WRITABLE:
        return
    if in_child and folder.startswith("review-md-setup-") and name == "REVIEW.md":
        return
    raise Deny(f"writes go only to content.json or refs.json in the run's working folder, not {path}; "
               "the scripts write everything else")


def guard_files(path):
    """True for the guard's own state and logs, which no tool may write."""
    return any(safety.inside(path, os.path.join(r, d)) for r in safety.known_roots() for d in ("runs", "logs"))


def log(session, payload, decision, reason):
    d = os.path.join(safety.out_root(), "logs")
    os.makedirs(d, mode=0o700, exist_ok=True)
    rec = {"ts": time.time(), "session": session, "tool": payload.get("tool_name"),
           "input": json.dumps(payload.get("tool_input"), ensure_ascii=False)[:600], "decision": decision, "reason": reason}
    fd = os.open(os.path.join(d, re.sub(r"[^A-Za-z0-9_-]", "_", str(session))[:80] + ".jsonl"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---- paths ----
def roots(st):
    return [st["repo"], *safety.known_roots(), SKILL_DIR]


def resolve(path, cwd):
    p = os.path.expanduser(str(path))
    return os.path.realpath(p if os.path.isabs(p) else os.path.join(cwd or ".", p))


def within(path, cwd, allowed):
    p = resolve(path, cwd)
    if SENSITIVE.search(p):
        return False
    return any(safety.inside(p, r) for r in allowed)


# ---- shell ----
class Word(str):
    """A shell word; `globbed` is True when it has an unquoted *, ? or [ the shell would expand."""
    globbed = False


def split_shell(cmd):
    r"""Split a command into segments of words, refusing anything that is not a plain command list.
    Separators && || ; | and newlines split segments. Outside single quotes, $ and backticks are refused
    (no expansion of any kind); redirection is refused except to /dev/null; ( ) { } and & are refused.
    Quoting follows bash and zsh: inside double quotes a backslash escapes only $ ` " \ and newline
    (a backslash-newline is removed); elsewhere it is kept. A word with a control character is refused."""
    segs, words, cur, i, n = [], [], None, 0, len(cmd)
    flags = {"quoted": False, "glob": False}

    def push_word():
        nonlocal cur
        if cur is not None:
            if CONTROL.search(cur):
                raise Deny("a control character in a word")
            if flags["glob"]:
                raise Deny(f"unquoted glob in {cur!r}: quote it (the shell would expand it into words the guard never saw)")
            w = Word(cur)
            w.globbed = flags["glob"]
            words.append(w)
        cur = None
        flags["quoted"] = flags["glob"] = False

    while i < n:
        c = cmd[i]
        if c == "'":
            j = cmd.find("'", i + 1)
            if j < 0:
                raise Deny("unbalanced quote")
            cur = (cur or "") + cmd[i + 1:j]
            flags["quoted"] = True
            i = j + 1
            continue
        if c == '"':
            j, buf = i + 1, ""
            while j < n and cmd[j] != '"':
                if cmd[j] in "$`":
                    raise Deny("expansion inside double quotes")
                if cmd[j] == "\\" and j + 1 < n:
                    nxt = cmd[j + 1]
                    if nxt == "\n":
                        j += 2
                        continue
                    buf += nxt if nxt in '$`"\\' else "\\" + nxt
                    j += 2
                    continue
                buf += cmd[j]
                j += 1
            if j >= n:
                raise Deny("unbalanced quote")
            cur = (cur or "") + buf
            flags["quoted"] = True
            i = j + 1
            continue
        if c == "\\":
            if i + 1 < n and cmd[i + 1] == "\n":
                i += 2
                continue
            cur = (cur or "") + (cmd[i + 1] if i + 1 < n else "")
            i += 2
            continue
        if c in "$`":
            raise Deny("shell expansion ($ or backtick)")
        if c in "(){}":
            raise Deny("subshells and groups")
        if c in " \t":
            push_word()
            i += 1
            continue
        two = cmd[i:i + 2]
        if two in ("&&", "||") or c in ";|\n":
            push_word()
            if words:
                segs.append(words)
            words = []
            i += 2 if two in ("&&", "||") else 1
            continue
        if c in "<>&":
            at = i - 1 if cur == "2" and not flags["quoted"] else i
            m = REDIRECT_RX.match(cmd, at)
            if m and (cur is None or at < i):
                cur, i = None, m.end()
                flags["quoted"] = flags["glob"] = False
                continue
            raise Deny("redirection or background (&)")
        if cur is None and c == "#":
            raise Deny("shell comments")
        if cur is None and c == "=":
            raise Deny("a word starting with = (zsh expands =cmd to a path)")
        if cur is None and c == "~" and i + 1 < n and cmd[i + 1] not in "/ \t\n;|&":
            raise Deny("~ forms other than ~ and ~/ (~user, ~-, ~+)")
        if c in "*?[":
            flags["glob"] = True
        cur = (cur or "") + c
        i += 1
    push_word()
    if words:
        segs.append(words)
    return segs


def verified_script(path, cwd):
    """The script's name when path is one of this skill's scripts in this guard's own folder, byte for byte,
    else None. A copy elsewhere is refused: Python would import safety.py and the rest from the copy's folder."""
    real = resolve(path, cwd)
    name = os.path.basename(real)
    if name not in SCRIPTS | CONSENT_SCRIPTS or os.path.dirname(real) != os.path.realpath(HERE):
        return None
    try:
        with open(real, "rb") as a, open(os.path.join(HERE, name), "rb") as b:
            return name if a.read() == b.read() else None
    except OSError:
        return None


def check_path(word, cwd, allowed, what):
    if getattr(word, "globbed", False):
        raise Deny(f"{what}: unquoted glob {word} (the shell would expand it past the path check)")
    if not within(word, cwd, allowed):
        raise Deny(f"{what} outside the repository: {word}")


def parse_options(cmd, args, short, short_value, long, long_value, cwd, allowed, numeric=False):
    """Walk args against an exact option allowlist. Returns (positionals, options seen). Values of -f/--file are path-checked."""
    pos, seen, i, end = [], set(), 0, False
    while i < len(args):
        a = args[i]
        if end or a == "-" or not a.startswith("-"):
            pos.append(a)
            i += 1
            continue
        if a == "--":
            end = True
            i += 1
            continue
        if numeric and re.fullmatch(r"-\d+", a):
            i += 1
            continue
        if a.startswith("--"):
            name, eq, val = a.partition("=")
            if name in long and not eq:
                seen.add(name)
            elif name in long_value:
                if not eq:
                    i += 1
                    if i >= len(args):
                        raise Deny(f"{cmd} {name} needs a value")
                    val = args[i]
                seen.add(name)
                if name in PATH_VALUE_OPTS:
                    check_path(val, cwd, allowed, f"{cmd} {name}")
            else:
                raise Deny(f"{cmd} option {name} is not on the allowlist")
            i += 1
            continue
        letters, j = a[1:], 0
        while j < len(letters):
            ch = letters[j]
            if ch in short_value:
                val = letters[j + 1:]
                if not val:
                    i += 1
                    if i >= len(args):
                        raise Deny(f"{cmd} -{ch} needs a value")
                    val = args[i]
                seen.add("-" + ch)
                if "-" + ch in PATH_VALUE_OPTS:
                    check_path(val, cwd, allowed, f"{cmd} -{ch}")
                break
            if ch not in short:
                raise Deny(f"{cmd} option -{ch} (in {a}) is not on the allowlist")
            j += 1
        i += 1
    return pos, seen


def judge_util(cmd, args, cwd, allowed):
    spec = UTILS[cmd]
    if spec is None:
        return
    short, short_value, long, long_value, mode = spec
    pos, seen = parse_options(cmd, args, short, short_value, long, long_value, cwd, allowed, numeric=cmd in ("head", "tail"))
    if mode == "none" and pos:
        raise Deny(f"{cmd} takes no arguments here")
    if mode == "pattern" and pos and not seen & PATTERN_OPTS:
        pos = pos[1:]
    if mode in ("paths", "pattern"):
        for a in pos:
            check_path(a, cwd, allowed, cmd)


def judge_find(args, cwd, allowed):
    i = 0
    while i < len(args) and args[i] in ("-E", "-s"):
        i += 1
    while i < len(args) and not (args[i].startswith("-") or args[i] in ("!", "(", ")")):
        check_path(args[i], cwd, allowed, "find")
        i += 1
    while i < len(args):
        a = args[i]
        if a in FIND_TESTS_VALUE:
            if i + 1 >= len(args):
                raise Deny(f"find {a} needs a value")
            if a in PATH_VALUE_OPTS:
                check_path(args[i + 1], cwd, allowed, f"find {a}")
            i += 2
            continue
        if a not in FIND_TESTS:
            raise Deny(f"find {a} is not on the allowlist (no -exec, -delete, -fprint, -L, ...)")
        i += 1


def judge_git(args, cwd, allowed):
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-C" and i + 1 < len(args):
            check_path(args[i + 1], cwd, allowed, "git -C")
            cwd = resolve(args[i + 1], cwd)
            i += 2
            continue
        if args[i] not in GIT_GLOBAL_OK:
            raise Deny(f"git option {args[i]}")
        i += 1
    sub, rest = (args[i], args[i + 1:]) if i < len(args) else (None, [])
    if not safety.inside(resolve(".", cwd), allowed[0]):
        raise Deny("git runs only inside the repository under review (a folder elsewhere can carry a .git/config that runs programs)")
    if sub not in GIT_READ:
        raise Deny(f"git {sub or ''} is not read-only".strip())
    if sub == "status" or (sub == "diff" and reads_worktree(rest)):
        # Refreshing the index runs post-index-change, from a tracked hooks folder too (husky's core.hooksPath).
        raise Deny(f"git {sub} on the working tree can run a repository hook; read the change with "
                   "extract_snippets.py --target WORK/target.json --diff, or git diff between two commits ending in -- "
                   "(git diff A B --, git diff A..B -- path; option values attached, as --diff-filter=M) or git diff --cached")
    if sub == "branch":
        if any(a.startswith("-") and a.split("=")[0] not in GIT_BRANCH_OK for a in rest):
            raise Deny("git branch may only list")
        if any(not a.startswith("-") for a in rest) and not any(a in ("--contains", "--merged", "--no-merged", "--points-at", "--list") for a in rest):
            raise Deny("git branch may only list")
        return cwd
    if sub == "remote" and rest not in ([], ["-v"], ["--verbose"]) and not (rest[:1] == ["get-url"] and len(rest) == 2):
        raise Deny("git remote may only list (no show: it contacts the remote)")
    if sub == "worktree" and rest not in (["list"], ["list", "--porcelain"]):
        raise Deny("git worktree may only list")
    value = GIT_SHORT_VALUE.get(sub, GIT_SHORT_VALUE[""])
    pos, _ = parse_options(f"git {sub}", rest, "".join(ch for ch in GIT_SHORT if ch not in value), value, GIT_LONG, GIT_LONG_VALUE,
                           cwd, allowed, numeric=True)
    for a in pos:
        if a.startswith(("/", "~")):
            check_path(a, cwd, allowed, f"git {sub}")
    return cwd


# git diff options that take no value of their own: anything else must carry its value attached (--opt=value, -U3),
# so no option can consume the next word and leave one revision, which git would compare with the working tree.
DIFF_FLAGS = {"--stat", "--shortstat", "--numstat", "--name-only", "--name-status", "--patch", "-p", "-u", "--no-patch", "-s",
              "--raw", "--summary", "--no-color", "--cached", "--staged", "--merge-base", "-M", "--find-renames", "-R", "-w",
              "--ignore-all-space", "-b", "--ignore-space-change", "--ignore-blank-lines", "-W", "--function-context",
              "--full-index", "--word-diff", "--abbrev", "--no-renames", "--minimal", "--patience", "--histogram",
              "--no-ext-diff", "--no-textconv", "-z", "--text", "-a", "--exit-code", "--quiet", "--no-prefix"}
DIFF_ATTACHED = re.compile(r"--[a-z][a-z-]*=.*|-U[0-9]+|-M[0-9]+")
# A revision the model may name: a ref or object id with ~N / ^N steps. No :/ search, ^{...}, ^@, ^!, @{...}
# or leading ^: each can give one commit or none, and git then diffs the working tree.
REV = re.compile(r"[A-Za-z0-9_@][A-Za-z0-9._/@+-]*(?:[~^][0-9]*)*")


def rev_kind(word):
    """'rev' for one commit, 'range' for A..B or A...B (one side may be empty), None for any other syntax."""
    ok = lambda r: bool(REV.fullmatch(r)) and ".." not in r  # noqa: E731
    for dots in ("...", ".."):
        if dots in word:
            a, b = word.split(dots, 1)
            return "range" if (a or b) and all(ok(x) for x in (a, b) if x) else None
    return "rev" if ok(word) else None


def reads_worktree(rest):
    """True unless this git diff surely compares two commits (A B or A..B / A...B, then --) or the index (--cached).
    Any other form compares with the working tree, which refreshes the index and can run a hook. The decision
    follows what git parses, not how a word looks:
    - every option is a flag from DIFF_FLAGS or carries its value attached, so no word before -- is an option's value;
    - the commit forms need the --, because git then refuses anything before it that is not a revision (without it,
      tracked files named abc1234 and def4567 are read as paths);
    - each revision has the plain syntax of REV."""
    head = rest[:rest.index("--")] if "--" in rest else rest
    opts = [a for a in head if a.startswith("-")]
    if any(a not in DIFF_FLAGS and not DIFF_ATTACHED.fullmatch(a) for a in opts):
        return True
    if "--cached" in opts or "--staged" in opts:
        return False
    if "--" not in rest:
        return True
    kinds = [rev_kind(a) for a in head if not a.startswith("-")]
    if None in kinds:
        return True
    return not (len(kinds) >= 2 or kinds == ["range"])


def judge_gh(args):
    pos = [a for a in args if not a.startswith("-")]
    key = tuple(pos[:2])
    if key not in GH:
        raise Deny(f"gh {' '.join(key)} is not a read")
    flags, values = GH[key]
    i = 0
    while i < len(args):
        a = args[i]
        name, eq, _ = a.partition("=")
        if not a.startswith("-"):
            i += 1
        elif name in flags and not eq:
            i += 1
        elif name in values:
            i += 1 if eq else 2
        else:
            raise Deny(f"gh {' '.join(key)} option {name} is not on the allowlist")
    return key


def judge_segment(words, cwd, st, consent):
    """(decision, reason, new cwd, script name or None) for one simple command."""
    allowed = roots(st)
    if not words:
        return "allow", "empty", cwd, None
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
        raise Deny("environment assignments")
    cmd, args = words[0], words[1:]
    if cmd == "cd":
        target = args[0] if args else Word(os.path.expanduser("~"))
        check_path(target, cwd, allowed, "cd")
        return "allow", "cd", resolve(target, cwd), None
    if cmd in ("python3", "python"):
        if not args or args[0].startswith("-"):
            raise Deny("python only runs the skill's own scripts")
        name = verified_script(args[0], cwd)
        if not name:
            raise Deny(f"{args[0]} is not one of the skill's scripts (in {HERE})")
        if name in CONSENT_SCRIPTS:
            return consent, f"{name} commits only with the user's yes", cwd, name
        return "allow", f"skill script {name}", cwd, name
    if cmd == "git":
        return "allow", "git", judge_git(args, cwd, allowed), None
    if cmd == "gh":
        return "allow", "gh " + " ".join(judge_gh(args)), cwd, None
    if cmd == "find":
        judge_find(args, cwd, allowed)
        return "allow", "find", cwd, None
    if cmd in UTILS:
        judge_util(cmd, args, cwd, allowed)
        return "allow", cmd, cwd, None
    raise Deny(f"{cmd} is not on the explain-diff allowlist (use Read, Grep and Glob to explore; never run the change's code)")


def judge_bash(cmd, cwd, st, consent):
    """The whole command is judged before any state changes: a denied command never marks the run as built."""
    reasons, decision, names = [], "allow", []
    for seg in split_shell(cmd):
        d, why, cwd, name = judge_segment(seg, cwd, st, consent)
        reasons.append(why)
        names.append(name)
        if d == "ask":
            decision = "ask"
    if "build_page.py" in names:
        st["built"] = True
    return decision, "; ".join(reasons)


def repo_review_init(st, cwd):
    for base in {st["repo"], resolve(".", cwd)}:
        if os.path.exists(os.path.join(base, ".claude", "skills", "review-init")):
            return os.path.join(base, ".claude", "skills", "review-init")
    return None


def judge(payload, st, consent):
    tool, inp, cwd = payload.get("tool_name") or "", payload.get("tool_input") or {}, payload.get("cwd") or st["cwd"]
    if tool in ALWAYS_OK:
        return "allow", tool
    if tool == "Bash":
        return judge_bash(inp.get("command") or "", cwd, st, consent)
    if tool in READ_TOOLS or tool in WRITE_TOOLS:
        p = inp.get("file_path") or inp.get("path") or inp.get("notebook_path") or cwd
        allowed = safety.known_roots() if tool in WRITE_TOOLS else roots(st)
        if tool in WRITE_TOOLS and guard_files(resolve(p, cwd)):
            raise Deny(f"{tool} on the run guard's own state or logs: {p}")
        if not within(p, cwd, allowed):
            raise Deny(f"{tool} outside {'the output folder' if tool in WRITE_TOOLS else 'the repository'}: {p}")
        if tool in WRITE_TOOLS:
            writable(resolve(p, cwd))
        return "allow", tool
    if tool == "Skill":
        name = skill_name(inp)
        if name in SKILL_NAMES:
            return "allow", "explain-diff"
        if name.split(":")[-1] == "review-init":
            src = repo_review_init(st, cwd) if ":" not in name else None
            if src:
                raise Deny(f"review-init is supplied by the repository under review ({src}); only a user or plugin install is used")
            return "allow", "review-init"
        raise Deny(f"skill {name or '?'} does not run during an explain-diff run")
    if tool in ("WebFetch", "WebSearch") or tool.startswith("mcp__"):
        raise Deny(f"{tool}: no network or MCP tools during an explain-diff run")
    raise Deny(f"{tool} is not on the explain-diff allowlist")


def skill_name(inp):
    words = str(inp.get("skill") or inp.get("command") or "").lstrip("/").split()
    return words[0] if words else ""


def starts_run(payload):
    tool, inp = payload.get("tool_name"), payload.get("tool_input") or {}
    if tool == "Skill" and skill_name(inp) in SKILL_NAMES:
        return True
    if tool == "Bash" and any(name in (inp.get("command") or "") for name in SCRIPTS):
        try:
            return any(len(seg) > 1 and seg[0] in ("python3", "python") and verified_script(seg[1], payload.get("cwd")) in SCRIPTS
                       for seg in split_shell(inp["command"]))
        except Deny:
            return False
    return False


def consent_call(payload):
    if payload.get("tool_name") != "Bash":
        return False
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    return any(s in cmd for s in CONSENT_SCRIPTS)


def output(decision, reason):
    if decision in ("deny", "ask"):
        hint = f" ({STOP_HINT})" if decision == "deny" else ""
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                                 "permissionDecisionReason": f"explain-diff run guard: {reason}{hint}"}}))


def handle(payload):
    session = payload.get("session_id")
    event = payload.get("hook_event_name")
    cwd = payload.get("cwd")
    consent = "allow" if os.environ.get("EXPLAIN_DIFF_GUARD_CONSENT") == "allow" else "ask"
    if event == "UserPromptSubmit":
        prompt = str(payload.get("prompt") or "").lstrip()
        st = load_state(session, cwd)
        if st and st.get("built"):
            end(session)
        if STOP_RX.match(prompt):
            end(session)
            return
        if re.match(r"/(?:explain-diff:)?explain-diff\b", prompt):
            start(session, cwd)
        return
    st = load_state(session, cwd)
    if not st and starts_run(payload):
        st = start(session, cwd)
    if not st:
        if consent_call(payload) and consent == "ask":
            output("ask", "commits only with the user's yes")
        return
    try:
        decision, reason = judge(payload, st, consent)
    except Deny as e:
        decision, reason = "deny", str(e)
    st["last"] = time.time()
    save_state(session, st)
    log(session, payload, decision, reason)
    output(decision, reason)


def main():
    """Fails closed: if the input, the state or the output folder cannot be checked, the tool call is denied.
    (A hook that exits 1 is a non-blocking error in Claude Code, so every failure is turned into a denial.)"""
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input is not an object")
    except BaseException as e:  # noqa: BLE001 - cannot read the call: block it
        print(f"explain-diff run guard: unreadable hook input ({type(e).__name__})", file=sys.stderr)
        sys.exit(2)
    try:
        handle(payload)
    except BaseException as e:  # noqa: BLE001 - includes SystemExit from safety.out_root()
        if payload.get("hook_event_name") == "UserPromptSubmit":
            print(f"explain-diff run guard error: {type(e).__name__}: {e}", file=sys.stderr)
            return  # never block the user's own prompt; the next tool call is denied
        try:
            output("deny", f"guard error, so the call is blocked ({type(e).__name__}: {e}). Fix the cause or disable the explain-diff plugin")
        except BaseException:  # noqa: BLE001
            sys.exit(2)


if __name__ == "__main__":
    main()
