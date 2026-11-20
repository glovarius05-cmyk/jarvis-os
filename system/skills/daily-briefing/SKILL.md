---
name: Daily Briefing
description: "Generate structured daily briefings: agenda, active Kanban tasks, inbox notifications, and key reminders via Telegram."
group: Productivity
---

# Daily Briefing

## When to use
Activate when the user asks for a morning or evening briefing, status update, or when triggered by a scheduled loop/reminder.

## Procedure
1. Query active Kanban tasks using `jarvis_query` or `list_tasks` to identify:
   - In-progress items
   - Blocked items requiring attention
   - Recently completed tasks
2. Check unread inbox notifications and upcoming cron reminders.
3. Check current date, time, and weekday via `jarvis_now`.
4. Compose a clean, motivating briefing with:
   - Today's date and key priority
   - Top 3 focus tasks for the day
   - Any blocked or pending decisions
5. Keep tone concise, professional, and ready for Telegram delivery.
