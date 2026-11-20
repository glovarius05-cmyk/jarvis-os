---
name: Meeting Synthesizer
description: "Extract decisions, action items with deadlines, and key takeaways from raw meeting notes or audio transcripts."
group: Productivity
---

# Meeting Synthesizer

## When to use
Activate when the user inputs meeting notes, discussion transcripts, voice message dumps, or call summaries.

## Procedure
1. Scan the text to isolate:
   - Key Decisions made
   - Unresolved open questions
   - Action Items: assignees, tasks, and deadlines
2. Format the output into clean Obsidian Markdown:
   - `## Summary`
   - `## Decisions Made`
   - `## Action Items` with checkboxes `[ ]` and `@owner` tags
3. Ask if any action items should be converted into Kanban cards or reminders.
4. Save to `meetings/YYYY-MM-DD-<topic>.md` in Second Brain.
