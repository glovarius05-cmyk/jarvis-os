---
name: Git Flow Agent
description: "Autonomous Git workflow: feature branches, pre-commit test execution, conventional commits, and pull request drafting."
group: Engineering
---

# Git Flow Agent

## When to use
Activate when implementing engineering tasks from Kanban, preparing Git commits, managing branches, running pre-commit verification tests, or drafting pull requests.

## Procedure
1. **Branch Hygiene:**
   - Before editing code, ensure a dedicated topic branch exists: `git checkout -b feature/<task-slug>` or `fix/<bug-slug>`.
   - Never commit directly to production or dirty detached HEAD states.
2. **Pre-Commit Verification:**
   - Inspect pending modifications using `git diff` and `git status`.
   - Run the local test suite (`pytest` or `npm test`) to guarantee zero test regressions before staging.
3. **Conventional Commits:**
   - Group related changes into atomic, logical commits.
   - Use standard Conventional Commit prefixes: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`.
   - Write clear summary lines under 72 characters without punctuation at the end.
4. **Pull Request Synthesis:**
   - Generate a concise PR description containing: Summary of Changes, Motivation & Context, How Tested, and Breaking Change Warnings.
