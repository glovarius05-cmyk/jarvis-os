"""Bundled plugin: jarvis_schedule - create reminders/cron jobs and repeating loops safely."""

from __future__ import annotations

import json
import re


def _slugify(text: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return clean[:48] or "loop"


def register(ctx) -> None:
    def jarvis_schedule(args: dict, brain: str) -> str:
        reminders = ctx.services.get("reminders")
        loops = ctx.services.get("loops")
        if reminders is None or loops is None:
            return "ERROR: Scheduler services unavailable."

        op = str(args.get("op") or "list").strip().lower()

        if op == "list":
            return json.dumps(
                {
                    "brain": brain,
                    "reminders": reminders.list_reminders(brain),
                    "loops": loops.list_loops(brain),
                },
                ensure_ascii=False,
            )

        if op == "create_reminder":
            res = reminders.create_reminder(
                brain=brain,
                name=str(args.get("name") or "Reminder"),
                prompt=str(args.get("prompt") or ""),
                cron=str(args.get("cron") or ""),
                run_at_ts=float(args.get("run_at_ts") or 0),
                notify_only=bool(args.get("notify_only", False)),
                mode=str(args.get("mode") or "auto"),
                owner_chat=str(args.get("owner_chat") or ""),
                required_connections=args.get("required_connections") if isinstance(args.get("required_connections"), list) else None,
                allow_missing=bool(args.get("allow_missing", False)),
            )
            return json.dumps(res, ensure_ascii=False)

        if op == "create_loop":
            name = str(args.get("name") or "").strip()
            slug = str(args.get("slug") or _slugify(name))
            prompt = str(args.get("prompt") or "").strip()
            if not prompt:
                return "ERROR: prompt is required for create_loop."
            # Hard safety: loops created from chat ALWAYS start disabled for owner review
            created = loops.create_loop(
                brain=brain,
                slug=slug,
                name=name or slug,
                prompt=prompt,
                interval_min=int(args.get("interval_min") or 60),
                mode=str(args.get("mode") or "auto"),
                enabled=False,
                quiet_hours=str(args.get("quiet_hours") or ""),
                owner_chat=str(args.get("owner_chat") or ""),
            )
            return json.dumps(
                {
                    "ok": True,
                    "loop": created,
                    "note": "Created with enabled=false. Tell the user to review and enable it on the Tasks -> Loops page.",
                },
                ensure_ascii=False,
            )

        if op == "delete_reminder":
            rid = str(args.get("id") or "").strip()
            ok = reminders.delete_reminder(brain, rid)
            return json.dumps({"ok": ok})

        return f"ERROR: Unsupported op '{op}'. Use list, create_reminder, create_loop, or delete_reminder."

    ctx.register_tool(
        name="jarvis_schedule",
        description="Schedule cron/one-shot reminders or repeating background loops (op=list|create_reminder|create_loop|delete_reminder).",
        handler=jarvis_schedule,
        min_mode="safe",
        schema={
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["list", "create_reminder", "create_loop", "delete_reminder"]},
                "id": {"type": "string"},
                "slug": {"type": "string"},
                "name": {"type": "string"},
                "prompt": {"type": "string"},
                "cron": {"type": "string", "description": "5-field cron expression, e.g. '30 8 * * 1-5'"},
                "run_at_ts": {"type": "number"},
                "interval_min": {"type": "integer", "default": 60},
                "quiet_hours": {"type": "string", "description": "e.g. '23-07'"},
                "notify_only": {"type": "boolean", "default": False},
                "mode": {"type": "string", "enum": ["suggest", "auto", "full"], "default": "auto"},
                "owner_chat": {"type": "string"},
                "required_connections": {"type": "array", "items": {"type": "string"}},
                "allow_missing": {"type": "boolean", "default": False},
            },
            "required": ["op"],
        },
    )
