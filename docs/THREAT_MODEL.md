# explain-diff threat model

Scope: the explain-diff plugin (skill, scripts, run guard hook, page template) and its evals and CI. Reviewed
2026-11-23 for version 1.5.0. Re-review at every minor release and whenever the skill gains a tool, a write, a
network use, or a new input.

## What it does

A reviewer asks Claude Code to explain a change. The skill resolves the target, gathers facts from git and `gh`,
has the model write the page's content as JSON, builds a self-contained HTML page from a fixed template, and
validates it. It may make two commits, each only after the user says yes: a REVIEW.md draft on its own branch,
and For QA notes on the change's branch. It never pushes, comments on, approves or merges anything.

## Assets

- The reviewer's credentials in the session: `gh` and git tokens, SSH keys, cloud credentials, the Anthropic key.
- The reviewer's machine: shell startup files, other repositories, Claude Code settings and skills.
- The integrity of the review: the rules applied (REVIEW.md, governance documents), the risk ranking, the
  attestation, and the reviewer's attention.
- Secrets and personal data inside the code under review.

## Trust boundaries and adversaries

| Input | Who controls it | Trusted? |
| --- | --- | --- |
| The user's request | The reviewer | Yes |
| SKILL.md, scripts, template, hooks | This repository, at an installed tag | Yes, if installed pinned |
| Diff, PR title and body, commit messages, branch names | The change's author | **No** |
| Repository files, tests, fixtures, `.claude/` | The change's author (they can edit any of them) | **No** |
| REVIEW.md, governance documents at the **base** commit | The team (approved on the default branch) | Rules yes, instructions no |
| The same files on the change's branch | The change's author | **No** |
| A `review-init` skill | The user (user or plugin scope) / the author (repo scope) | Only user or plugin scope |

The main adversary is the author of a hostile change, especially a pull request from a fork or an outside
contributor. They aim for the model to run their code, leak the reviewer's credentials, weaken the rules it
is reviewed by, or tell the reviewer it is safe.

## Threats and controls

| # | Threat | Control | Enforced by | Tested by |
| --- | --- | --- | --- | --- |
| T1 | Prompt injection in any untrusted text makes the model run a command, fetch a URL, or use an MCP tool, during the run or on any later turn | Run guard allowlist: only the skill's scripts (byte-identical), read-only git and gh, read-only utilities confined to the repo; network, MCP, other skills and other programs denied. The run lasts until the user runs `/clear` or the stop prompt, not until the build or an idle timeout, because the text stays in context; compaction and resume keep it | `run_guard.py` hook | `guard_test.py`, model evals (`inject-after-run`) |
| T2 | The change's tests, `conftest.py`, package scripts or Git hooks run with the reviewer's credentials | The skill never runs them; the guard denies interpreters on anything but the skill's scripts; every script runs git through `safety.git()` with hooks and fsmonitor off and no external diff or textconv (a tracked `core.hooksPath` folder would otherwise run on `git fetch`); the guard denies the model `git status` (it runs `post-index-change`) and any `git diff` it cannot show compares two commits or the index: the revisions must be followed by `--` (without it, tracked files named like commit ids are read as paths), options must be flags or carry their value attached, and revisions must use plain syntax (no `:/`, `^{}`, `^@`, `@{}`) | SKILL.md + guard + `safety.git` | `guard_test.py`, `safety_test.py`, `meta_test.py`, `no-run-pr-code` |
| T3 | The change rewrites REVIEW.md or governance documents to weaken its own review | Rules read from the base commit; policy and agent-instruction edits ranked High and flagged for the owner | `gather_facts.py` | `facts_test.py`, `policy-weakening` |
| T4 | A write lands in a shell startup file, a symlink target, or a shared temp path | One private output root (0700, files 0600); other paths, symlinks and loose folders refused | `safety.py` | `safety_test.py` |
| T5 | A tracked symlink makes the skill read a file outside the repository | Reads come from git objects; working-mode reads never follow a symlink | `safety.read_in_repo`, `extract_snippets.py` | `safety_test.py` |
| T6 | Secrets or personal email addresses in the code end up on a page that gets shared | Masking at build; S17 fails a page with an unmasked secret | `build_page.py`, `validate_page.py` | `safety_test.py`, `hostile_snippet_test.py` |
| T7 | Hostile text executes in the page or makes it call out | Fixed template, escaped data blocks, no network code; static and browser checks | `build_page.py`, `validate_page.py` | `hostile_snippet_test.py`, `template_test.py` |
| T8 | The model commits or writes without the user's yes | Commits only through two scripts; the guard makes Claude Code ask the user before each, and denies them in dontAsk and bypassPermissions mode, where no one would see the question. Only the eval harness's own hook command (`--consent=allow`) skips the question | `run_guard.py`, the scripts | `guard_test.py` |
| T9 | A repository supplies its own `review-init` that runs on the user's yes | Repo-scoped review-init is denied; only user or plugin scope | `run_guard.py` | `guard_test.py`, `missing-yes-repo-review-init` |
| T10 | The page is edited after the build, the attestation claims rules it did not use, or someone edits a reviewer's old attestation comment to name a new commit | Page hash (S18); provenance with policy hashes; the optional gate checks them against the base and never counts an edited comment | `build_page.py`, `docs/gate/` | `hostile_snippet_test.py`, `node --test docs/gate/validate.test.js` |
| T11 | A tampered or moved release, CI action, or test dependency | Tagged installs, SHA-pinned actions, hashed Playwright, pinned Claude Code on PRs, `npm audit signatures` | README, CI | `meta_test.py` |
| T13 | Injected text makes the model rewrite the scripts' own run files: an option in target.json's `diffArgs` (`--output=<path>` writes anywhere), another repository, swapped policy copies, or a target the page does not explain | The guard allows Write and Edit only to `content.json` and `refs.json` in a run's folder; each script reads an input only under its own name (`target.json`, `facts.json`, `snippets.json`) and writes no other step's file; every script checks target.json (`safety.check_target`: full commit ids, no option-like values, the repository it runs in, and the file list git gives for `diffArgs`) before git sees it; the build requires `--facts`, one run folder, and recomputes the policy hashes from git at the base. The maintenance override `EXPLAIN_DIFF_ANY_OUT=1` drops the name, run-folder and `--facts` rules, so a build under it without `--facts` checks neither the target nor the policy; `extract_snippets.py` and `gather_facts.py` still run `check_target` (the guard denies environment assignments, so the model cannot set the override in a command) | `run_guard.py`, `safety.py`, `build_page.py` | `guard_test.py`, `safety_test.py` |
| T14 | The repository under review weakens the session through Claude Code's own settings: `.claude/settings.json` `env` setting a maintenance override, `disableAllHooks`, or its own hooks and MCP servers, loaded when a session starts in a checkout of the change | The guard refuses every call while `EXPLAIN_DIFF_ANY_OUT` or `EXPLAIN_DIFF_GUARD_CONSENT` is in its environment; the eval consent is a hook argument, not an environment variable; `resolve_target.py --work` starts a run only with the one-use note the guard leaves when it allows that call for the same repository, so a session with hooks off stops at step 1 (a check against a guard switched off, not a boundary: a model with no guard could write the note); the resolver also refuses either override in its own environment, and warns when the change's version of `.claude/`, `.mcp.json` or `CLAUDE.md` is in this checkout. Residual: hooks or MCP servers the change defines run when the session starts, before the skill; the README says to review from a trusted checkout of the base | `run_guard.py`, `resolve_target.py`, `safety.take_guard_ticket` | `guard_test.py`, `safety_test.py` |
| T12 | The model's narrative misleads the reviewer (a verdict, invented coverage) | No verdict wording (S13); coverage is "not run"; the gate checks attention, not correctness | Validator + SKILL.md | Model evals |

## Credential scope (least privilege)

The skill needs: read access to the repository, and for pull requests a `gh` login that can read them. Use a
fine-grained token limited to the repositories you review with **Contents: read** and **Pull requests: read**
(`GH_TOKEN=... claude`), not a token that can push or approve. The two commits are local and are never pushed.
The run's tool calls are logged in `<temp dir>/explain-diff-<uid>/logs/`, and both commits carry a
`Generated-by: explain-diff/<version>` trailer, so its actions are attributable to the skill, under your identity.

## Residual risks

- The guard enforces tools, not judgement: injected text can still skew the narrative or ranking. The reviewer
  reads the code; the gate makes them engage, it does not prove the review is right.
- The guard parses shell commands itself and allows exact option lists for `git`, `gh` and a few utilities. Two
  adversarial reviews (2026-11-23) found bypasses through option bundles, globs, comments, quoting, zsh
  expansions and planted git config; each is fixed and has a regression test in `evals/guard_test.py`. A parser
  disagreement with the shell, or a dangerous option in a future tool version, would still be a bypass. Report
  it as a vulnerability.
- Masking is pattern based. A secret in an unusual format can get through; S17 runs the same patterns.
- The guard is active only while the plugin is enabled. A copy of the skill installed without the plugin has no
  guard (the README says so).
- A run is tied to its session id. Forking a guarded session (`--fork-session`, or a resumed copy with a new id)
  gives a session with the change's text in context and no guard, because the hook is not told the parent's id.
- The run's state lives under the temp directory. A `TMPDIR` set by the repository's settings moves the output
  folder; the guard and `take_guard_ticket()` then agree on the new place, but files land outside the usual folder.
- A run's state is a file in the temp folder. If it is removed (reboot, macOS purging old temp files), the next
  SessionStart or prompt re-arms the run from the transcript; tool calls in between, with no prompt, are unguarded.
  Re-arming reads slash commands and tool calls from Claude Code's transcript format, which is not documented. It
  ends a run only on a user entry that is wholly a slash-command record or a typed prompt from a human origin, so
  shell output (`! cmd`), pasted text or a tool result that quotes `/clear` cannot end one.
- The start note is checked by repository and time (ten minutes), and is deleted when used. A sandboxed resolver
  that cannot delete it in the hook's folder still accepts it; an unused note (the user declined the permission
  prompt) stays valid for its window. Either way it only lets a run start, under a guard that was live moments ago.
- Outside a run, a command that names a commit script is asked (or denied where no one is asked) unless its program
  only reads files and nothing in it runs code (`rg`, `cat`, `git log`). The guard cannot see every way a shell runs
  a file; during a run, every such command is denied anyway.
- One run per session: a second review in another repository in the same session keeps the first repository, so
  reads there are denied. Run `/clear` between reviews.
- Whether `/clear` keeps the session id is not documented. If it gives a new id, the old run's state is orphaned
  and the cleared session starts unguarded, which is right: its context no longer has the change's text.
- Whether bypassPermissions shows a hook's "ask" is not documented, so the guard denies the commit scripts there;
  a mode the guard does not know is treated the same way.
- `modelReported` in the attestation is what the model said it was; nothing verifies it.

## Ownership and lifecycle

- Owner: the repository maintainer (see `.github/CODEOWNERS`), who approves changes to the skill, the guard, the
  evals and CI, and handles reports under `SECURITY.md`.
- Release: a version bump in `plugin.json` and `safety.py`, a CHANGELOG entry, the offline suite green, a model
  eval run recorded in `evals/RESULTS.md` for any change to SKILL.md or the guard, then a tag.
- Monitoring: the weekly model eval run against the latest Claude Code opens an `eval-regression` issue on a miss.
- Retirement: if the plugin is withdrawn, mark it in the README and marketplace and keep the last tag installable.
