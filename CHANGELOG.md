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
- **Run guard.** The plugin installs a PreToolUse, UserPromptSubmit and SessionStart hook (`scripts/run_guard.py`) that, during a
  run, allows only the skill's own scripts (checked byte for byte), read-only `git` and `gh`, read-only utilities
  confined to the repository, and writes inside the run's folder. It denies network, MCP, other skills, and running
  any other program, logs every tool call, and makes Claude Code ask before either commit script runs. Every
  command's options are an exact allowlist; path arguments must resolve inside the repository; the guard fails
  closed on any error and protects its own state.
- **A run lasts until `/clear`.** Building the page no longer ends the run, and there is no idle timeout: the
  change's text stays in the conversation, so an instruction in it could act on any later turn. The run ends on
  `/clear` (a SessionStart hook) or `/explain-diff:explain-diff stop`, which warns that the text is still in context.
  Compaction and resume keep it, and if the run's state file is gone (temp folders are cleaned on reboot), resume
  and the next prompt re-arm the run from the session's transcript. This changes behaviour: after a review, run
  `/clear` before other work.
- **Commits need someone to ask.** In dontAsk and bypassPermissions mode (and any mode the guard does not know),
  where Claude Code's question would never reach a person, the commit scripts are denied instead of asked. The eval
  harness gives consent with a `--consent=allow` argument on its own hook command, not an environment variable.
- **The repository's settings cannot switch the guard off.** The guard refuses every call while
  `EXPLAIN_DIFF_ANY_OUT` or `EXPLAIN_DIFF_GUARD_CONSENT` is in its environment (a checkout's `.claude/settings.json`
  `env` can set them). Outside a run, only a command that runs a commit script is asked or denied; one that
  only names it (`rg`, `git log`) is left alone. `resolve_target.py --work` starts a run only when the guard has just allowed that call for
  the same repository: the guard leaves a one-use note (`runs/ticket.<session>.json`, good for ten minutes, to allow
  for Claude Code's permission prompt) and the resolver takes it. With `disableAllHooks` there is no note, so the
  skill stops instead of running unguarded. This catches a guard that is switched off; it is not a boundary, since
  a model with no guard could write a note. The resolver also refuses a run with either override in its own
  environment, and warns when the change edits `.claude/`, `.mcp.json` or `CLAUDE.md` and this checkout has the
  change's version of them (uncommitted, at the head, or kept by later commits): Claude Code loaded those before
  the skill ran.
- **Edited attestations do not count.** The gate workflow ignores an attestation comment that was edited after it
  was posted (`fromComment` in `docs/gate/validate.js`); a user with write access can edit another user's comment.
- **`gh` reads only the repository under review.** The run guard no longer allows `--repo`, `--search` (its
  `repo:` qualifier), a PR URL or an `OWNER/REPO` argument: `gh pr view|diff|checks` take only a PR number, and
  `gh` runs only at the repository's own top level (not in a submodule, a vendored clone, or a folder around
  it). It is denied while `GH_REPO` or `GH_FORCE_TTY` (which makes gh start its pager, a program of the settings'
  choosing) is in the session's environment, and the scripts' own `gh` calls drop them and use no pager.
- **Run logs are masked and expire.** The guard's log masks secrets and personal email addresses in each tool
  input and reason, as the page does, and a run's start deletes logs older than 30 days.
- **Offline evals run only when you start them.** The repository's `run-offline-evals` skill runs this checkout's
  code, so the model can no longer start it on its own (`disable-model-invocation`), and it asks you to check
  whose code the branch holds first. It installs Playwright from the hashed CI requirements into a private folder,
  runs the suites without the session's tokens in their environment, reports a skipped browser check as not run,
  and deletes its folder when done.
- **Supported models.** A model is supported for a release only when its run record in `evals/RESULTS.md` meets its
  bar; `evals/meta_test.py` fails when the CHANGELOG marks a version released without one.
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
- **Run files are the scripts', not the model's.** The run guard allows Write and Edit only to `content.json` and
  `refs.json` in a run's folder (and REVIEW.md in the review-md-setup worktree). Each script reads an input only under
  the name its script writes (`target.json`, `facts.json`, `snippets.json`) and never writes another step's file or
  into `policy/`. Every script checks target.json (`safety.check_target`) before its values reach git, so a forged
  `diffArgs` cannot pass an option such as `--output=<path>`, and its file list must be the one git gives for
  `diffArgs`, so no changed file can be left out. The build requires `--facts` (except in skill maintenance), takes
  target, facts and snippets from one run folder, and recomputes each policy file's hash from git at the base commit.
- **No repository hooks from any git call.** Every script runs git through `safety.git()` (hooks and fsmonitor off,
  no external diff or textconv, paths always from the repository root even with `diff.relative=true`). Before,
  `git fetch` in `resolve_target.py` ran a `reference-transaction` hook from a tracked `core.hooksPath` folder.
  `meta_test.py` fails on any raw git call. The run guard denies `git status`, which refreshes the index and runs
  `post-index-change`, and any `git diff` it cannot show compares two commits or the index. A diff between commits
  must end in `--`, so git cannot read tracked files named like commit ids as paths. Its options must be plain flags
  or carry their value attached (`--opt=value`), and its revisions must use plain syntax: a ref or id with `~N`/`^N`,
  or one `A..B` or `A...B`. The model reads the change with the new `extract_snippets.py --diff`. `guard_test.py`
  runs the allow-list git commands, and two dozen diff forms that fool a check by spelling, in a clone with a tracked
  hooks folder, and fails if any command is both allowed and runs a hook.
- **Evals.** New offline suites (`guard_test.py`, `safety_test.py`, `meta_test.py`) and five model cases
  (`policy-weakening`, `no-run-pr-code`, `missing-yes-repo-review-init`, `inject-run-files`, `inject-after-run`); model runs load the run guard and scrub
  credentials from subprocesses.

## 1.4.0

Gather facts before exploring (`gather_facts.py`); configurable quiz length (`--quiz N`); stop when the page passes.

## 1.3.0

Build pages from a fixed template; resolve the target and base with a script.

## 1.2.0

Governance section, quiz and governance gate, and governance evals; evals in GitHub Actions.

## 1.1.0

Review guide (where to look, coverage, root cause, undo, REVIEW.md rules, For QA).
