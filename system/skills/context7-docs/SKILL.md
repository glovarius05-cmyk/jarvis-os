---
name: Context7 & HF Research
description: "Fetch up-to-date library documentation via Context7 and search Hugging Face models or datasets."
group: Research
---

# Context7 & HF Research

## When to use
Activate when writing code against fast-changing libraries/frameworks (via Context7) or researching open-source AI models/datasets on Hugging Face.

## Procedure
1. For library documentation:
   - Search Context7 tools via `composio_search(app="context7", query="resolve library docs")`.
   - Execute `composio_action(app="context7", action_name="...", arguments={...})` to fetch current API docs before writing code.
2. For Hugging Face models/datasets:
   - Call `composio_feed(platform="huggingface", query="...", limit=10)` or `composio_search(app="huggingface", query="...")`.
3. Summarize key APIs, model sizes, licenses, and links directly in chat.
