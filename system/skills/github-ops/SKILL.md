---
name: GitHub Ops
description: "Inspect GitHub repositories, open issues, pull requests, and create issues via Composio."
group: Engineering
---

# GitHub Ops

Inspired by OpenClaw's `github` skill, routed through Jarvis OS `composio_github`.

## When to use
Activate when the user asks about GitHub repositories, open issues, pull requests, starring repos, or creating issues.

## Procedure
1. Use `composio_github` directly (account ID is injected automatically):
   - Profile: `composio_github(action="profile")`
   - Repositories: `composio_github(action="repos", limit=10)`
   - Issues: `composio_github(action="issues", repo="owner/repo")`
   - Pull Requests: `composio_github(action="pulls", repo="owner/repo")`
   - Create Issue: `composio_github(action="create_issue", repo="owner/repo", title="...", body="...")`
2. For advanced GitHub actions (workflow runs, releases, commits), use `composio_search(app="github", query="...")` and `composio_action`.
3. Present full repository names, issue titles, and statuses directly in chat.
