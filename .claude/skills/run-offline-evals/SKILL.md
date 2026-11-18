---
name: run-offline-evals
description: Run every offline check for the explain-diff skill (resolver, page template and build, hostile input, graders, model-eval dry run, example page). Use after changing anything under plugins/explain-diff/ or evals/, or before committing.
---

# Run the offline evals

Run from the repository root. Browser parts need Playwright; uv supplies a pinned copy.

```
uv run -q --with playwright==1.65.2 python3 evals/template_test.py
python3 evals/target_test.py
python3 evals/facts_test.py
uv run -q --with playwright==1.65.2 python3 evals/hostile_snippet_test.py
python3 evals/model/test_graders.py
python3 evals/model/run.py --dry-run --all --out "$(mktemp -d)"
python3 evals/validate_page.py docs/example-explanation.html --browser --canary evil.example --canary __pwned
```

Each prints a last line with its failure count; all must be 0 (the dry run must exit 0).

If `templates/` changed, rebuild the example page first, then run the list above:

```
S=plugins/explain-diff/skills/explain-diff
python3 $S/scripts/build_page.py --content $S/templates/content.example.json --target evals/fixtures/example/target.json \
  --snippets evals/fixtures/example/snippets.json --out docs/example-explanation.html \
  --qa-out /tmp/2026-11-02-for-qa-retry-backoff.md --date 2026-11-02
```

Report each suite's last line. These do not run the live model evals (`evals/model/run.py` without `--dry-run`), which cost tokens; say so.
