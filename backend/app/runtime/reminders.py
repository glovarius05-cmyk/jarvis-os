"""Dependency-free 5-field cron parser and Reminders scheduler with precondition checks.

Precondition rule: before scheduling a recurring job or reminder, verify that any
required MCP connections exist and that a reporting channel is configured; if not,
return structured missing preconditions (`can_force=True`) rather than failing silently daily.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime
from typing import Any

from app.brain.vault import VaultManager, safe_resolve
from app.engines.base import EngineProtocol, EngineRequest
from app.engines.router import EngineRouter
from app.hub.gateway import MCPHubGateway
from app.runtime.inbox import InboxStore


def _match_cron_field(expr: str, value: int, min_v: int, max_v: int) -> bool:
    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            if step <= 0:
                raise ValueError(f"Invalid cron step: {part}")
        else:
            base = part

        if base == "*":
            start, end = min_v, max_v
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = int(a), int(b)
        else:
            num = int(base)
            if "/" in part:
                start, end = num, max_v
            else:
                if value == num:
                    return True
                continue

        if start <= value <= end and ((value - start) % step == 0):
            return True
    return False


def validate_cron(cron_expr: str) -> bool:
    fields = (cron_expr or "").strip().split()
    if len(fields) != 5:
        return False
    limits = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
    try:
        for field, (lo, hi) in zip(fields, limits):
            _match_cron_field(field, lo, lo, hi)
        return True
    except ValueError:
        return False


def cron_matches(cron_expr: str, dt: datetime) -> bool:
    """Return True if a standard 5-field cron expression matches datetime `dt` (minute precision)."""
    fields = (cron_expr or "").strip().split()
    if len(fields) != 5:
        return False
    # Python weekday(): Mon=0..Sun=6 -> cron weekday: Sun=0 (or 7), Mon=1..Sat=6
    cron_dow = (dt.weekday() + 1) % 7
    try:
        if not _match_cron_field(fields[0], dt.minute, 0, 59):
            return False
        if not _match_cron_field(fields[1], dt.hour, 0, 23):
            return False
        if not _match_cron_field(fields[2], dt.day, 1, 31):
            return False
        if not _match_cron_field(fields[3], dt.month, 1, 12):
            return False
        return _match_cron_field(fields[4], cron_dow, 0, 7) or (
            cron_dow == 0 and _match_cron_field(fields[4], 7, 0, 7)
        )
    except ValueError:
        return False


class ReminderStore:
    """Manages one-shot and cron reminders in <brain>/Javis/reminders.json."""

    def __init__(
        self,
        *,
        vaults: VaultManager,
        router: EngineRouter,
        hub: MCPHubGateway,
        inbox: InboxStore,
        engine_override: EngineProtocol | None = None,
    ) -> None:
        self._vaults = vaults
        self._router = router
        self._hub = hub
        self._inbox = inbox
        self._engine_override = engine_override
        self._lock = threading.RLock()

    def _file_path(self, brain: str):
        return safe_resolve(self._vaults.brain_path(brain), "Javis/reminders.json")

    def _read_all(self, brain: str) -> list[dict[str, Any]]:
        path = self._file_path(brain)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _write_all(self, brain: str, items: list[dict[str, Any]]) -> None:
        path = self._file_path(brain)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def list_reminders(self, brain: str) -> list[dict[str, Any]]:
        with self._lock:
            return self._read_all(brain)

    def check_preconditions(
        self,
        *,
        required_connections: list[str] | None = None,
        owner_chat: str = "",
    ) -> list[str]:
        missing: list[str] = []
        if required_connections:
            connected_ids = {c.id for c in self._hub.connections.list_connections() if c.enabled}
            for req in required_connections:
                if req not in connected_ids:
                    missing.append(f"Required MCP connection '{req}' is not connected.")
        if not owner_chat.strip():
            missing.append("No owner_chat channel specified (results will only appear in web Inbox).")
        return missing

    def create_reminder(
        self,
        *,
        brain: str,
        name: str,
        prompt: str,
        cron: str = "",
        run_at_ts: float = 0.0,
        notify_only: bool = False,
        mode: str = "auto",
        provider: str = "",
        model: str = "",
        owner_chat: str = "",
        required_connections: list[str] | None = None,
        allow_missing: bool = False,
    ) -> dict[str, Any]:
        if not cron and run_at_ts <= 0:
            raise ValueError("Provide either a 5-field `cron` expression or a positive `run_at_ts`.")
        if cron and not validate_cron(cron):
            raise ValueError(f"Invalid 5-field cron expression: '{cron}'")

        missing = self.check_preconditions(required_connections=required_connections, owner_chat=owner_chat)
        if missing and not allow_missing:
            return {
                "created": False,
                "can_force": True,
                "missing_preconditions": missing,
            }

        item = {
            "id": f"rem_{uuid.uuid4().hex[:10]}",
            "name": name.strip() or "Reminder",
            "prompt": prompt.strip(),
            "cron": cron.strip(),
            "run_at_ts": float(run_at_ts),
            "notify_only": bool(notify_only),
            "mode": mode if mode in {"suggest", "auto", "full"} else "auto",
            "provider": provider.strip(),
            "model": model.strip(),
            "owner_chat": owner_chat.strip(),
            "enabled": True,
            "last_minute_key": "",
            "last_run_at": 0.0,
            "created_at": time.time(),
        }
        with self._lock:
            items = self._read_all(brain)
            items.append(item)
            self._write_all(brain, items)
        return {"created": True, "reminder": item}

    def delete_reminder(self, brain: str, reminder_id: str) -> bool:
        with self._lock:
            items = self._read_all(brain)
            kept = [r for r in items if r.get("id") != reminder_id]
            if len(kept) == len(items):
                return False
            self._write_all(brain, kept)
            return True

    async def fire_reminder(self, brain: str, item: dict[str, Any]) -> str:
        if item.get("notify_only"):
            body = str(item.get("prompt") or item.get("name"))
        else:
            item_provider = str(item.get("provider") or "").strip()
            item_model = str(item.get("model") or "").strip()
            provider, model = self._router.resolve_target(
                override_provider=item_provider,
                override_model=item_model,
                role="main",
            )
            mode = str(item.get("mode") or "auto")
            engine = self._engine_override or self._router.build_engine(provider, brain=brain, run_mode=mode)
            tools = await self._hub.discover_schemas(brain=brain, run_mode=mode)
            executor = self._hub.make_executor(brain=brain, run_mode=mode, session_id=f"rem:{item['id']}")
            req = EngineRequest(
                session_id=f"rem:{item['id']}",
                brain=brain,
                system_prompt="You are Jarvis executing a scheduled reminder job. Be concise and factual.",
                messages=[{"role": "user", "content": str(item.get("prompt") or "")}],
                provider=provider,
                model=model,
                tools=tools,
                tool_executor=executor,
            )
            parts: list[str] = []
            final = ""
            try:
                async for ev in engine.stream(req):
                    if ev.type == "text":
                        parts.append(ev.content)
                    elif ev.type == "error":
                        parts.append(f"ERROR: {ev.content}")
                        break
                    elif ev.type == "final":
                        final = ev.content
            except Exception as exc:
                parts.append(f"ERROR: {exc}")
            body = (final or "".join(parts)).strip()

        await self._inbox.deliver(
            brain=brain,
            source="reminder",
            title=str(item.get("name") or "Reminder"),
            body=body,
            owner_chat=str(item.get("owner_chat") or ""),
        )
        return body

    async def tick(self, brains: list[str], now_dt: datetime | None = None) -> list[str]:
        dt = now_dt or datetime.now()
        now_ts = dt.timestamp()
        minute_key = dt.strftime("%Y-%m-%d %H:%M")
        fired: list[str] = []

        for b in brains:
            due: list[dict[str, Any]] = []
            with self._lock:
                items = self._read_all(b)
                dirty = False
                for item in items:
                    if not item.get("enabled", True):
                        continue
                    cron_expr = str(item.get("cron") or "")
                    run_at = float(item.get("run_at_ts") or 0)
                    if cron_expr:
                        if item.get("last_minute_key") != minute_key and cron_matches(cron_expr, dt):
                            item["last_minute_key"] = minute_key
                            item["last_run_at"] = now_ts
                            dirty = True
                            due.append(dict(item))
                    elif run_at > 0 and now_ts >= run_at:
                        item["enabled"] = False
                        item["last_run_at"] = now_ts
                        dirty = True
                        due.append(dict(item))
                if dirty:
                    self._write_all(b, items)

            for item in due:
                await self.fire_reminder(b, item)
                fired.append(f"{b}:{item['id']}")
        return fired
