---
name: Jarvis Builder
description: "Create or update Jarvis skills, agents, workflows, loops, and plugins with templates and duplicate checks."
group: AI
---

# Jarvis Builder

## When to use
Activate when the user asks to create a new reusable skill, specialist agent, multi-step workflow, recurring loop, or native Python plugin.

## Procedure
1. Pick the smallest capability type:
   - Reusable know-how using existing tools -> Skill (`skills/<slug>/SKILL.md`)
   - Specialist role with its own system prompt -> Agent (`agents/<slug>.md`)
   - Multi-step chain across agents -> Workflow (`workflows/<slug>.md`)
   - Repeating interval work -> Loop (`Javis/loops/<slug>.md`, always `enabled: false`)
   - Native Python action no MCP covers -> Plugin via `jarvis_plugin` (always `enabled: false`)
2. Check for duplicates first by listing the target folder. Update existing items instead of cloning.
3. Keep skill `description` <= 150 characters (the router truncates silently beyond 150).
4. Use lowercase ASCII hyphenated slugs (`review-pr`, `weekly-digest`).
5. Report briefly what was created, its file path, and whether the user needs to enable it.
