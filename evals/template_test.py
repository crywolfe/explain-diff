#!/usr/bin/env python3
"""Tests the page template and build script that ship with the skill.

1. Build the example page from templates/content.example.json and the sample
   target and snippets in evals/fixtures/example/, then run the validator.
2. Build a page that uses every visualization type (stepper, graph, sequence,
   compare, scrubber, map, chart) and confirm each one renders without a
   "failed to load" message and that its controls respond.
3. Build a page whose prose, labels, file names, quiz and governance text carry
   hostile payloads, and confirm nothing runs and the canaries stay inert.
4. Content errors stop the build: an unknown chip id, a missing visualization,
   and a quiz with four questions.

Usage: python3 template_test.py [--static]   (--static skips the browser parts)
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(HERE, "..", "plugins", "explain-diff", "skills", "explain-diff")
BUILD = os.path.join(SKILL, "scripts", "build_page.py")
FIX = os.path.join(HERE, "fixtures", "example")
STATIC = "--static" in sys.argv[1:]
sys.path.insert(0, HERE)
import validate_page as V  # noqa: E402

tmp = tempfile.mkdtemp(prefix="explain-diff-template-")
failures = []
CANARIES = ["evil.example", "__pwned"]
HOSTILE = "</script><script>window.__pwned=1</script><img src=x onerror=window.__pwned=1> <a href=\"https://evil.example/x\">x</a>"


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def base_content():
    return json.load(open(os.path.join(SKILL, "templates", "content.example.json"), encoding="utf-8"))


def build(name, content, snippets=None):
    cp = os.path.join(tmp, name + ".json")
    json.dump(content, open(cp, "w", encoding="utf-8"))
    sp = os.path.join(FIX, "snippets.json")
    if snippets is not None:
        sp = os.path.join(tmp, name + "-snippets.json")
        json.dump(snippets, open(sp, "w", encoding="utf-8"))
    out = os.path.join(tmp, name + ".html")
    p = subprocess.run([sys.executable, BUILD, "--content", cp, "--target", os.path.join(FIX, "target.json"), "--snippets", sp,
                        "--out", out, "--qa-out", os.path.join(tmp, name + "-qa.md")], capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr, out


def fails(res):
    return [f"{c} {m}" for c, lvl, m in res if lvl == "FAIL"]


def all_viz(c):
    c["viz"]["deps"] = {"type": "graph", "title": "Who calls what", "caption": "Select a node to see what it uses and what uses it.",
                        "nodes": [{"id": "fp", "label": "fetch_page", "ref": "s_loop", "row": 0, "col": 0, "changed": True},
                                  {"id": "bd", "label": "backoff_delay", "ref": "s_delay", "row": 1, "col": 0, "changed": True},
                                  {"id": "fe", "label": "FetchError", "ref": "s_error", "row": 1, "col": 1},
                                  {"id": "sl", "label": "sleep", "row": 2, "col": 0, "detail": "time.sleep by default"}],
                        "edges": [{"from": "fp", "to": "bd"}, {"from": "fp", "to": "fe"}, {"from": "fp", "to": "sl", "dashed": True}]}
    c["viz"]["seq"] = {"type": "sequence", "title": "One retried request", "caption": "Messages for the 503, 503, 200 case.",
                       "actors": [{"id": "c", "label": "caller"}, {"id": "f", "label": "fetch_page"}, {"id": "s", "label": "server"}],
                       "messages": [{"from": "c", "to": "f", "label": "fetch_page(url)"}, {"from": "f", "to": "s", "label": "GET", "ref": "s_loop"},
                                    {"from": "s", "to": "f", "label": "503", "dashed": True}, {"from": "f", "to": "f", "label": "sleep 0.5s", "ref": "s_delay"},
                                    {"from": "f", "to": "s", "label": "GET"}, {"from": "s", "to": "f", "label": "200", "dashed": True},
                                    {"from": "f", "to": "c", "label": "body", "note": "returned on the third call"}]}
    c["viz"]["shape"] = {"type": "compare", "title": "FetchError before and after", "caption": "The error gains an attempts field.",
                         "before": {"FetchError": {"args": ["message"]}},
                         "after": {"FetchError": {"args": ["message"], "attempts": 1}},
                         "marks": [{"path": "FetchError.attempts", "kind": "add", "note": "defaults to 1"}]}
    c["viz"]["scrub"] = {"type": "scrubber", "title": "Waits for 503 on every attempt", "caption": "The waits list after each attempt.",
                         "stages": [{"name": "attempt 1", "value": [0.5], "ref": "s_delay"}, {"name": "attempt 2", "value": [0.5, 1.0]},
                                    {"name": "attempt 3", "value": [0.5, 1.0, 2.0]}, {"name": "attempt 4", "value": {"raised": "FetchError", "attempts": 4}, "note": "no wait after the last"}]}
    c["code"]["groups"][0]["visual"] = "shape"
    c["code"]["fullVisuals"] = ["waits", "deps", "seq", "scrub"]
    return c


VIZ_JS = """()=>{
 const out={fails:document.querySelectorAll('.fail').length,hosts:{}};
 document.querySelectorAll('[data-viz]').forEach(h=>{out.hosts[h.dataset.spec]={svg:h.querySelectorAll('svg').length,alt:h.querySelectorAll('details.alt').length,kids:h.children.length}});
 const click=e=>e&&e.dispatchEvent(new MouseEvent('click',{bubbles:true}));
 const seq=document.querySelector('[data-spec="seq"]');click([...seq.querySelectorAll('button')].find(b=>b.textContent==='Next'));
 out.seqCap=seq.querySelector('.stepcap').textContent;
 const g=document.querySelector('[data-spec="deps"] g.node');click(g);out.graphDetail=document.querySelector('[data-spec="deps"] .graph-detail').textContent;
 out.near=document.querySelectorAll('[data-spec="deps"] g.node.near').length;
 const cmp=document.querySelector('[data-spec="shape"]');click([...cmp.querySelectorAll('button')].find(b=>b.textContent==='After only'));
 out.cmpHidden=[...cmp.querySelectorAll('.side')].map(s=>s.hidden);out.cmpAdd=cmp.querySelectorAll('.mk-add').length;
 const r=document.querySelector('[data-spec="scrub"] input[type=range]');r.value='3';r.dispatchEvent(new Event('input'));
 out.scrub=document.querySelector('[data-spec="scrub"] .stage').textContent;
 const st=document.querySelector('[data-spec="flow"]');click([...st.querySelectorAll('button')].find(b=>b.textContent==='Next'));
 out.stepCount=st.querySelector('.count').textContent;
 return out}"""


def main():
    # 1 example page
    rc, log, page = build("example", base_content())
    check("example builds", rc == 0, log.strip().splitlines()[-1] if log.strip() else "")
    res = V.run(page, browser=not STATIC, canaries=CANARIES)
    check("example passes the validator", not fails(res), "; ".join(fails(res)[:3]))

    # 2 every visualization type
    rc, log, page = build("all-viz", all_viz(base_content()))
    check("all visualization types build", rc == 0, log.strip()[-300:])
    res = V.run(page, browser=not STATIC, canaries=CANARIES)
    check("all-viz page passes the validator", not fails(res), "; ".join(fails(res)[:3]))
    if not STATIC and rc == 0:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = V.launch(pw)
            pg = b.new_page(viewport={"width": 1400, "height": 900})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto("file://" + page)
            o = pg.evaluate(VIZ_JS)
            b.close()
        check("no visualization failed to load", o["fails"] == 0 and not errs, f"{o['fails']} fail boxes; {errs[:2]}")
        for vid in ("flow", "deps", "seq", "shape", "scrub"):
            h = o["hosts"].get(vid, {})
            check(f"{vid} rendered with a text version", h.get("kids", 0) > 1 and h.get("alt", 0) >= 1, str(h))
        check("sequence steps", o["seqCap"].startswith("2. GET"), o["seqCap"])
        check("graph select highlights neighbours", o["near"] >= 1 and "Uses" in o["graphDetail"], o["graphDetail"])
        check("compare one-sided view", o["cmpHidden"] == [True, False] and o["cmpAdd"] == 1, str(o["cmpHidden"]))
        check("scrubber moves", o["scrub"].startswith("Stage 4 of 4"), o["scrub"])
        check("stepper steps", o["stepCount"].startswith("Step 2 of"), o["stepCount"])

    # 3 hostile content everywhere the model writes text
    c = all_viz(base_content())
    c["title"] = "Title " + HOSTILE
    c["tldr"] = "TL;DR " + HOSTILE + " `" + HOSTILE + "` [[s_loop|" + HOSTILE + "]]"
    c["intent"]["what"]["text"] = HOSTILE
    c["intent"]["what"]["source"] = HOSTILE
    c["review"]["whereToLook"][0]["reasons"] = [HOSTILE]
    c["review"]["findings"] = [{"author": HOSTILE, "text": HOSTILE}]
    c["governance"]["controls"][0]["requirement"] = HOSTILE
    c["governance"]["controls"][0]["evidence"] = HOSTILE
    c["viz"]["flow"]["nodes"][0]["label"] = HOSTILE
    c["viz"]["map"]["nodes"][0]["label"] = HOSTILE
    c["viz"]["waits"]["bars"][0]["label"] = HOSTILE
    c["viz"]["shape"]["after"]["FetchError"][HOSTILE] = HOSTILE
    c["viz"]["seq"]["messages"][0]["label"] = HOSTILE
    c["quiz"][0]["explain"] = HOSTILE
    c["viz"]["waits"]["unit"] = HOSTILE
    c["governance"]["controls"][0]["location"] = HOSTILE
    snips = json.load(open(os.path.join(FIX, "snippets.json"), encoding="utf-8"))
    snips["s_loop"]["file"] = "<b>evil</b>/" + HOSTILE + ".py"
    snips["s_loop"]["lines"][0]["text"] = HOSTILE
    rc, log, page = build("hostile", c, snips)
    check("hostile content builds", rc == 0, log.strip()[-300:])
    res = V.run(page, browser=not STATIC, canaries=CANARIES)
    bad = [f for f in fails(res) if not f.startswith("S13")]
    check("hostile page: nothing runs, canaries inert", not bad, "; ".join(bad[:3]))

    # 3b enum fields are checked, not echoed into attributes
    for path, val in ((("review", "whereToLook", 0, "risk"), 'x" onmouseover="window.__pwned=1" data-x="'),
                      (("review", "ci"), HOSTILE), (("review", "undo", "kind"), HOSTILE), (("governance", "controls", 0, "id"), HOSTILE)):
        c = base_content()
        node = c
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = val
        rc, log, page = build("enum-" + path[-1], c)
        check(f"hostile {'.'.join(map(str, path))} stops the build and writes no page", rc != 0 and not os.path.exists(page), log.strip()[-120:])

    # 3c a data key named ref inside a visualization is data, not a snippet reference
    c = all_viz(base_content())
    c["viz"]["shape"]["after"]["FetchError"]["ref"] = "main"
    rc, log, _ = build("data-ref", c)
    check("a 'ref' key inside compare data is not a snippet reference", rc == 0, log.strip()[-160:])

    # 3d prototype-named control ids and a second Expand click
    c = base_content()
    c["governance"]["controls"][0]["id"] = "constructor"
    rc, log, page = build("proto", c)
    if not STATIC and rc == 0:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = V.launch(pw)
            pg = b.new_page(viewport={"width": 1400, "height": 900})
            pg.goto("file://" + page)
            o = pg.evaluate("""async()=>{const s=document.getElementById('gov-summary').textContent;
              const btn=document.querySelector('.viz .expand'),v=btn.closest('.viz');btn.click();btn.click();
              document.getElementById('vizdlg-close').click();await new Promise(r=>setTimeout(r,100));
              return {summary:s,stuck:document.getElementById('vizdlg').contains(v),open:document.getElementById('vizdlg').open}}""")
            b.close()
        check("a control id like 'constructor' loads unmarked", o["summary"].startswith("0 of"), o["summary"])
        check("Expand twice then Close puts the visualization back", not o["stuck"] and not o["open"], str(o))

    # 4 content errors stop the build
    c = base_content()
    c["code"]["groups"][0]["body"] = ["see [[no_such_snippet]]"]
    rc, log, _ = build("bad-ref", c)
    check("unknown chip id stops the build", rc != 0 and "no_such_snippet" in log, log.strip()[-120:])
    c = base_content()
    c["intuition"]["visual"] = "missing"
    rc, log, _ = build("bad-viz", c)
    check("missing visualization stops the build", rc != 0 and "missing" in log, log.strip()[-120:])
    c = base_content()
    c["quiz"] = c["quiz"][:4]
    rc, log, _ = build("bad-quiz", c)
    check("four quiz questions stop the build", rc != 0 and "4 questions" in log, log.strip()[-120:])
    c = base_content()
    c["quiz"], c["quizCount"] = c["quiz"][:3], 3
    rc, log, page = build("quiz-three", c)
    check("quizCount 3 with three questions builds", rc == 0, log.strip()[-160:])
    if rc == 0:
        html = open(page, encoding="utf-8").read()
        check("the page says all three, not all five", "all three" in html and "all five" not in html, "")
        res = V.run(page, browser=not STATIC, canaries=CANARIES)
        check("a three-question page validates, gate included", not fails(res), str(fails(res)))
    c = base_content()
    c["quizCount"] = 8
    rc, log, _ = build("quiz-mismatch", c)
    check("five questions with quizCount 8 stop the build", rc != 0 and "quizCount is 8" in log, log.strip()[-120:])
    for bad in (2, 11, "5", True):
        c = base_content()
        c["quizCount"] = bad
        rc, log, _ = build("quiz-range", c)
        check(f"quizCount {bad!r} stops the build", rc != 0 and "3 to 10" in log, log.strip()[-120:])
    c = base_content()
    c["intent"]["what"] = "a bare string"
    rc, log, _ = build("bad-shape", c)
    check("malformed content is a content error, not a traceback", rc != 0 and "content errors" in log and "Traceback" not in log, log.strip()[-160:])
    c = base_content()
    c["slug"] = "../../etc/x y"
    rc, log, _ = build("bad-slug", c)
    check("a slug with path characters is sanitized", rc == 0, log.strip()[-120:])

    print(f"\n{len(failures)} failed" + (f": {failures}" if failures else ""))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
