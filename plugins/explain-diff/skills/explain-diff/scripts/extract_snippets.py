#!/usr/bin/env python3
"""Build the snippet data for an explain-diff page from the real diff.

Usage:
  python3 extract_snippets.py --target target.json --list
  python3 extract_snippets.py --target target.json --refs refs.json --out snippets.json
  python3 extract_snippets.py --target target.json --all-hunks --out snippets.json

target.json is the output of resolve_target.py. refs.json maps a snippet id to a
location, written as "path:START-END" (new file line numbers) or
"path:old:START-END" (old line numbers, for a pure deletion):

  {"s_loop": "pagefetch/client.py:19-32", "s_gone": "tools.py:old:850-856"}

Each snippet is one unified block: removed and added lines in diff order, old and
new line numbers, the referenced lines marked focus, 5 lines of context on each
side, extended toward 20 lines when the file has more, capped at 80 lines. A
reference to a file the change does not touch gives context lines only.

--list prints the changed hunks with ready-made locations, so you can pick refs.
--all-hunks makes one snippet per hunk, with ids h_<file>_<n>.
"""
import argparse
import json
import os
import re
import subprocess
import sys

CONTEXT, TARGET_LEN, CAP = 5, 20, 80
LOC = re.compile(r"^(?P<file>.+?):(?:(?P<old>old):)?(?P<a>\d+)(?:-(?P<b>\d+))?$")


def git(*args):
    p = subprocess.run(("git",) + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])}: {p.stderr.strip()[:200]}")
    return p.stdout


def file_rows(target, path):
    """Every line of the file as a diff row: {old, new, kind, text}."""
    diff_args = target["diffArgs"]
    old_path = next((f.get("oldPath") for f in target.get("files", []) if f["path"] == path and f.get("oldPath")), None)
    spec = [old_path, path] if old_path else [path]
    out = git("diff", "-U1000000", "--no-color", "--no-ext-diff", "-M", *diff_args, "--", *spec)
    if out.strip():
        if "\nBinary files " in out or out.startswith("Binary files "):
            raise RuntimeError(f"{path} is binary")
        rows, o, n, started = [], 0, 0, False
        for line in out.split("\n"):
            if line.startswith("@@"):
                m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                o, n, started = int(m.group(1)), int(m.group(2)), True
                if o == 0:
                    o = 1
                if n == 0:
                    n = 1
                continue
            if not started or line.startswith("\\"):
                continue
            if line.startswith("+"):
                rows.append({"old": None, "new": n, "kind": "add", "text": line[1:]}); n += 1
            elif line.startswith("-"):
                rows.append({"old": o, "new": None, "kind": "del", "text": line[1:]}); o += 1
            elif line.startswith(" "):
                rows.append({"old": o, "new": n, "kind": "ctx", "text": line[1:]}); o += 1; n += 1
        return rows
    # Not changed by the diff: show the file as it is at the head.
    head = target["head"].get("sha")
    if head:
        text = git("show", f"{head}:{path}")
    else:
        with open(os.path.join(target.get("repo", "."), path), encoding="utf-8", errors="replace") as f:
            text = f.read()
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [{"old": i, "new": i, "kind": "ctx", "text": t} for i, t in enumerate(lines, 1)]


def window(rows, first, last):
    """Indexes [lo, hi] around the focus rows: context, extension toward 20 lines, cap at 80."""
    lo, hi = max(0, first - CONTEXT), min(len(rows) - 1, last + CONTEXT)
    while hi - lo + 1 < TARGET_LEN and (lo > 0 or hi < len(rows) - 1):
        if lo > 0:
            lo -= 1
        if hi - lo + 1 < TARGET_LEN and hi < len(rows) - 1:
            hi += 1
    if hi - lo + 1 > CAP:
        lo = max(0, first - CONTEXT) if last - first + 1 <= CAP - CONTEXT else first
        hi = lo + CAP - 1
    return lo, hi


def label(s):
    """The chip label: new line numbers when the lines exist in the new file, else old ones."""
    base = s["file"].rsplit("/", 1)[-1]
    if s.get("newStart") is not None:
        a, b = s["newStart"], s["newEnd"]
        return f"{base}:{a}" if a == b else f"{base}:{a}-{b}"
    a, b = s["oldStart"], s["oldEnd"]
    return f"old {base}:{a}" if a == b else f"old {base}:{a}-{b}"


def snippet(rows, path, a, b, old=False):
    key = "old" if old else "new"
    idx = [i for i, r in enumerate(rows) if r[key] is not None and a <= r[key] <= b]
    if not idx:
        side = "old" if old else "new"
        raise RuntimeError(f"{path}: no {side} lines {a}-{b} (the file has {sum(1 for r in rows if r[key] is not None)} {side} lines)")
    first, last = idx[0], idx[-1]
    if not old and rows[first]["kind"] == "add":  # a modification: the removed run just above belongs to it
        while first > 0 and rows[first - 1]["kind"] == "del":
            first -= 1
    lo, hi = window(rows, first, last)
    lines = []
    for i in range(lo, hi + 1):
        r = dict(rows[i])
        if first <= i <= last:  # removed lines between focused added lines are part of the reference
            r["focus"] = True
        lines.append(r)
    focus = [r for r in lines if r.get("focus")]
    news = [r["new"] for r in focus if r["new"] is not None]
    olds = [r["old"] for r in focus if r["old"] is not None] or [r["old"] for r in lines if r["old"] is not None]
    s = {"file": path,
         "oldStart": min(olds) if olds else None, "oldEnd": max(olds) if olds else None,
         "newStart": min(news) if news and not old else None, "newEnd": max(news) if news and not old else None,
         "lines": lines}
    if old and not news:
        s["oldStart"], s["oldEnd"] = a, b
    s["label"] = label(s)
    return s


def hunks(rows):
    """Runs of changed rows, merged when fewer than 4 context lines separate them."""
    out, cur, gap = [], None, 0
    for i, r in enumerate(rows):
        if r["kind"] == "ctx":
            gap += 1
            continue
        if cur and gap < 4:
            cur[1] = i
        else:
            cur = [i, i]
            out.append(cur)
        gap = 0
    res = []
    for s, e in out:
        seg = rows[s:e + 1]
        news = [r["new"] for r in seg if r["kind"] == "add"] or [r["new"] for r in seg if r["new"] is not None]
        olds = [r["old"] for r in seg if r["kind"] == "del"]
        adds = sum(1 for r in seg if r["kind"] == "add")
        dels = sum(1 for r in seg if r["kind"] == "del")
        pure_del = adds == 0
        res.append({"old": pure_del, "a": min(olds) if pure_del else min(news), "b": max(olds) if pure_del else max(news), "start": s, "end": e,
                    "additions": adds, "deletions": dels, "first": next((r["text"].strip() for r in seg if r["kind"] != "ctx" and r["text"].strip()), "")})
    return res


def slug(path, taken):
    """An id stem from the file name; parent folders are added until it is unique (p/__init__.py vs q/__init__.py)."""
    parts = path.split("/")
    for n in range(1, len(parts) + 1):
        stem = re.sub(r"[^A-Za-z0-9]+", "_", "/".join(parts[-n:])).strip("_").lower() or "file"
        if stem not in taken:
            break
    while stem in taken:
        stem += "_x"
    taken.add(stem)
    return stem


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--target", required=True, help="JSON written by resolve_target.py")
    ap.add_argument("--refs", help="JSON object: snippet id -> 'path:START-END' or 'path:old:START-END'")
    ap.add_argument("--all-hunks", action="store_true", help="one snippet per changed hunk")
    ap.add_argument("--list", action="store_true", help="print the changed hunks and exit")
    ap.add_argument("--out", help="write the snippet data here")
    a = ap.parse_args()
    target = json.load(open(a.target, encoding="utf-8"))
    if target.get("status") != "ok":
        sys.exit(f"target is not resolved: {target.get('summary')}")
    os.chdir(target.get("repo", "."))
    cache = {}

    def rows_for(path):
        if path not in cache:
            cache[path] = file_rows(target, path)
        return cache[path]

    if a.list or a.all_hunks:
        data, taken = {}, set()
        for f in target["files"]:
            try:
                hs = hunks(rows_for(f["path"]))
            except RuntimeError as e:
                print(f"skip {f['path']}: {e}", file=sys.stderr)
                continue
            stem = slug(f["path"], taken)
            for n, h in enumerate(hs, 1):
                loc = f"{f['path']}:{'old:' if h['old'] else ''}{h['a']}-{h['b']}"
                if a.list:
                    print(f"{loc}\t+{h['additions']}/-{h['deletions']}\t{h['first'][:70]}")
                else:
                    data[f"h_{stem}_{n}"] = snippet(rows_for(f["path"]), f["path"], h["a"], h["b"], h["old"])
        if a.list:
            return
    else:
        if not a.refs:
            sys.exit("pass --refs, --all-hunks, or --list")
        refs = json.load(open(a.refs, encoding="utf-8"))
        data, errors = {}, []
        for sid, loc in refs.items():
            m = LOC.match(str(loc).strip())
            if not m:
                errors.append(f"{sid}: cannot read location {loc!r}; use path:START-END or path:old:START-END")
                continue
            start = int(m.group("a"))
            end = int(m.group("b") or start)
            try:
                data[sid] = snippet(rows_for(m.group("file")), m.group("file"), start, end, bool(m.group("old")))
            except RuntimeError as e:
                errors.append(f"{sid}: {e}")
        if errors:
            sys.exit("\n".join(errors))
    text = json.dumps(data, indent=1, ensure_ascii=False)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        for sid, s in data.items():
            print(f"{sid}\t{s['label']}\t{len(s['lines'])} lines")
    else:
        print(text)


if __name__ == "__main__":
    main()
