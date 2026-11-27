---
name: git-hygiene
description: Maintain clean Git history, LF line endings on Windows, worktree isolation, and CODEOWNERS hygiene. Use when initializing repositories, committing changes, or managing git worktrees. Do not use for web research.
---

# Git Hygiene (Windows 11)

1. Enforce `core.autocrlf=false` and `.gitattributes` LF rules for code/config files.
2. Never commit `.env`, `.venv/`, or state/video artifacts.
3. Isolate parallel experiments via `git worktree add --detach` and clean up afterward.
