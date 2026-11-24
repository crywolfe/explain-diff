#!/usr/bin/env python3
"""Assemble an explain-diff page from content JSON and the fixed template.

Usage:
  python3 build_page.py --content content.json --target target.json --snippets snippets.json
                        [--facts facts.json] [--out PAGE.html] [--qa-out FOR_QA.md] [--date YYYY-MM-DD] [--no-validate]

You write only content.json (prose, review and governance data, visualization
specs, quiz). The template in ../templates supplies every line of CSS and
JavaScript: the code panel, Brief / Full, visualizations, quiz, governance marks
and gate. This script escapes all text, embeds the data blocks with every "<"
escaped, writes the For QA notes, and runs the validator's static checks.

Default outputs: <out root>/<date>-explanation-<slug>.html and <out root>/<date>-for-qa-<slug>.md,
where <out root> is the private per-user folder from safety.py (<temp dir>/explain-diff-<uid>).
Outputs elsewhere are refused (see safety.py); both files are written 0600.

Secrets and personal email addresses in the snippets and in every content text are masked
before the page is built (the line stays, the value is replaced); the build names what it masked.

The gate data carries provenance for the attestation: the skill version, the model the content
names as its author (self-reported), a digest of the build inputs, the sha256 of the page itself
(computed with the field zeroed; the validator recomputes it), and, with --facts, the path, sha256
and commit of each policy file the review used.
See content.example.json next to the templates for the content shape.

Rich text (any prose field): plain text, with
  [[SNIPPET_ID]]          a reference chip, labelled from the snippet (client.py:19-32)
  [[SNIPPET_ID|label]]    a chip with your own label
  `code`                  inline code
  **bold**                bold
A block list (body, full, ...) holds strings (paragraphs) or objects:
  {"p": rich}  {"list": [rich, ...]}  {"callout": rich, "title": "..."}, each with optional "full": true.
"""
import argparse
import copy
import datetime
import hashlib
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(HERE, "..", "templates")
sys.path.insert(0, HERE)
from extract_snippets import label as snippet_label  # noqa: E402
import safety  # noqa: E402

PAGE_HASH_PLACEHOLDER = "0" * 64

TOKEN = re.compile(r"\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]|`([^`]+)`|\*\*(.+?)\*\*")
SECTIONS = [("background", "Background"), ("intent", "Intent"), ("review", "Review"), ("governance", "Governance"),
            ("intuition", "Intuition"), ("code", "Code"), ("quiz", "Quiz"), ("gate", "Gate")]
JS_VIZ = {"stepper", "graph", "sequence", "compare", "scrubber"}
STATIC_VIZ = {"map", "chart"}
BUDGET = {"background": 200, "intent": 200, "review": 250, "intuition": 250, "group": 120, "caption": 40}
RISK_LABEL = {"high": "High", "medium": "Medium", "low": "Low"}
UNDO_LABEL = {"flag": "Flag", "revert": "Clean revert", "careful": "Needs care", "irreversible": "Not reversible"}
CI_LABEL = {"pass": "passing", "fail": "failing", "pending": "pending", "unknown": "unknown (could not read it)", "none": "no checks on this change"}


QUIZ_MIN, QUIZ_MAX = 3, 10  # quiz length range; the skill's argument --quiz N picks within it, 5 by default
WORDS = {3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}

class Build:
    def __init__(self, content, target, snippets):
        self.c, self.t, self.snip = content, target, snippets
        self.errors, self.warnings, self.used = [], [], set()
        self.words = {}
        self.viz_placed = []  # (id, full)

    # ---- text ----
    def esc(self, s):
        return html.escape(str(s), quote=True)

    def chip(self, sid, lab=None):
        self.used.add(sid)
        if sid not in self.snip:
            self.errors.append(f"reference [[{sid}]] has no snippet")
            return self.esc(lab or sid)
        return f'<button type="button" class="ref" data-ref="{self.esc(sid)}">{self.esc(lab or self.snip[sid].get("label") or snippet_label(self.snip[sid]))}</button>'

    def rich(self, s, count=None):
        if s is None:
            return ""
        s = str(s)
        if count:
            plain = TOKEN.sub(lambda m: m.group(2) or m.group(3) or m.group(4) or "ref", s)
            self.words[count] = self.words.get(count, 0) + len(plain.split())
        out, pos = [], 0
        for m in TOKEN.finditer(s):
            out.append(self.esc(s[pos:m.start()]))
            if m.group(1):
                out.append(self.chip(m.group(1).strip(), m.group(2)))
            elif m.group(3) is not None:
                out.append(f"<code>{self.esc(m.group(3))}</code>")
            else:
                out.append(f"<b>{self.rich(m.group(4))}</b>")
            pos = m.end()
        out.append(self.esc(s[pos:]))
        return "".join(out)

    def blocks(self, items, count=None, full=False):
        out = []
        for b in items or []:
            if isinstance(b, str):
                b = {"p": b}
            cls = ' class="full-only"' if (full or b.get("full")) else ""
            if "p" in b:
                out.append(f"<p{cls}>{self.rich(b['p'], count)}</p>")
            elif "list" in b:
                out.append(f"<ul{cls}>" + "".join(f"<li>{self.rich(x, count)}</li>" for x in b["list"]) + "</ul>")
            elif "callout" in b:
                title = f"<b>{self.esc(b['title'])}.</b> " if b.get("title") else ""
                inner = f'<div class="callout">{title}{self.rich(b["callout"], count)}</div>'
                out.append(f'<div class="full-only">{inner}</div>' if cls else inner)
            else:
                self.errors.append(f"unknown block {list(b)[:3]}")
        return "\n".join(out)

    def tag(self, text, cls=""):
        return f'<span class="tag{(" " + self.esc(cls)) if cls else ""}">{self.esc(text)}</span>'

    # ---- visualizations ----
    def viz(self, vid, full=False):
        if not vid:
            return ""
        spec = self.c.get("viz", {}).get(vid)
        if not spec:
            self.errors.append(f"visualization {vid!r} is not in viz")
            return ""
        spec.setdefault("id", vid)
        kind = spec.get("type")
        full = full or spec.get("full", False)
        self.viz_placed.append((vid, full))
        cap = spec.get("caption", "")
        if len(str(cap).split()) > BUDGET["caption"]:
            self.warnings.append(f"caption of {vid} has {len(str(cap).split())} words (budget {BUDGET['caption']})")
        head = (f'<div class="viz{" full-only" if full else ""}"><div class="viz-head"><h3>{self.esc(spec.get("title", ""))}</h3>'
                f'<button type="button" class="btn expand" aria-expanded="false">Expand</button></div>\n<p class="cap">{self.rich(cap)}</p>\n')
        self.viz_refs(spec)
        if kind in JS_VIZ:
            return head + f'<div data-viz="{kind}" data-spec="{self.esc(vid)}"></div></div>'
        if kind == "map":
            parts = []
            for i, n in enumerate(spec.get("nodes", [])):
                if i:
                    parts.append('<div class="arrow" aria-hidden="true">&darr;</div>')
                ref = f' data-ref="{self.esc(n["ref"])}"' if n.get("ref") else ""
                parts.append(f'<button type="button" class="node"{ref}><span>{self.esc(n["label"])}</span><span class="st">{self.esc(n.get("status", ""))}</span></button>')
            return head + f'<div class="map">{"".join(parts)}</div></div>'
        if kind == "chart":
            bars = spec.get("bars", [])
            top = max([abs(float(b["value"])) for b in bars] or [1]) or 1
            unit = spec.get("unit", "")
            alt = "Bar chart; the data table below lists every value"  # labels stay out of attributes
            rows = "".join(f'<div class="bar"><span>{self.esc(b["label"])}</span><i style="width:{max(1, round(100 * abs(float(b["value"])) / top))}%"></i>'
                           f'<span>{self.esc(b["value"])}{self.esc(unit)}</span></div>' for b in bars)
            table = "".join(f'<tr><th>{self.esc(b["label"])}</th><td>{self.esc(b["value"])}{self.esc(unit)}</td></tr>' for b in bars)
            return (head + f'<div class="bars" role="img" aria-label="{self.esc(alt)}">{rows}</div>'
                    f'<details><summary>Data table</summary><table class="state">{table}</table></details></div>')
        self.errors.append(f"visualization {vid}: unknown type {kind!r} (use {sorted(JS_VIZ | STATIC_VIZ)})")
        return ""

    def viz_refs(self, spec):
        """Snippet refs sit only on nodes, steps, messages and stages; data values (compare trees, state) may hold any key."""
        items = list(spec.get("nodes") or []) + list(spec.get("messages") or []) + list(spec.get("stages") or []) + list(spec.get("steps") or [])
        for case in spec.get("cases") or []:
            items += list(case.get("steps") or []) if isinstance(case, dict) else []
        for it in items:
            if isinstance(it, dict) and it.get("ref") is not None:
                self.walk_refs({"ref": it["ref"]})

    def check_enums(self):
        r = self.c.get("review") or {}
        for row in r.get("whereToLook") or []:
            if row.get("risk") not in RISK_LABEL:
                self.errors.append(f"review.whereToLook risk {row.get('risk')!r}: use one of {sorted(RISK_LABEL)}")
        if r.get("ci") is not None and r.get("ci") not in CI_LABEL:
            self.errors.append(f"review.ci {r.get('ci')!r}: use one of {sorted(CI_LABEL)}")
        kind = (r.get("undo") or {}).get("kind")
        if kind not in UNDO_LABEL:
            self.errors.append(f"review.undo.kind {kind!r}: use one of {sorted(UNDO_LABEL)}")
        for c in (self.c.get("governance") or {}).get("controls") or []:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._:/-]{0,79}", str(c.get("id", ""))):
                self.errors.append(f"governance control id {str(c.get('id'))[:40]!r}: use letters, digits, spaces and . _ : / - (GOV-4, DOC-1)")
        rc = r.get("rootCause") or {}
        if rc.get("applies") and rc.get("verdict") not in ("cause", "symptom", "unclear"):
            self.errors.append(f"review.rootCause.verdict {rc.get('verdict')!r}: use cause, symptom, or unclear")

    def walk_refs(self, obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "ref" and isinstance(v, str):
                    self.used.add(v)
                    if v not in self.snip:
                        self.errors.append(f"ref {v!r} has no snippet")
                elif k == "refs" and isinstance(v, list):
                    for x in v:
                        self.walk_refs({"ref": x})
                else:
                    self.walk_refs(v)
        elif isinstance(obj, list):
            for v in obj:
                self.walk_refs(v)

    # ---- sections ----
    def header(self):
        t = self.t
        h, b = t["head"], t["base"]
        hs = f' @<code>{self.esc(h["sha"][:7])}</code>' if h.get("sha") else ""
        via = (f' (merge-base <code>{self.esc(t["mergeBase"][:7])}</code>)'
               if t.get("mergeBase") and t["mergeBase"] != b.get("sha") else "")
        pr = f'PR <b>#{self.esc(t["pr"]["number"])}</b>: {self.esc(t["pr"]["title"])}. ' if t.get("pr") else ""
        line = (f'{pr}Explaining <code>{self.esc(h["ref"])}</code>{hs} against <code>{self.esc(b["ref"])}</code> @<code>{self.esc((b.get("sha") or "")[:7])}</code>{via}: '
                f'{len(t.get("files", []))} file(s)'
                + (f', +{int(t["additions"])}/&minus;{int(t["deletions"] or 0)}.' if t.get("additions") is not None else "."))
        warn = "".join(f"<li>{self.esc(w)}</li>" for w in t.get("warnings", []))
        tldr = self.c.get("tldr", "")
        if len(re.findall(r"[.!?](\s|$)", tldr)) > 3:
            self.warnings.append("TL;DR has more than 3 sentences")
        assume = f'<p class="assume">Assumption: {self.rich(self.c["assumptions"])}</p>' if self.c.get("assumptions") else ""
        nav = "".join(f'<a href="#{sid}">{name}</a>' for sid, name in SECTIONS)
        return (f'<header class="top">\n<h1>{self.esc(self.c["title"])}</h1>\n<p class="tldr"><b>TL;DR:</b> {self.rich(tldr)}</p>\n'
                f'<p class="target" id="target" data-base="{self.esc(b.get("sha") or "")}" data-head="{self.esc(h.get("sha") or "")}">{line}</p>\n'
                + (f'<ul class="warnings">{warn}</ul>\n' if warn else "") + assume +
                '<div class="modes" role="group" aria-label="Detail level"><button type="button" data-mode="brief" aria-pressed="true">Brief</button>'
                '<button type="button" data-mode="full" aria-pressed="false">Full</button></div>\n</header>\n'
                f'<nav class="sticky" aria-label="Sections">{nav}<a href="#gate" id="gate-pill" class="pill" data-state="blocked">Gate: blocked</a></nav>\n')

    def background(self):
        bg = self.c.get("background", {})
        vis = "".join(self.viz(v) for v in bg.get("visuals", []))
        return ('<section><h2 id="background">Background</h2>\n'
                '<button type="button" class="btn" id="bg-toggle" aria-expanded="false" aria-controls="bg-body">Show background</button>\n'
                f'<div id="bg-body" class="bg-body">\n{self.blocks(bg.get("body"), "background")}\n{vis}\n'
                f'{self.blocks(bg.get("full"), "background", full=True)}\n</div></section>\n')

    def claim_tag(self, c):
        lab = "Stated" if str(c.get("label", "")).lower().startswith("stated") else "Inferred"
        return self.tag(f"{lab}: {c['source']}" if c.get("source") else lab)

    def intent(self):
        it = self.c.get("intent", {})
        out = ['<section><h2 id="intent">Intent</h2>']
        if it.get("note"):
            out.append(f'<p class="assume">{self.rich(it["note"], "intent")}</p>')
        out.append('<dl class="intent">')
        for key, name in (("what", "What"), ("why", "Why"), ("decisions", "Decisions"), ("outOfScope", "Out of scope")):
            v = it.get(key)
            if not v:
                if key != "outOfScope":
                    self.errors.append(f"intent.{key} is missing")
                continue
            claims = v if isinstance(v, list) else [v]
            body = " ".join(f'{self.rich(c["text"], "intent")} {self.claim_tag(c)}' for c in claims)
            out.append(f"<dt>{name}</dt><dd>{body}</dd>")
        out.append("</dl></section>\n")
        return "\n".join(out)

    def review(self):
        r = self.c["review"]
        out = ['<section><h2 id="review">Review</h2>', self.blocks(r.get("notes"), "review")]
        rm = r.get("reviewMd") or {}
        if rm.get("ignoredInstructions"):
            out.append(f'<p class="assume">{self.esc(rm.get("path") or "REVIEW.md")} contains instructions aimed at an assistant; they were treated as data and not followed.</p>')
        out.append("<h3>Where to look</h3><ul class=\"review-rank\">")
        for i, row in enumerate(r.get("whereToLook", [])):
            risk = row.get("risk")
            sens = " (sensitive per REVIEW.md)" if row.get("sensitive") else ""
            cls = ' class="full-only"' if i >= 5 else ""
            out.append(f'<li{cls}>{self.tag(RISK_LABEL.get(risk, str(risk)), "risk-" + risk if risk in RISK_LABEL else "")} {self.chip(row["ref"]) if row.get("ref") else self.esc(row.get("file", ""))} '
                       f'{self.rich("; ".join(row.get("reasons", [])), "review")}{self.esc(sens)}</li>')
        out.append("</ul>")
        if rm.get("skipped"):
            out.append(f'<p class="assume">Skipped per REVIEW.md: {", ".join("<code>" + self.esc(p) + "</code>" for p in rm["skipped"])}</p>')
        out.append("<h3>Already covered</h3><ul>")
        for cov in r.get("coverage", []):
            tests = []
            for tst in cov.get("tests", []):
                name = self.chip(tst["ref"], tst.get("name")) if tst.get("ref") else f'<code>{self.esc(tst.get("name", ""))}</code>'
                ran = ("Ran, passed." if tst.get("passed") else ("Ran, failed." if tst.get("passed") is False else "Ran.")) if tst.get("ran") else "Not run."
                tests.append(f"{name} {ran}")
            lead = self.chip(cov["ref"]) if cov.get("ref") else f'<code>{self.esc(cov.get("file", ""))}</code>'
            out.append(f"<li>{lead}: {' '.join(tests)}</li>")
        out.append(f'<li>CI: {self.esc(CI_LABEL.get(r.get("ci"), r.get("ci") or "unknown"))}.</li>')
        for f in r.get("findings", []):
            out.append(f'<li>Posted by {self.esc(f.get("author", "unknown"))}: &ldquo;{self.esc(f.get("text", ""))}&rdquo;</li>')
        out.append("</ul>")
        unc = r.get("uncovered") or []
        out.append(f'<p>Not covered by tests or checks: {", ".join("<code>" + self.esc(p) + "</code>" for p in unc)}.</p>' if unc
                   else "<p>Every High and Medium hunk has a traced test.</p>")
        rc = r.get("rootCause") or {}
        if rc.get("applies"):
            verdict = {"cause": "Cause", "symptom": "Symptom", "unclear": "Unclear"}.get(rc.get("verdict"), "Unclear")
            origin = self.chip(rc["origin"]) if rc.get("origin") else "unknown"
            fix = self.chip(rc["fix"]) if rc.get("fix") else "unknown"
            out.append(f'<h3>Root cause</h3><p>Wrong behavior originates at {origin}; the change acts at {fix}. {self.tag(verdict)} {self.rich(rc.get("evidence", ""), "review")}</p>')
        u = r.get("undo") or {}
        flag = f' Flag <code>{self.esc(u["flag"])}</code>.' if u.get("flag") else ""
        out.append(f'<h3>How to undo</h3><p>{self.tag(UNDO_LABEL.get(u.get("kind"), str(u.get("kind"))))}{flag} {self.rich(u.get("evidence", ""), "review")} '
                   f'{self.chip(u["ref"]) if u.get("ref") else ""}</p>')
        if rm.get("present") and rm.get("rules"):
            out.append('<h3>REVIEW.md checklist</h3><ul class="checklist">')
            for i, rule in enumerate(rm["rules"]):
                files = ", ".join("<code>" + self.esc(p) + "</code>" for p in rule.get("files", []))
                out.append(f'<li><label><input type="checkbox"> <span><b>{self.esc(rule.get("area", ""))}:</b> &ldquo;{self.esc(rule.get("rule", ""))}&rdquo; {files}</span></label></li>')
            out.append("</ul>")
        qa = (r.get("forQa") or {}).get("items", [])
        out.append("<h3>For QA</h3><ul>")
        for it in qa:
            ac = f' <b>AC:</b> {self.esc(it["ac"])}' if it.get("ac") else ""
            out.append(f'<li><b>Try:</b> {self.rich(it.get("try", ""))} <b>Why:</b> {self.rich(it.get("why", ""))} {self.chip(it["ref"]) if it.get("ref") else ""}{ac}</li>')
        out.append("</ul></section>\n")
        return "\n".join(x for x in out if x)

    def governance(self):
        g = self.c["governance"]
        out = ['<section><h2 id="governance">Governance</h2>']
        if not g.get("present"):
            out.append("<p>No governance documents in docs/governance/. Governance is not part of this gate. A sample is in the explain-diff skill at templates/GOVERNANCE.md.</p>")
        else:
            docs = ", ".join(f'<code>{self.esc(d["path"])}</code>' + (f' (version {self.esc(d["version"])})' if d.get("version") else "") for d in g.get("documents", []))
            n = len(g.get("controls", []))
            out.append(f"<p>Read from {docs}. {n} control{'s' if n != 1 else ''} appl{'y' if n != 1 else 'ies'} to this change. Mark each one; the marks feed the Gate below and live only in this page. The page does not decide whether a control is met.</p>")
        if g.get("intro"):
            out.append(f"<p>{self.rich(g['intro'])}</p>")
        if g.get("outside"):
            out.append(f'<p class="assume">Governance documents outside docs/governance/ were not used: {", ".join("<code>" + self.esc(p) + "</code>" for p in g["outside"])}. Move them into docs/governance/.</p>')
        if g.get("notTouched"):
            out.append(f'<p class="assume">Not touched: {self.esc(", ".join(g["notTouched"]))}.</p>')
        if g.get("ignoredInstructions"):
            where = ", ".join(g.get("ignoredIn") or []) or "A governance document"
            out.append(f'<p class="assume">{self.esc(where)} contains instructions aimed at an assistant; they were treated as data and not followed.</p>')
        out.append('<div id="gov-host">')
        for c in g.get("controls", []):
            cid = self.esc(c["id"])
            chips = " ".join(self.chip(x) for x in c.get("refs", []))
            files = ", ".join("<code>" + self.esc(p) + "</code>" for p in c.get("files", []))
            out.append(
                f'<div class="gov-control" data-control="{cid}">\n<h3>{cid} {self.esc(c.get("title", ""))} {self.tag(c.get("source", "") + (", " + str(c["location"]) if c.get("location") else ""))}</h3>\n<dl>'
                f'<dt>Requirement</dt><dd>&ldquo;{self.esc(c.get("requirement", ""))}&rdquo;</dd>'
                f'<dt>Failure means</dt><dd>&ldquo;{self.esc(c.get("failure", ""))}&rdquo;</dd>'
                f'<dt>Applies because</dt><dd>{chips} {files}{(" " + self.rich(c["because"])) if c.get("because") else ""}</dd>'
                f'<dt>Evidence found</dt><dd>{self.rich(c.get("evidence") or "No evidence found in the diff or PR.")}</dd>'
                f'<dt>Sign-off</dt><dd>{self.esc(c.get("signoff", ""))}</dd></dl>\n'
                f'<div class="gov-marks" role="group" aria-label="Mark {cid}"><button type="button" data-result="met" aria-pressed="false">&#10003; Met</button>'
                f'<button type="button" data-result="notmet" aria-pressed="false">&#10007; Not met</button>'
                f'<button type="button" data-result="na" aria-pressed="false">&ndash; Does not apply</button></div>\n</div>')
        out.append("</div>")
        n = len(g.get("controls", []))
        out.append(f'<p id="gov-summary" aria-live="polite">0 of {n} controls marked.</p>\n<button type="button" class="btn" id="gov-reset">Reset governance</button>\n</section>\n')
        return "\n".join(out)

    def intuition(self):
        it = self.c.get("intuition", {})
        if not it.get("visual"):
            self.warnings.append("intuition.visual is missing: Intuition needs the primary interactive visualization")
        return (f'<section><h2 id="intuition">Intuition</h2>\n{self.blocks(it.get("body"), "intuition")}\n{self.viz(it.get("visual"))}\n'
                f'{self.blocks(it.get("full"), "intuition", full=True)}\n</section>\n')

    def code(self):
        cd = self.c.get("code", {})
        if not cd.get("changeMap"):
            self.errors.append("code.changeMap is missing")
        out = ['<section><h2 id="code">Code</h2>', self.viz(cd.get("changeMap"))]
        groups = cd.get("groups", [])
        if len(groups) > 6:
            self.warnings.append(f"{len(groups)} code groups (at most 6)")
        for i, gr in enumerate(groups, 1):
            key = f"group {i}"
            cls = ' class="full-only"' if gr.get("full") else ""
            out.append(f"<div{cls}><h3>{i}. {self.esc(gr.get('title', ''))}</h3>")
            for rule in gr.get("reviewMd", []):
                out.append(f'<div class="callout"><b>REVIEW.md: {self.esc(rule.get("area", ""))}.</b> &ldquo;{self.esc(rule.get("rule", ""))}&rdquo;</div>')
            out.append(self.blocks(gr.get("body"), key))
            out.append(self.viz(gr.get("visual")))
            out.append("</div>")
            if self.words.get(key, 0) > BUDGET["group"]:
                self.warnings.append(f"code {key} has {self.words[key]} words (budget {BUDGET['group']})")
        out.append(self.blocks(cd.get("full"), None, full=True))
        for v in cd.get("fullVisuals", []):
            out.append(self.viz(v, full=True))
        out.append("</section>\n")
        return "\n".join(x for x in out if x)

    def quiz_and_gate(self):
        n = len(self.c["governance"].get("controls", [])) if self.c["governance"].get("present") else 0
        gov = f"<li>Governance: 0 of {n} controls marked</li>" if self.c["governance"].get("present") else \
            "<li>Governance: no governance documents in docs/governance/, so the gate needs the quiz only.</li>"
        q = len(self.c.get("quiz", []))
        all_q = f"all {WORDS.get(q, q)}"
        return ('<section><h2 id="quiz">Quiz</h2>\n<button type="button" class="btn" id="quiz-open" aria-expanded="false">Test yourself</button> '
                '<button type="button" class="btn" id="quiz-reset">Retake quiz</button>\n'
                '<p class="assume">Each question locks after your first answer. The Gate needs ' + all_q + ' right in one attempt. Retake clears your answers and shuffles the options.</p>\n'
                '<div id="quiz-host" hidden></div></section>\n'
                '<section><h2 id="gate">Gate</h2>\n<p>The gate opens when ' + all_q + ' quiz answers are right in one attempt and every governance control is marked, with none Not met. '
                'It checks that you finished both; it is not a security control, and this page cannot change the pull request.</p>\n'
                f'<div id="gate-status" data-state="blocked" role="status" aria-live="polite"><strong id="gate-label">Blocked</strong><ul id="gate-reasons"><li>Quiz: 0 of {q} answered</li>{gov}</ul></div>\n'
                '<div class="gate-actions"><button type="button" class="btn" id="gate-retake" hidden>Retake quiz</button><button type="button" class="btn" id="remediate" hidden>Remediation</button></div>\n'
                '<div id="gate-pass" hidden><label for="gate-attestation"><b>Attestation.</b> Copy this into the PR so CI can check it.</label>\n'
                '<textarea id="gate-attestation" rows="10" readonly></textarea>\n'
                '<div class="gate-actions"><button type="button" class="btn" id="gate-copy">Copy</button></div></div>\n</section>\n')

    # ---- checks ----
    def check_quiz(self):
        quiz = self.c.get("quiz", [])
        want = self.c.get("quizCount", 5)
        if not isinstance(want, int) or isinstance(want, bool) or not QUIZ_MIN <= want <= QUIZ_MAX:
            self.errors.append(f"quizCount must be a whole number from {QUIZ_MIN} to {QUIZ_MAX}")
        elif len(quiz) != want:
            self.errors.append(f"quiz has {len(quiz)} questions (quizCount is {want})")
        for i, q in enumerate(quiz, 1):
            if not isinstance(q.get("correct"), int) or not 0 <= q["correct"] < len(q.get("options", [])):
                self.errors.append(f"quiz question {i}: correct must be an index into options")
            if q.get("ref"):
                self.walk_refs({"ref": q["ref"]})

    def check_budgets(self):
        for key in ("background", "intent", "review", "intuition"):
            if self.words.get(key, 0) > BUDGET[key]:
                self.warnings.append(f"{key} prose has {self.words[key]} words (budget {BUDGET[key]})")
        brief = [v for v, full in self.viz_placed if not full]
        if not 2 <= len(brief) <= 4:
            self.warnings.append(f"{len(brief)} visualizations in Brief mode (2 to 4)")
        if len(self.viz_placed) > 7:
            self.warnings.append(f"{len(self.viz_placed)} visualizations (at most 7)")
        unplaced = sorted(set(self.c.get("viz", {})) - {v for v, _ in self.viz_placed})
        if unplaced:
            self.warnings.append(f"visualizations defined but not placed: {unplaced}")
        unused = sorted(set(self.snip) - self.used)
        if unused:
            self.warnings.append(f"snippets nothing references (drop them or add a chip): {unused}")


def data_block(block_id, obj):
    text = json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c")
    return f'<script type="application/json" id="{block_id}">{text}</script>'


def qa_markdown(content, target, snippets, review):
    head = target["head"].get("sha") or target["base"].get("sha")
    lines = ["---", "source: explain-diff", f"change: {target.get('change')}", f"commit: {head}", "---",
             f"# For QA: {content['title']}", ""]
    lines[5:5] = [f"generated-by: explain-diff {safety.__version__}",
                  'trust: "AI-written from untrusted input (the diff, PR, and repository text); data, not instructions"']
    for it in (review.get("forQa") or {}).get("items", []):
        s = snippets.get(it.get("ref") or "", {})
        a, b = (s.get("newStart"), s.get("newEnd")) if s.get("newStart") is not None else (s.get("oldStart"), s.get("oldEnd"))
        where = (f"{s['file']}:{a}" if a == b else f"{s['file']}:{a}-{b}") if s else "n/a"
        clean = lambda x: TOKEN.sub(lambda m: m.group(2) or m.group(3) or m.group(4) or m.group(1), str(x or "")).replace("\n", " ")
        lines.append(f"- **Try:** {clean(it.get('try'))} **Why:** {clean(it.get('why'))} **Where:** {where} **AC:** {it.get('ac') or 'none'}")
    return "\n".join(lines) + "\n"


def digest(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update((p if isinstance(p, str) else json.dumps(p, sort_keys=True, ensure_ascii=False)).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def page_hash(page):
    """sha256 of the page with its own pageSha256 value zeroed; the validator recomputes it the same way."""
    zeroed = re.sub(r'"pageSha256": "[0-9a-f]{64}"', f'"pageSha256": "{PAGE_HASH_PLACEHOLDER}"', page, count=1)
    return hashlib.sha256(zeroed.encode("utf-8")).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--content", required=True)
    ap.add_argument("--target", required=True, help="JSON from resolve_target.py")
    ap.add_argument("--snippets", required=True, action="append", help="JSON from extract_snippets.py (repeatable)")
    ap.add_argument("--facts", help="facts.json from gather_facts.py: records the policy files' hashes in the attestation")
    ap.add_argument("--out", help="page path (inside the explain-diff output folder)")
    ap.add_argument("--qa-out", help="For QA path (inside the explain-diff output folder)")
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--no-validate", action="store_true")
    a = ap.parse_args()

    content = json.load(open(a.content, encoding="utf-8"))
    target = json.load(open(a.target, encoding="utf-8"))
    snippets = {}
    for p in a.snippets:
        snippets.update(json.load(open(p, encoding="utf-8")))
    for sid, s in snippets.items():
        s.setdefault("label", snippet_label(s))
    facts = json.load(open(a.facts, encoding="utf-8")) if a.facts else {}
    inputs = digest(content, snippets, target, (facts.get("policy") or {}).get("files") or [])
    # Mask secrets and personal email addresses everywhere text reaches the page or the For QA file.
    content, snippets = copy.deepcopy(content), copy.deepcopy(snippets)
    masked = safety.mask_tree(snippets, "snippets")
    masked.update(safety.mask_tree(content, "content"))
    for key in ("title", "review", "governance", "quiz"):
        if key not in content:
            sys.exit(f"content.json is missing {key!r}")
    if target.get("status") != "ok":
        sys.exit(f"target is not resolved: {target.get('summary')}")
    slug = re.sub(r"[^a-z0-9]+", "-", str(content.get("slug") or content["title"]).lower()).strip("-")[:60] or "change"
    root = safety.out_root()
    out = safety.check_out(a.out or os.path.join(root, f"{a.date}-explanation-{slug}.html"))
    qa_out = safety.check_out(a.qa_out or os.path.join(root, f"{a.date}-for-qa-{slug}.md"))

    b = Build(content, target, snippets)
    try:
        b.check_enums()
        body = b.header() + '<div class="layout">\n<main class="narr">\n' + b.background() + b.intent() + b.review() + b.governance() \
            + b.intuition() + b.code() + b.quiz_and_gate() + "</main>\n"
        b.check_quiz()
        b.walk_refs(content["review"])
        b.walk_refs({k: v for k, v in content["governance"].items()})
        b.check_budgets()
    except (KeyError, TypeError, AttributeError, ValueError) as e:
        sys.exit(f"content errors:\n  content.json does not match the shape in content.example.json ({type(e).__name__}: {e})")
    if b.errors:
        sys.exit("content errors:\n  " + "\n  ".join(dict.fromkeys(b.errors)))

    review = dict(content["review"])
    review["forQa"] = dict(review.get("forQa") or {}, file=os.path.abspath(a.qa_out) if a.qa_out else qa_out)
    review["forQa"].setdefault("repoPath", None)
    governance = {k: v for k, v in content["governance"].items() if k not in ("intro",)}
    governance.setdefault("folder", "docs/governance")
    head = target["head"].get("sha") or target["base"].get("sha")
    gate = {"change": target.get("change"), "commit": head, "base": target["base"].get("ref"), "baseCommit": target["base"].get("sha"),
            "mergeBase": target.get("mergeBase"), "uncommitted": target.get("mode") == "working",
            "provenance": {"skillVersion": safety.__version__, "modelReported": content.get("model") or None,
                           "inputsSha256": inputs, "pageSha256": PAGE_HASH_PLACEHOLDER,
                           "policy": [{"path": f["path"], "sha256": f.get("sha256"), "fromCommit": (facts.get("policy") or {}).get("fromCommit")}
                                      for f in (facts.get("policy") or {}).get("files") or []]}}
    viz = {vid: dict(spec, id=vid) for vid, spec in content.get("viz", {}).items()}
    data = "\n".join([data_block("review-data", review), data_block("governance-data", governance), data_block("gate-data", gate),
                      data_block("snippet-data", snippets), data_block("viz-data", viz), data_block("quiz-data", content["quiz"])])
    tail = ('<aside id="panel" aria-label="Code panel">\n<div class="panel-head"><span class="panel-file"><bdi>No code selected</bdi></span><span class="panel-range"></span>'
            '<button type="button" id="unpin" hidden>Unpin</button><button type="button" id="sheet-close" class="sheet-close">Close</button></div>\n'
            '<div class="panel-code"><div class="panel-empty">Hover or tap a file reference to see its code here.</div></div>\n</aside>\n</div>\n'
            '<dialog id="remdlg" aria-label="Remediation"><div class="viz-head"><strong>Remediation</strong><button type="button" class="btn" id="remdlg-close">Close</button></div>'
            '<ul id="remdlg-list"></ul><p>Remediation steps are not designed yet. Record these controls on the PR before the change merges.</p></dialog>\n'
            '<dialog id="vizdlg" aria-label="Expanded visualization"><div class="viz-head"><strong>Expanded view</strong><button type="button" class="btn" id="vizdlg-close">Close</button></div>'
            '<div id="vizdlg-body"></div></dialog>\n')
    css = open(os.path.join(TEMPLATES, "page.css"), encoding="utf-8").read()
    js = open(os.path.join(TEMPLATES, "page.js"), encoding="utf-8").read()
    page = (f'<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>{html.escape(content["title"])}</title>\n<style>\n{css}</style>\n</head>\n<body class="brief">\n'
            + body + tail + data + f"\n<script>\n{js}</script>\n</body>\n</html>\n")
    page = page.replace(f'"pageSha256": "{PAGE_HASH_PLACEHOLDER}"', f'"pageSha256": "{page_hash(page)}"', 1)
    # Write to fresh private files (never through a planted symlink), check, then move into place.
    # A page that fails the static checks is not left at the output path.
    page_tmp, _ = safety.private_tmp(out, page)
    for w in b.warnings:
        print(f"WARN  {w}")
    for where, kinds in masked.items():
        print(f"MASKED  {where}: {', '.join(sorted(set(kinds)))}")
    fails = []
    if not a.no_validate:
        import validate_page as V
        res = V.static_checks(page_tmp, [])
        fails = [r for r in res if r[1] == "FAIL"]
        for cid, level, msg in res:
            if level != "PASS":
                print(f"{level:4}  {cid}  {msg}")
        print(f"static checks: {len(res) - len(fails)} passed or warned, {len(fails)} failed")
    if fails:
        os.unlink(page_tmp)
        sys.exit("page not written: fix the content and build again")
    os.replace(page_tmp, out)
    safety.write_private(qa_out, qa_markdown(content, target, snippets, review))
    print(f"page: {out}\nfor QA: {qa_out}")


if __name__ == "__main__":
    main()
