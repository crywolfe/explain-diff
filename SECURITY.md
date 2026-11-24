# Security policy

## Reporting a vulnerability

Report it privately through GitHub: **Security → Report a vulnerability** on this repository
(<https://github.com/crywolfe/explain-diff/security/advisories/new>). Please do not open a public issue for a
vulnerability. Expect an acknowledgement within a week.

In scope: anything that lets content in a change under review (diff, PR text, commit messages, repository files,
governance documents) make the skill or its page do something the reviewer did not ask for, such as running code,
reaching the network, writing outside the skill's output folder, committing, pushing, leaking a secret, or getting
a script past the run guard. The threat model is in [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).

## Supported versions

Only the latest release gets fixes. Install a tagged release (see the README) so you know which version you run.

## What to rely on

The run guard, the private output folder, masking, and the page's escaping are enforced by scripts and tested
offline (`evals/`). Behaviour that depends on the model following `SKILL.md` (reading text as data, ranking,
not giving a verdict) is measured by the model evals, not guaranteed.
