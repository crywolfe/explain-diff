# Enforcing the gate in GitHub

The explain-diff page has a Gate: it stays closed until the reviewer answers every quiz question right (five by default; `--quiz N` sets 3 to 10) in one attempt and marks every governance control, with none Not met. On its own the page cannot stop anyone approving a pull request. It is an offline file with no network access, and GitHub has no hook that blocks the Approve button. What GitHub can block is the merge. This page describes how to do that. Nothing here is installed by the plugin; [`docs/gate/`](gate/) has a workflow you can copy, with its checks in a tested module.

## What the page gives you

When the gate passes, the page shows an attestation the reviewer copies:

```json
{
  "source": "explain-diff",
  "change": "retry-backoff",
  "commit": "<full head sha>",
  "base": "origin/main",
  "baseCommit": "<base sha the page compared against>",
  "mergeBase": "<the commit the diff starts from>",
  "uncommitted": false,
  "quiz": { "correct": 5, "of": 5, "attempt": 2 },
  "governance": [{ "id": "GOV-4", "mark": "met" }, { "id": "GOV-7", "mark": "na" }],
  "governanceDocuments": ["docs/governance/GOVERNANCE.md"],
  "provenance": {
    "skillVersion": "1.5.0",
    "modelReported": "claude-opus-5-5",
    "inputsSha256": "<hash of the content, snippets, target and policy the page was built from>",
    "pageSha256": "<hash of the page as built>",
    "policy": [{ "path": "REVIEW.md", "sha256": "<file hash>", "fromCommit": "<base sha>" }]
  },
  "at": "2026-11-12T18:04:11Z"
}
```

The reviewer posts it as a PR comment that starts with the line `explain-diff attestation`, followed by the JSON in a code block. The skill never posts it; the reviewer does, under their own GitHub account.

## Enforcement

1. **A required status check.** A workflow runs on `issue_comment` and `pull_request_target` (`opened`, `synchronize`, `reopened`). It reads the attestation comments and sets a commit status `explain-diff/gate` on the head commit:
   - `success` when someone with write access to the repository, other than the PR's author, posted a valid attestation (the workflow below; an earlier design also required that person to have approved, which needs the untrusted `pull_request_review` event).
   - `failure` otherwise, with the reason.
   An attestation is valid when its author is the approving reviewer (GitHub supplies the author, so it cannot be forged in the JSON), `commit` equals the PR's head sha, `base` names the PR's base branch and `mergeBase` equals the merge-base of the PR's base and head (so the reviewer read the PR's own diff, not one against a stale or wrong branch), `uncommitted` is false, `quiz.correct` equals `quiz.of` (a repository that wants a minimum can also require `quiz.of` of at least that number), and no governance mark is `notmet`. When the repository has files in `docs/governance/`, the attestation must list at least one of them.
2. **Branch protection or a ruleset** on the default branch: require `explain-diff/gate`, require at least one approval, and dismiss stale approvals when new commits are pushed. A new push changes the head sha, so the old attestation stops counting and the reviewer runs the page again.
3. **Optional: dismiss bare approvals.** On `pull_request_review` with state `approved`, if the reviewer has no valid attestation for the head sha, the workflow dismisses the review with a message such as "Approval dismissed: post the explain-diff attestation for <sha> first." This is the closest GitHub gets to "you cannot approve without the quiz". It needs a token with `pull-requests: write`. It is not implemented in `docs/gate/`: `pull_request_review` runs the workflow file from the PR, so a dismissal workflow must not trust anything the PR can change.

### The workflow

[`docs/gate/explain-diff-gate.yml`](gate/explain-diff-gate.yml) implements step 1. Install it by copying it to `.github/workflows/` and [`docs/gate/validate.js`](gate/validate.js) to `.github/explain-diff-gate/validate.js` on the default branch, then require the `explain-diff/gate` status (step 2).

- The checks are in `validate.js`, a pure function tested by `node --test docs/gate/validate.test.js` (in this repository's CI). The workflow only gathers facts from GitHub: the head sha, the merge-base against the base branch as it is now, the governance files at the base, and the sha256 of REVIEW.md and each governance file at the base.
- Beyond the checks above, it requires `provenance` and that its `policy` hashes equal the base's: the reviewer used the rules in force, not a version the PR rewrote. If the base's rules change after the attestation, the reviewer runs the page again.
- It runs only on `pull_request_target` and `issue_comment`, which run the default branch's copy of the workflow, and it never checks out the PR's code. It does not run on `pull_request_review`: that event runs the workflow file from the PR, so a same-repository branch could edit the check and post a passing status, and on fork PRs it has a read-only token.
- Because approvals do not trigger it, the status does not tie the attestation to the approver. It passes on a valid attestation from someone with write access who did not open the PR. Branch protection requires the approval separately; to keep the two together, ask reviewers to post the attestation and approve in the same sitting, and audit the pair from the PR timeline.
- In the ruleset, require `explain-diff/gate` from the **GitHub Actions** app, so a status posted any other way does not count.
- The status description names the first reason the latest attestation failed.

## Limits

- The quiz answers are in the page, and the attestation is text the reviewer could write by hand (the page hash only shows whether a given page file was edited, and only to someone who has that file). The gate checks attention, not knowledge. What the workflow does guarantee is a record of who attested to what, for which commit, signed by their GitHub identity. That record is what an audit uses.
- Making the quiz itself hard to bypass needs a server that holds the answers and issues a signed result. That conflicts with the page being an offline file, and is out of scope here.
- Governance marks are the reviewer's judgment. The workflow checks that none is Not met; it cannot check that a Met is true. Controls whose sign-off names a second role (security lead, data owner) still need that person, for example through CODEOWNERS on the affected paths.
- Remediation for a Not met control is not designed yet. The page's Remediation button lists the failed controls and says to record them on the PR.
