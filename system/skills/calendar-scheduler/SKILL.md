---
name: Calendar Scheduler
description: "Manage calendar events, resolve schedule conflicts, find optimal meeting slots, and sync agendas into daily briefings."
group: Productivity
---

# Calendar Scheduler

## When to use
Activate when the user asks to schedule, reschedule, or inspect calendar events, find available meeting slots across timezones, or prepare meeting agendas.

## Procedure
1. **Context & Timezone Verification:**
   - Always query current local date, time, and timezone before calculating offsets.
   - Inspect existing commitments to avoid scheduling conflicts and overlaps.
2. **Optimal Slot Identification:**
   - Identify candidate meeting windows (minimum 30 min buffer between back-to-back calls).
   - Account for focus blocks and user quiet hours defined in Second Brain settings.
   - Present candidate slots clearly with date, start time, end time, and timezone.
3. **Event Execution & Sync:**
   - Prepare event parameters: title, start ISO timestamp, duration, attendees, and concise agenda.
   - Dispatch to Google Calendar / Outlook via Composio MCP or output an iCal (.ics) snippet.
   - Integrate upcoming appointments into the daily morning briefing (`daily-briefing`).
