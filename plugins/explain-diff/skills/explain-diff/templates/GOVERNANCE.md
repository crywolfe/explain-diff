# GOVERNANCE.md

Governance controls for this repository. This is a sample: copy it into `docs/governance/` (the folder the explain-diff skill reads; Markdown, PDF and Word files there are all read), then change the paths, roles and controls to match your organization. The governance owner (for example the head of engineering or the compliance lead) approves changes to this file in a PR.

Version: 2026-11

## How a control is used

- Each control says when it applies, what the change must show, the evidence a reviewer checks, who signs off, and what failure means.
- When a change matches a control's "Applies when", the human reviewer marks the control **Met**, **Not met**, or **Does not apply** before the change merges.
- A control marked **Not met** blocks the merge until it is remediated. Remediation is recorded on the PR.
- An assistant may point out which controls a change touches and what evidence exists. It never marks a control, and it never says a change passes governance.

## GOV-1 Personal data

Applies when: the change reads, stores, logs, or sends personal data (names, emails, phone numbers, addresses, payment details), or touches `app/users.py`, `app/models/user*`, or `migrations/**`.
Requirement: the PR names each personal data field the change touches and its classification (Public, Internal, Confidential, Restricted). Restricted data is never written to logs or sent to a new destination.
Evidence: a data classification line in the PR description; no personal data in log or print calls in the diff.
Sign-off: reviewer. Restricted data also needs the privacy lead.
Failure means: personal data is logged, sent to a new destination, or stored without a classification.

## GOV-2 Third-party dependencies

Applies when: a dependency manifest or lockfile changes (`requirements*.txt`, `pyproject.toml`, `package.json`, `*.lock`, `go.mod`), or code vendors third-party source.
Requirement: every new or upgraded dependency is pinned to an exact version and its license is on the allowlist: MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC. GPL, AGPL, and unknown licenses need the governance owner's written exception.
Evidence: the pinned version in the manifest; the license from the package metadata.
Sign-off: reviewer. A license exception also needs the governance owner.
Failure means: an unpinned dependency, or a license off the allowlist without an exception.

## GOV-3 Secrets and credentials

Applies when: the change adds or reads a key, token, password, certificate, or connection string, or touches `config/**`, `.env*`, or CI secrets.
Requirement: secrets come from the secret manager at runtime. No secret value appears in code, config, tests, fixtures, or logs.
Evidence: secret references by name only; no high-entropy strings in the diff.
Sign-off: reviewer.
Failure means: a secret value is committed, logged, or passed through a command line.

## GOV-4 Calls to external services

Applies when: the change adds or changes a request to a host outside the company (HTTP clients, SDKs, webhooks, retries, timeouts).
Requirement: the vendor is in the vendor register. Every request has a timeout. Retries are bounded in count and total wait, and honor the vendor's rate limit headers.
Evidence: the timeout and retry limits in the code; the vendor's name in the register.
Sign-off: reviewer. A vendor not yet in the register also needs procurement.
Failure means: an unbounded retry or wait, a request with no timeout, or a vendor outside the register.

## GOV-5 Access control and audit

Applies when: the change touches authentication, authorization, roles, or billing (`app/auth.py`, `app/billing.py`, `app/permissions/**`).
Requirement: every grant, revoke, or billing change writes an audit event with the actor, the subject, and the time.
Evidence: the audit call in the changed path, and a test that asserts it.
Sign-off: reviewer and the security lead.
Failure means: an access or billing decision changes with no audit event.

## GOV-6 AI and model use

Applies when: the change adds or edits a prompt, a model identifier, model parameters, or a tool given to a model.
Requirement: the PR links eval results for the change and names the data the model can see.
Evidence: a link to the eval run; the prompt diff.
Sign-off: reviewer and the AI governance lead.
Failure means: a prompt or model change ships without eval results.

## GOV-7 Change management

Applies when: the change touches production code (anything outside `docs/`, `tests/`, and Markdown files).
Requirement: the PR states how the change is undone (flag, clean revert, or a rollback plan) and links the ticket.
Evidence: the undo line in the PR or the review page; a ticket key in the branch or PR title.
Sign-off: reviewer.
Failure means: production code changes with no stated way to undo it.

## GOV-8 Data retention and deletion

Applies when: the change deletes, truncates, or drops stored data, or changes how long data is kept.
Requirement: the data owner has approved the deletion, and a backup taken before the change runs is confirmed.
Evidence: the data owner's approval on the PR; the backup job reference.
Sign-off: reviewer and the data owner.
Failure means: stored data is removed or retention is shortened without the data owner's approval and a confirmed backup.
