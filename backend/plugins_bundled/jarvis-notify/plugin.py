"""Bundled plugin: jarvis-notify - deliver notifications to Inbox and Telegram."""

from __future__ import annotations


def register(ctx) -> None:
    async def jarvis_notify(args: dict, brain: str) -> str:
        title = str(args.get("title") or "Jarvis Notification").strip()
        body = str(args.get("body") or args.get("message") or "").strip()
        owner_chat = str(args.get("owner_chat") or "").strip()
        if not body:
            return "ERROR: 'body' (notification text) is required."

        inbox = ctx.services.get("inbox")
        if inbox is None:
            return "ERROR: InboxStore service is unavailable."

        # Auto-resolve Telegram chat if owner_chat omitted
        if not owner_chat:
            settings = ctx.services.get("settings")
            if settings is not None:
                try:
                    cfg = settings.load_public()
                    tg_ids = cfg.get("telegram_allowed_chat_ids") or []
                    if isinstance(tg_ids, list) and tg_ids:
                        owner_chat = f"telegram:{tg_ids[0]}"
                except Exception:
                    pass

        item = await inbox.deliver(
            brain=brain,
            source="system",
            title=title,
            body=body,
            owner_chat=owner_chat,
        )
        route_note = f" (routed to {owner_chat})" if owner_chat else ""
        return f"Notification delivered to Inbox (id={item.id}){route_note}."

    ctx.register_tool(
        name="jarvis_notify",
        description="Send a notification to the Jarvis Inbox and optionally to the owner's linked Telegram chat.",
        handler=jarvis_notify,
        min_mode="safe",
        schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Notification title"},
                "body": {"type": "string", "description": "Notification body text"},
                "owner_chat": {"type": "string", "description": "Optional target channel (e.g. telegram:12345)"},
            },
            "required": ["body"],
        },
    )
