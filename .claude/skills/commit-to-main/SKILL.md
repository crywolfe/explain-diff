---
name: commit-to-main
description: Commit the working branch's changes and push them onto main by fast-forward (no PR), keeping vulnerability details out of the commit message. Use when the user says "commit, push onto main".
disable-model-invocation: true
---

# Commit and push onto main

1. `git status --short`. Stage only the files this work changed, by name. Leave untracked files you did not
   create (`.vscode/`, `refs.json`) alone, and never stage secrets (`.env`, `.claude/settings.local.json`).
2. If you are on `main`, create a branch first (`git checkout -b <topic>`).
3. Commit with a message that says what changed in behaviour, not how to exploit what it fixes (SECURITY.md:
   no vulnerability details in public places before the release). End it with the attribution trailer the session
   gives.
4. `git checkout main && git merge --ff-only <branch> && git push origin main`. If the fast-forward fails, stop and
   ask: don't merge, rebase or force-push.
5. Report the new commit, `git log --oneline -1 origin/main`, and whether CI ran (`gh run list --branch main --limit 3`).
   Leave the branch in place.
