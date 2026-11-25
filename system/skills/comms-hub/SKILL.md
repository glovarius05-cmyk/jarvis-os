---
name: Comms Hub
description: "Coordinate Slack channels, WhatsApp messages, and Zoom meetings or recordings via Composio."
group: Communication
---

# Comms Hub

Inspired by OpenClaw (`Wacli`) and Nanobot multi-channel messaging workflows.

## When to use
Activate when the user wants to check or send Slack messages, read WhatsApp chats, or schedule/list Zoom meetings.

## Procedure
1. For quick overviews:
   - Slack channels: `composio_feed(platform="slack", limit=10)`
   - WhatsApp messages: `composio_feed(platform="whatsapp", limit=10)`
   - Zoom meetings: `composio_feed(platform="zoom", limit=10)`
2. For sending messages or creating a Zoom meeting:
   - Find the exact action via `composio_search(app="slack" | "whatsapp" | "zoom", query="...")`.
   - Run it with `composio_action(app="...", action_name="...", arguments={...})`.
3. Summarize unread threads, meeting links, and action items clearly in chat.
