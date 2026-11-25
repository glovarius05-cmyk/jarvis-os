---
name: Google Workspace Ops
description: "Search and manage Google Drive files, Docs, Sheets, and Analytics reports using Composio tools."
group: Integrations
---

# Google Workspace Ops

Inspired by OpenClaw's Google Workspace (`GOG`) skill, adapted for Jarvis OS + Composio.

## When to use
Activate when the user asks to find files on Google Drive, read or update Google Docs / Google Sheets, or check Google Analytics metrics.

## Procedure
1. Use `composio_feed` for quick listings:
   - Drive files: `composio_feed(platform="googledrive", query="...", limit=10)`
   - Docs: `composio_feed(platform="googledocs", query="...", limit=10)`
   - Sheets: `composio_feed(platform="googlesheets", limit=10)`
2. When you need a specific action (creating a doc, appending rows to a sheet, fetching GA4 traffic):
   - Call `composio_search(app="googledrive" | "googledocs" | "googlesheets" | "googleanalytics", query="...")` to get the exact action slug.
   - Execute via `composio_action(app="...", action_name="...", arguments={...})`.
3. Present the full results (file names, links, sheet values, or metrics) directly in chat.
