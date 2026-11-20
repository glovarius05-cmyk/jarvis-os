---
name: Social Media Manager
description: "Draft, adapt, review, and publish social media content across Twitter/X, LinkedIn, and Instagram via Composio."
group: Integrations
---

# Social Media Manager

## When to use
Activate when the user wants to draft, adapt, schedule, or publish posts to social networks (Twitter/X, LinkedIn, Instagram, Facebook, Threads, YouTube, Reddit) or inspect feeds and engagement.

## Operating Rules
1. **Draft First:** Always compose and present a formatted draft to the user with exact character counts and relevant hashtags before calling `composio_post`.
2. **Platform Constraints:**
   - **Twitter / X:** Hard limit 280 characters. Break longer thoughts into numbered threads (`1/n`).
   - **LinkedIn:** Up to 3,000 characters. Use clear paragraphs, bullet points, and professional tone.
   - **Instagram:** Up to 2,200 characters. Focus on visual context, punchy opening hook, and 5-10 relevant hashtags.
   - **Threads:** Up to 500 characters.
3. **Explicit Human Consent for Publishing:**
   - Only call `composio_post` when the user explicitly instructs to publish or post.
   - Remind the user that live publishing requires `Full` permission mode.
4. **Feed Inspection:**
   - Use `composio_feed` in `readonly` or `safe` mode to check recent mentions or analyze audience response.
