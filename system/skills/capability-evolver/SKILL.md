---
name: Capability Evolver
description: "Analyze repeated workflows or errors and turn successful patterns into reusable Jarvis skills or rules."
group: Meta
---

# Capability Evolver

Inspired by OpenClaw's popular `capability-evolver` meta-skill and Nanobot's background memory consolidation.

## When to use
Activate when the user asks to optimize Jarvis, automate a repeated multi-step workflow, or prevent a recurring mistake.

## Procedure
1. Review recent context in `memory/MEMORY.md`, `memory/CORE_MEMORY.md`, and recent task reports in `wiki/tasks/`.
2. Identify any repeated manual steps, friction points, or tool errors.
3. Propose or write the smallest durable upgrade:
   - A concise rule in `memory/CORE_MEMORY.md` for behavioral preferences.
   - A reusable skill in `skills/<slug>/SKILL.md` (keeping `description` <= 150 chars).
   - A scheduled loop or reminder if the task is recurring.
4. Report what was upgraded and where it was saved directly in chat.
