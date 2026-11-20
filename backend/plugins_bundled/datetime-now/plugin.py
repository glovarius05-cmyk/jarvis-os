"""Bundled plugin: jarvis_now - exact current timestamp for any engine."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def register(ctx) -> None:
    def jarvis_now(args: dict, _brain: str) -> str:
        tz_name = str(args.get("timezone") or "").strip()
        if not tz_name:
            settings = ctx.services.get("settings")
            if settings is not None:
                tz_name = str(settings.load_public().get("timezone") or "UTC")
            else:
                tz_name = "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = timezone.utc
            tz_name = "UTC"
        now = datetime.now(tz)
        return f"{now.strftime('%Y-%m-%d %H:%M:%S')} ({tz_name}, weekday={now.strftime('%A')})"

    ctx.register_tool(
        name="jarvis_now",
        description="Get the exact current date, time, and weekday.",
        handler=jarvis_now,
        min_mode="readonly",
        schema={
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "description": "Optional IANA timezone name (e.g. Europe/Moscow, UTC)"}
            },
        },
    )
