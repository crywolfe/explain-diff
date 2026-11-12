# Enforcing the gate in GitHub

The explain-diff page has a Gate: it stays closed until the reviewer answers all five quiz questions right in one attempt and marks every governance control, with none Not met. On its own the page cannot stop anyone approving a pull request. It is an offline file with no network access, and GitHub has no hook that blocks the Approve button. What GitHub can block is the merge. This page describes how to do that. It is a design; nothing here is installed by the plugin.

## What the page gives you

When the gate passes, the page shows an attestation the reviewer copies:

```json
{
  "source": "explain-diff",
  "change": "retry-backoff",
  "commit": "<full head sha>",
  "quiz": { "correct": 5, "of": 5, "attempt": 2 },
  "governance": [{ "id": "GOV-4", "mark": "met" }, { "id": "GOV-7", "mark": "na" }],
  "governanceDocuments": ["docs/governance/GOVERNANCE.md"],
  "at": "2026-11-12T18:04:11Z"
}
```

The reviewer posts it as a PR comment that starts with the line `explain-diff attestation`, followed by the JSON in a code block. The skill never posts it; the reviewer does, under their own GitHub account.

## Enforcement

1. **A required status check.** A workflow runs on `issue_comment`, `pull_request_review`, and `pull_request` (`synchronize`). It reads the attestation comments and sets a commit status `explain-diff/gate` on the head commit:
   - `success` when at least one reviewer who approved the PR also posted a valid attestation.
   - `failure` otherwise, with the reason.
   An attestation is valid when its author is the approving reviewer (GitHub supplies the author, so it cannot be forged in the JSON), `commit` equals the PR's head sha, `quiz.correct` equals `quiz.of`, and no governance mark is `notmet`. When the repository has files in `docs/governance/`, the attestation must list at least one of them.
2. **Branch protection or a ruleset** on the default branch: require `explain-diff/gate`, require at least one approval, and dismiss stale approvals when new commits are pushed. A new push changes the head sha, so the old attestation stops counting and the reviewer runs the page again.
3. **Optional: dismiss bare approvals.** On `pull_request_review` with state `approved`, if the reviewer has no valid attestation for the head sha, the workflow dismisses the review with a message such as "Approval dismissed: post the explain-diff attestation for <sha> first." This is the closest GitHub gets to "you cannot approve without the quiz". It needs a token with `pull-requests: write`.

A sketch of step 1 (untested):

```yaml
name: explain-diff gate
on:
  issue_comment: { types: [created, edited] }
  pull_request_review: { types: [submitted, dismissed] }
  pull_request: { types: [synchronize, opened] }
permissions: { pull-requests: read, statuses: write, contents: read }
jobs:
  gate:
    if: github.event.issue.pull_request || github.event.pull_request
    runs-on: ubuntu-latest
    steps:
      - uses: actions/github-script@v7
        with:
          script: |
            const num = context.payload.pull_request?.number ?? context.payload.issue.number;
            const { owner, repo } = context.repo;
            const pr = (await github.rest.pulls.get({ owner, repo, pull_number: num })).data;
            const sha = pr.head.sha;
            const reviews = await github.paginate(github.rest.pulls.listReviews, { owner, repo, pull_number: num });
            const latest = {};
            for (const r of reviews) latest[r.user.login] = r.state;
            const approvers = Object.keys(latest).filter(u => latest[u] === 'APPROVED');
            const comments = await github.paginate(github.rest.issues.listComments, { owner, repo, issue_number: num });
            const valid = new Set();
            for (const c of comments) {
              if (!c.body.startsWith('explain-diff attestation')) continue;
              const m = c.body.match(/```(?:json)?\n([\s\S]*?)```/);
              try {
                const a = JSON.parse(m[1]);
                const ok = a.source === 'explain-diff' && a.commit === sha && a.quiz.correct === a.quiz.of
                  && (a.governance || []).every(g => g.mark === 'met' || g.mark === 'na');
                if (ok) valid.add(c.user.login);
              } catch (e) { /* not an attestation */ }
            }
            const passed = approvers.some(u => valid.has(u));
            await github.rest.repos.createCommitStatus({ owner, repo, sha, context: 'explain-diff/gate',
              state: passed ? 'success' : 'failure',
              description: passed ? 'An approver attested for this commit' : 'No approver has attested for this commit' });
```

## Limits

- The quiz answers are in the page, and the attestation is text the reviewer could write by hand. The gate checks attention, not knowledge. What the workflow does guarantee is a record of who attested to what, for which commit, signed by their GitHub identity. That record is what an audit uses.
- Making the quiz itself hard to bypass needs a server that holds the answers and issues a signed result. That conflicts with the page being an offline file, and is out of scope here.
- Governance marks are the reviewer's judgment. The workflow checks that none is Not met; it cannot check that a Met is true. Controls whose sign-off names a second role (security lead, data owner) still need that person, for example through CODEOWNERS on the affected paths.
- Remediation for a Not met control is not designed yet. The page's Remediation button lists the failed controls and says to record them on the PR.
