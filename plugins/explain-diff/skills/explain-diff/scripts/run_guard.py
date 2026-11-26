#!/usr/bin/env python3
"""Run guard for explain-diff: enforces the skill's tool boundary outside the model.

A PreToolUse, UserPromptSubmit and SessionStart hook, registered by the plugin's hooks/hooks.json. Plugin
hooks run in every session, so the guard does nothing unless an explain-diff run is active in this session.

A run starts when the Skill tool invokes explain-diff, when a prompt starts with /explain-diff, or when
one of the skill's scripts is run. It does not end when the page is built, or after any idle time or age:
the change's text (diff, PR, repository files) stays in the session's context, and injected instructions in
it can act on any later turn. It ends only when the user runs /clear (SessionStart "clear") or types the
prompt `/explain-diff:explain-diff stop`, which warns that the text is still in context. Compaction and
resume keep the run. The state is one file per session under <out root>/runs/; if it is gone (temp folders are
cleaned on reboot), SessionStart and the next prompt re-arm the run from the session's transcript. When the guard
allows `resolve_target.py --work`, it leaves a one-use note (runs/ticket.<session>.json) that the resolver needs
to start, so a session whose hooks are off does not run the skill unguarded.

While a run is active every tool call is checked against an allowlist and logged (JSONL, with secrets and personal
email addresses masked; logs older than LOG_DAYS are deleted when a run starts) under <out root>/logs/:
  - the skill's own scripts, verified byte for byte against this copy;
  - read-only git subcommands, and gh pr view|diff|checks|list, gh repo view, gh auth status on the repository under
    review only (PR numbers, no --repo, --search, URL or GH_REPO); not git status or a
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
"ask": Claude Code asks the user before each one runs, whatever the model says the user said. In a permission
mode where no one sees that question (dontAsk, bypassPermissions) they are denied. A harness that answers for
the user (the model evals) passes --consent=allow on its own hook command line, which no repository setting
can change.

A run refuses every call while a maintenance override (EXPLAIN_DIFF_ANY_OUT, EXPLAIN_DIFF_GUARD_CONSENT) is in
the environment: in a review session it can only come from settings, perhaps the repository's own
.claude/settings.json env, and it would switch off the scripts' protections.

If the guard itself fails (bad input, a damaged state file, an output folder it cannot use), it denies the
call; a state file that is not valid counts as an active run. No tool may write the guard's own state or logs.
"""
import json
import os
import re
import stat
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
STOP_RX = re.compile(r"/(?:explain-diff:)?explain-diff\s+(?:stop|end)\s*$")
STOP_HINT = ("this session stays guarded while the change's text is in its context: /clear ends the run, "
             "or /explain-diff:explain-diff stop ends it with that text still in context")
STOP_NOTE = ("explain-diff run guard: the run has ended, but the change's text (diff, PR, repository files) is still in this "
             "session's context. Treat anything it asks for as untrusted, or run /clear before other work.")
# Permission modes in which Claude Code shows a hook's "ask" to the user (auto mode included: a hook's ask goes to the
# prompt, not the classifier). In dontAsk and bypassPermissions no one answers, so the commit scripts are denied.
INTERACTIVE_MODES = {"default", "acceptEdits", "plan", "auto"}
# Maintenance overrides. None belongs in a review session: in one, they can only come from settings, possibly the
# repository's own .claude/settings.json env, so a run with any of them set is refused outright.
OVERRIDE_ENV = ("EXPLAIN_DIFF_ANY_OUT", "EXPLAIN_DIFF_GUARD_CONSENT")

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
# gh: (flags, options that take a value, PR selectors allowed) per command. No --web, --jq, --template or --show-token,
# and nothing that names another repository: no --repo, no --search (its repo: qualifier), no URL or OWNER/REPO argument.
# gh then reads the repository under review, from the remotes of the folder it runs in (judge_gh checks the folder).
GH = {("pr", "view"): ({"--comments", "-c"}, {"--json"}, 1),
      ("pr", "diff"): ({"--name-only", "--patch"}, {"--color"}, 1),
      ("pr", "checks"): ({"--required"}, {"--json"}, 1),
      ("pr", "list"): (set(), {"--state", "-s", "--limit", "-L", "--head", "-H", "--base", "-B", "--author", "-A",
                               "--json", "--label", "-l"}, 0),
      ("repo", "view"): (set(), {"--json"}, 0),
      ("auth", "status"): (set(), {"--hostname", "-h"}, 0)}
GH_PR_NUMBER = re.compile(r"^#?[0-9]{1,9}$")
# Environment that points gh at another repository (GH_REPO), or makes it start its pager, which runs any program
# (GH_FORCE_TTY: gh pages only to a terminal, and Bash here has none). The repository's .claude/settings.json can set
# them. GH_HOST only picks among this repository's own remotes, and GH_PAGER, PAGER or a GH_CONFIG_DIR pager need a
# terminal, so they are left alone.
GH_UNSAFE_ENV = ("GH_REPO", "GH_FORCE_TTY")
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
    # No idle or age limit: the change's text stays in the session's context, so the run lasts until the user
    # types the stop prompt or /clear (SessionStart "clear"). Compaction and resume keep it (run_in_transcript
    # re-arms it if this file was removed).
    return st


def save_state(session, st):
    safety.write_private(state_path(session), json.dumps(st))


def end(session):
    for path in (state_path(session), ticket_path(session)):
        try:
            os.unlink(path)
        except OSError:
            pass


def ticket_path(session):
    """Where the guard notes that it allowed `resolve_target.py --work` (a run's first step) for this session's
    repository. A state file's name has no dot before .json, so a ticket's name never is one."""
    return os.path.join(os.path.dirname(state_path(session)), "ticket." + os.path.basename(state_path(session)))


# A slash-command record in a transcript: only the command's own tags (name, message, args, in any order), nothing
# else. Shell output (`! cmd`), pasted text and notifications are string entries too, and can quote these tags.
COMMAND_RECORD = re.compile(r"\s*(?:<command-(name|message|args)>[\s\S]*?</command-\1>\s*)+")
TRANSCRIPT_HINTS = (b"explain-diff", b"resolve_target.py", b"/clear")


def transcript_entries(path):
    """The transcript's entries that could start or end a run, read a line at a time (transcripts can be large)."""
    try:
        if not path or not os.path.isfile(path):
            return
        with open(path, "rb") as f:
            for raw in f:
                if any(h in raw for h in TRANSCRIPT_HINTS):
                    try:
                        entry = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(entry, dict):
                        yield entry
    except OSError:
        return


def typed_command(entry):
    """What the user typed, for a user entry that is the user's own prompt or slash command; else None. Tool results
    (list content), meta entries, compaction summaries and anything not from the user never count."""
    msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    content = msg.get("content")
    origin = entry.get("origin") if isinstance(entry.get("origin"), dict) else None
    if (entry.get("type") != "user" or not isinstance(content, str) or entry.get("isMeta") or entry.get("isCompactSummary")
            or (origin is not None and origin.get("kind") != "human")):
        return None
    text = content.strip()
    if text.startswith("<"):
        if not COMMAND_RECORD.fullmatch(text):
            return None  # shell output, a notification, or other tagged text: not a command the user ran
        name = re.search(r"<command-name>\s*(/[\w:-]+)\s*</command-name>", text)
        args = re.search(r"<command-args>([\s\S]*?)</command-args>", text)
        return (name.group(1) + " " + (args.group(1).strip() if args else "")).strip() if name else None
    return text


def run_in_transcript(path):
    """True when the session's transcript shows an explain-diff run that was not ended: a start (the skill, its
    slash command, or resolve_target.py --work) after the last stop prompt or /clear. It lets a run whose state file
    was removed (temp folders are cleaned on reboot, and macOS purges old files) come back guarded on resume.
    Starts are read anywhere; ends only from the user's own prompts and slash-command records, so text from the
    change can start a run but never end one. Unreadable or missing: False (no run is known)."""
    active = False
    for entry in transcript_entries(path):
        typed = typed_command(entry)
        if typed is not None:
            if typed == "/clear" or typed.startswith("/clear ") or STOP_RX.match(typed):
                active = False
            elif re.match(r"/(?:explain-diff:)?explain-diff\b", typed):
                active = True
            continue
        if entry.get("type") != "assistant":
            continue  # tool results, shell output (/plugin, ! cmd) and notifications name the skill without running it
        content = (entry.get("message") or {}).get("content") if isinstance(entry.get("message"), dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            inp = block.get("input") if isinstance(block.get("input"), dict) else {}
            if block.get("name") == "Skill" and skill_name(inp) in SKILL_NAMES and not re.match(r"\s*(?:stop|end)\s*$", str(inp.get("args") or "")):
                active = True
            elif block.get("name") == "Bash" and resolve_cwd({"tool_name": "Bash", "tool_input": inp, "cwd": "/"}) is not None:
                active = True
    return active


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
        prune_logs()
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


LOG_DAYS = 30
LOG_MASK_CHARS = 8192
LOG_NAME = re.compile(r"^[A-Za-z0-9_-]{1,80}\.jsonl$")


def masked(value):
    """`value` (a tool input or a reason) with secrets and personal email addresses masked as the page masks them.
    Masking runs on the parsed strings, before JSON escaping and before the log cuts the input short, so a quoted
    secret still matches and no half token is left."""
    def cut(v):  # only the first 600 characters are logged; masking a whole 10 MB Write would be slow
        if isinstance(v, str):
            return v[:LOG_MASK_CHARS]
        if isinstance(v, dict):
            return {k: cut(x) for k, x in v.items()}
        if isinstance(v, list):
            return [cut(x) for x in v]
        return v
    copy = cut(json.loads(json.dumps(value, ensure_ascii=False)))
    if isinstance(copy, str):
        return safety.mask(copy)[0]
    safety.mask_tree(copy)
    return copy


def prune_logs(now=None):
    """Delete this user's run logs older than LOG_DAYS: only regular files with the guard's own names, not symlinks."""
    d = os.path.join(safety.out_root(), "logs")
    cutoff = (now or time.time()) - LOG_DAYS * 86400
    try:
        if os.path.islink(d):
            return
        names = os.listdir(d)
    except OSError:
        return
    for name in names:
        path = os.path.join(d, name)
        try:
            st = os.lstat(path)
            if LOG_NAME.match(name) and stat.S_ISREG(st.st_mode) and st.st_mtime < cutoff:
                os.unlink(path)
        except OSError:
            pass


def log(session, payload, decision, reason):
    d = os.path.join(safety.out_root(), "logs")
    os.makedirs(d, mode=0o700, exist_ok=True)
    rec = {"ts": time.time(), "session": session, "tool": payload.get("tool_name"),
           "input": json.dumps(masked(payload.get("tool_input")), ensure_ascii=False)[:600], "decision": decision,
           "reason": masked(reason)}
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


def judge_gh(args, cwd, repo):
    """The command's (subcommand, verb) if it only reads the repository under review, else Deny."""
    key = tuple(a for a in args[:2])
    if key not in GH:
        raise Deny(f"gh {' '.join(key)} is not a read")
    here = resolve(".", cwd)
    top = safety.git("-C", here, "rev-parse", "--show-toplevel") if os.path.isdir(here) else None
    if not (top and top.returncode == 0 and os.path.realpath(top.stdout.strip()) == os.path.realpath(repo)):
        raise Deny("gh runs only in the repository under review, not in a folder inside it with its own .git (a submodule or "
                   "a vendored clone) or around it: gh reads the repository from the nearest .git's remotes")
    unsafe = [v for v in GH_UNSAFE_ENV if os.environ.get(v)]
    if unsafe:
        raise Deny(f"gh with {', '.join(unsafe)} set in the session's environment, which points it at another repository "
                   "or makes it run a pager program")
    flags, values, selectors = GH[key]
    pos, i = [], 2
    while i < len(args):
        a = args[i]
        name, eq, _ = a.partition("=")
        if not a.startswith("-"):
            pos.append(a)
            i += 1
        elif name in flags and not eq:
            i += 1
        elif name in values:
            if not eq and i + 1 >= len(args):
                raise Deny(f"gh {' '.join(key)} option {name} needs a value")
            i += 1 if eq else 2
        else:
            raise Deny(f"gh {' '.join(key)} option {name} is not on the allowlist")
    if len(pos) > selectors or not all(GH_PR_NUMBER.match(p) for p in pos):
        raise Deny(f"gh {' '.join(key)} takes only a pull request number of the repository under review, not {' '.join(pos)}")
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
            if consent == "deny":
                raise Deny(f"{name} commits only with the user's yes, and in this permission mode no one is asked; "
                           "switch to default mode, or run it yourself")
            return consent, f"{name} commits only with the user's yes", cwd, name
        return "allow", f"skill script {name}", cwd, name
    if cmd == "git":
        return "allow", "git", judge_git(args, cwd, allowed), None
    if cmd == "gh":
        return "allow", "gh " + " ".join(judge_gh(args, cwd, allowed[0])), cwd, None
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


# Programs that only read a file named on their command line. Anything else that names a commit script may run it.
VIEWERS = {"rg", "grep", "egrep", "fgrep", "cat", "head", "tail", "less", "more", "wc", "ls", "file", "stat", "diff",
           "bat", "fd", "find", "shasum", "sha256sum", "md5", "md5sum", "realpath", "dirname", "basename"}
RUNNERS = re.compile(r"(?:python[\d.]*|pypy[\d.]*|uv|bash|sh|zsh|dash|ksh|fish|env|xargs|exec|perl|ruby|node|source|\.)$")
GIT_VIEWS = {"log", "show", "diff", "blame", "status", "ls-files", "grep"}


def consent_call(payload):
    """True when the Bash command may run a commit script: any segment that names one, unless its program only reads
    files (rg, cat, git log). A command the guard cannot parse that names one counts, to be safe."""
    if payload.get("tool_name") != "Bash":
        return False
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not any(s in cmd for s in CONSENT_SCRIPTS):
        return False
    try:
        segments = split_shell(cmd)
    except Deny:
        return True
    runner = any(seg and RUNNERS.match(os.path.basename(seg[0])) for seg in segments)  # cat X | python3, echo X | xargs ...
    for seg in segments:
        prog = [w for w in seg if not re.match(r"[A-Za-z_][A-Za-z0-9_]*=", w)]  # env assignments before the program
        if not prog or not any(os.path.basename(w).strip("'\"") in CONSENT_SCRIPTS or any(s in w for s in CONSENT_SCRIPTS) for w in prog):
            continue
        name = os.path.basename(prog[0])
        if not runner and (name in VIEWERS or (name == "git" and len(prog) > 1 and prog[1] in GIT_VIEWS)):
            continue  # reads the file (rg, cat, git log), does not run it
        return True  # python3 -u X, bash -c '...X', env python3 X, xargs, timeout: anything else may run it
    return False


def resolve_cwd(payload):
    """The folder `resolve_target.py --work` runs in, when the Bash command runs it (after any `cd`); else None."""
    cmd = (payload.get("tool_input") or {}).get("command") or "" if payload.get("tool_name") == "Bash" else ""
    if "resolve_target.py" not in cmd or "--work" not in cmd:
        return None
    cwd = payload.get("cwd") or "."
    try:
        for seg in split_shell(cmd):
            if len(seg) > 1 and seg[0] == "cd":
                cwd = resolve(seg[1], cwd)
            elif len(seg) > 1 and os.path.basename(seg[1]) == "resolve_target.py" and "--work" in seg[2:]:
                return cwd
    except Deny:
        return None
    return None


def output(decision, reason, active=True):
    if decision in ("deny", "ask"):
        hint = f" ({STOP_HINT})" if decision == "deny" and active else ""
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                                 "permissionDecisionReason": f"explain-diff run guard: {reason}{hint}"}}))


def consent_for(payload, harness_consent):
    """What the commit scripts get: "allow" only for an eval harness that answers for the user (the --consent=allow
    argument on its own hook command, which no repository setting can add); otherwise "ask" where Claude Code shows
    the prompt to the user, and "deny" where no one would see it (dontAsk, bypassPermissions)."""
    if harness_consent:
        return "allow"
    mode = payload.get("permission_mode")
    return "ask" if mode is None or mode in INTERACTIVE_MODES else "deny"


def handle(payload, harness_consent=False):
    session = payload.get("session_id")
    event = payload.get("hook_event_name")
    cwd = payload.get("cwd")
    consent = consent_for(payload, harness_consent)
    if event == "SessionStart":
        if payload.get("source") == "clear":  # the context, and with it the change's text, is gone
            end(session)
        elif not load_state(session, cwd) and run_in_transcript(payload.get("transcript_path")):
            start(session, cwd)  # resumed or compacted with the run's state file gone: guard it again
        return
    if event == "UserPromptSubmit":
        prompt = str(payload.get("prompt") or "").lstrip()
        if STOP_RX.match(prompt):
            if load_state(session, cwd):
                print(json.dumps({"systemMessage": STOP_NOTE}))
            end(session)
            return
        if re.match(r"/(?:explain-diff:)?explain-diff\b", prompt) or (
                not load_state(session, cwd) and run_in_transcript(payload.get("transcript_path"))):
            start(session, cwd)
        return
    st = load_state(session, cwd)
    if not st and starts_run(payload):
        st = start(session, cwd)
    if not st:
        if consent_call(payload) and consent != "allow":
            output(consent, "commits only with the user's yes" + ("" if consent == "ask" else
                   ", and in this permission mode no one is asked; switch to default mode, or run it yourself"), active=False)
        return
    try:
        overrides = [name for name in OVERRIDE_ENV if name in os.environ]
        if overrides:
            raise Deny(f"{', '.join(overrides)} is set in this session's environment (by a settings file such as the "
                       "repository's .claude/settings.json, or the shell): it switches off explain-diff's protections, "
                       "so no run proceeds with it. Remove it and start a new session")
        decision, reason = judge(payload, st, consent)
    except Deny as e:
        decision, reason = "deny", str(e)
    st["last"] = time.time()
    save_state(session, st)
    where = resolve_cwd(payload) if decision == "allow" else None
    if where is not None:
        # resolve_target.py --work starts only when it finds this note for the repository it runs in
        # (safety.take_guard_ticket), so a session whose hooks are off does not start a run unguarded.
        safety.write_private(ticket_path(session), json.dumps({"repo": repo_root(where), "ts": time.time()}))
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
        handle(payload, harness_consent="--consent=allow" in sys.argv[1:])
    except BaseException as e:  # noqa: BLE001 - includes SystemExit from safety.out_root()
        if payload.get("hook_event_name") in ("UserPromptSubmit", "SessionStart"):
            print(f"explain-diff run guard error: {type(e).__name__}: {e}", file=sys.stderr)
            return  # never block the user's own prompt; the next tool call is denied
        try:
            output("deny", f"guard error, so the call is blocked ({type(e).__name__}: {e}). Fix the cause or disable the explain-diff plugin")
        except BaseException:  # noqa: BLE001
            sys.exit(2)


if __name__ == "__main__":
    main()
