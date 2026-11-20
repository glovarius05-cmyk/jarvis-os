---
name: Research Deep Dive
description: "Conduct multi-source web research, cross-reference facts, verify citations, and write structured Second Brain memos."
group: Knowledge
---

# Research Deep Dive

## When to use
Activate when the user asks for deep research, market analysis, competitor comparison, technical evaluation, or fact-checking.

## Procedure
1. Break the research question into 2-4 distinct search queries.
2. Execute searches using `web_search` and retrieve in-depth source pages with `web_fetch`.
3. Cross-reference claims across multiple independent sources.
4. Structure findings into an Obsidian Markdown memo:
   - Executive Summary (TL;DR)
   - Key Facts & Comparison Table
   - Trade-offs & Risks
   - Verified Source Links
5. Save the report to `Second Brain` under `research/<topic-slug>.md` using `vault_write`.
