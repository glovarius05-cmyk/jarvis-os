---
name: Prompt Engineer
description: "Refine, test, and optimize prompts for LLMs using few-shot examples, chain-of-thought, and clear guardrails."
group: AI
---

# Prompt Engineer

## When to use
Activate when crafting custom instructions, system prompts, agent personas, or optimizing prompts that produce inaccurate or inconsistent LLM outputs.

## Procedure
1. Identify the task objective, target model constraints, and expected output format (JSON, Markdown, YAML, plain text).
2. Apply proven prompting principles:
   - Specific role and expertise persona definition
   - Explicit step-by-step reasoning instructions
   - Positive constraints rather than only negative prohibitions
   - 1-3 concrete few-shot input/output examples
   - Clear edge case guidelines and fallback responses
3. Test edge cases mentally or via test turns.
4. Deliver the polished prompt ready to paste into `agents/<name>.md` or an API payload.
