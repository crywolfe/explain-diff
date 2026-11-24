# Changelog

All notable changes to the explain-diff plugin. Versions match `plugins/explain-diff/.claude-plugin/plugin.json`
and the release tags (`v1.5.0`); install a tag with `/plugin marketplace add crywolfe/explain-diff#v1.5.0`.

## 1.5.0 (unreleased)

Security hardening from the November 2026 AI security and governance review.

- **Review rules come from the base commit.** `gather_facts.py` reads REVIEW.md and `docs/governance/` at the base
  commit and copies them as text into the run's folder, with each file's sha256. A change that edits a policy or
  agent-instruction file (REVIEW.md, governance documents, CODEOWNERS, CLAUDE.md, AGENTS.md, `.claude/`, SKILL.md,
  `.mcp.json`, prompt files) is ranked High as "policy file changed" and needs its owner's sign-off.
- **The change's code is never run.** The skill no longer runs the change's tests or code; coverage says "not run" and
  CI reports results.
- **Run guard.** The plugin installs a PreToolUse and UserPromptSubmit hook (`scripts/run_guard.py`) that, during a
  run, allows only the skill's own scripts (checked byte for byte), read-only `git` and `gh`, read-only utilities
  confined to the repository, and writes inside the run's folder. It denies network, MCP, other skills, and running
  any other program, logs every tool call, and makes Claude Code ask before either commit script runs. Every
  command's options are an exact allowlist; path arguments must resolve inside the repository; the guard fails
  closed on any error, protects its own state, ends after 30 idle minutes, and `/explain-diff:explain-diff stop`
  ends a run.
- **Private outputs.** Every file goes in `<temp dir>/explain-diff-<uid>` (0700, files 0600); the scripts refuse other
  output paths, symlinks and loose folders. Working-mode reads never follow a symlink out of the repository.
- **Masking.** The build masks tokens, keys, assigned passwords and personal email addresses on the page and in the
  For QA file, and names what it masked. The validator adds S17 (no unmasked secrets) and S18 (page hash).
- **Provenance.** The attestation records the skill version, the reported model, a hash of the inputs, the page hash,
  and the hash and commit of each policy file used. `docs/gate/` has a tested workflow that checks it.
- **Commits through scripts, with no hooks.** The For QA commit (`commit_for_qa.py`) and the REVIEW.md draft branch
  (`review_md_branch.py`) are the only commits, made with hooks and fsmonitor disabled so a tracked hooks folder
  never runs; review-init is never taken from the repository under review.
- **Supply chain.** CI actions are pinned by SHA (with Dependabot), Playwright installs from a hashed requirements
  file, Claude Code is pinned on pull requests, and `evals/meta_test.py` keeps versions and pins in sync.
- **Evals.** New offline suites (`guard_test.py`, `safety_test.py`, `meta_test.py`) and three model cases
  (`policy-weakening`, `no-run-pr-code`, `missing-yes-repo-review-init`); model runs load the run guard and scrub
  credentials from subprocesses.

## 1.4.0

Gather facts before exploring (`gather_facts.py`); configurable quiz length (`--quiz N`); stop when the page passes.

## 1.3.0

Build pages from a fixed template; resolve the target and base with a script.

## 1.2.0

Governance section, quiz and governance gate, and governance evals; evals in GitHub Actions.

## 1.1.0

Review guide (where to look, coverage, root cause, undo, REVIEW.md rules, For QA).
