"""Bundled plugin: jarvis_task - manage autonomous Kanban work from any engine."""

from __future__ import annotations

import json


def register(ctx) -> None:
    def jarvis_task(args: dict, brain: str) -> str:
        kanban = ctx.services.get("kanban")
        if kanban is None:
            return "ERROR: Kanban service unavailable."

        op = str(args.get("op") or "list").strip().lower()

        if op == "list":
            tasks = [t.to_dict() for t in kanban.list_tasks(brain=brain, limit=30)]
            return json.dumps(
                {"brain": brain, "auto_enabled": kanban.is_auto_enabled(brain), "tasks": tasks},
                ensure_ascii=False,
            )

        if op == "add":
            title = str(args.get("title") or "").strip()
            if not title:
                return "ERROR: title is required for op=add."
            task = kanban.create_task(
                brain=brain,
                title=title,
                description=str(args.get("description") or ""),
                mode=str(args.get("mode") or "auto"),
                route=str(args.get("route") or "auto"),
                owner_chat=str(args.get("owner_chat") or ""),
            )
            auto_on = kanban.is_auto_enabled(brain)
            note = (
                "Auto-orchestration is ON; worker will claim and run this task automatically."
                if auto_on
                else "NOTE: Auto-orchestration is currently OFF for this brain. Tell the user the task is QUEUED (not running yet) and to enable Auto-Orchestration or click Run on the Tasks page."
            )
            return json.dumps({"ok": True, "task": task.to_dict(), "note": note}, ensure_ascii=False)

        if op == "answer":
            tid = str(args.get("id") or "").strip()
            answer = str(args.get("answer") or "").strip()
            if not answer:
                return "ERROR: answer text is required."
            if not tid:
                blocked = [t for t in kanban.list_tasks(brain=brain) if t.status == "blocked"]
                if len(blocked) == 1:
                    tid = blocked[0].id
                else:
                    return f"ERROR: Specify task id. Blocked tasks count: {len(blocked)}."
            updated = kanban.answer_task(tid, answer)
            return json.dumps({"ok": True, "task": updated.to_dict()}, ensure_ascii=False)

        if op == "accept":
            tid = str(args.get("id") or "").strip()
            if not tid:
                return "ERROR: id is required for op=accept."
            updated = kanban.accept_task(tid)
            return json.dumps({"ok": True, "task": updated.to_dict()}, ensure_ascii=False)

        if op == "cancel":
            tid = str(args.get("id") or "").strip()
            if not tid:
                return "ERROR: id is required for op=cancel."
            updated = kanban.cancel_task(tid, reason=str(args.get("reason") or ""))
            return json.dumps({"ok": True, "task": updated.to_dict()}, ensure_ascii=False)

        return f"ERROR: Unsupported op '{op}'. Use list, add, answer, accept, or cancel."

    ctx.register_tool(
        name="jarvis_task",
        description="Manage autonomous Kanban background tasks (op=add|list|answer|accept|cancel).",
        handler=jarvis_task,
        min_mode="safe",
        schema={
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["add", "list", "answer", "accept", "cancel"]},
                "id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "mode": {"type": "string", "enum": ["suggest", "auto", "full"], "default": "auto"},
                "route": {"type": "string", "default": "auto"},
                "owner_chat": {"type": "string", "description": "e.g. web:<session_id> or tg:<chat_id>"},
                "answer": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["op"],
        },
    )
