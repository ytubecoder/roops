---
name: repo-hygiene-check
description: Check ~/projects repos for dirty worktrees and unpushed commits
---

# Repo hygiene check

Run `git -C <repo> status --porcelain` for each repo under ~/projects.
Run `git -C <repo> log --oneline @{u}.. 2>/dev/null | wc -l` to count unpushed commits.
Summarize repos that are dirty or unpushed in a short report with one line per repo.
