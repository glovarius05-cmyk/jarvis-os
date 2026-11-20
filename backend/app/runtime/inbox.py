"""Unified results inbox for all background work (Kanban, Loops, Reminders).

Every finished background job writes a durable entry to STATE_DIR/inbox.json and
routes its report back to the requester's channel (`web:<session_id>`, Telegram, etc.).
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.runtime.sessions import SessionStore

ChannelNotifier = Callable[[str, str, str], Awaitable[None]]  # (owner_chat, title, body) -> None


@dataclass(frozen=True)
class InboxItem:
    id: str
    brain: str
    source: str  # "kanban" | "loop" | "reminder" | "system"
    title: str
    body: str
    owner_chat: str
    read: bool
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "brain": self.brain,
            "source": self.source,
            "title": self.title,
            "body": self.body,
            "owner_chat": self.owner_chat,
            "read": self.read,
            "created_at": self.created_at,
        }


class InboxStore:
    """Thread-safe JSON inbox with automatic delivery back to web sessions and external channels."""

    def __init__(self, state_dir: Path, sessions: SessionStore) -> None:
        self._path = state_dir / "inbox.json"
        self._sessions = sessions
        self._lock = threading.RLock()
        self._ws_publisher: Callable[[dict[str, Any]], None] | None = None
        self._channel_notifiers: list[ChannelNotifier] = []

    def set_ws_publisher(self, publisher: Callable[[dict[str, Any]], None]) -> None:
        self._ws_publisher = publisher

    def add_channel_notifier(self, notifier: ChannelNotifier) -> None:
        self._channel_notifiers.append(notifier)

    def _load_raw(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save_raw(self, items: list[dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items[-300:], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self._path)

    def list_items(self, *, brain: str | None = None, limit: int = 50) -> list[InboxItem]:
        with self._lock:
            raw = self._load_raw()
        out = []
        for row in reversed(raw):
            if brain and row.get("brain") != brain:
                continue
            out.append(
                InboxItem(
                    id=str(row.get("id") or ""),
                    brain=str(row.get("brain") or ""),
                    source=str(row.get("source") or "system"),
                    title=str(row.get("title") or ""),
                    body=str(row.get("body") or ""),
                    owner_chat=str(row.get("owner_chat") or ""),
                    read=bool(row.get("read", False)),
                    created_at=float(row.get("created_at") or 0.0),
                )
            )
            if len(out) >= limit:
                break
        return out

    def unread_count(self, brain: str | None = None) -> int:
        return sum(1 for item in self.list_items(brain=brain, limit=300) if not item.read)

    def mark_read(self, item_id: str | None = None) -> int:
        """Mark one item (or all items when item_id is None) as read."""
        changed = 0
        with self._lock:
            raw = self._load_raw()
            for row in raw:
                if item_id is None or row.get("id") == item_id:
                    if not row.get("read"):
                        row["read"] = True
                        changed += 1
            if changed:
                self._save_raw(raw)
        return changed

    async def deliver(
        self,
        *,
        brain: str,
        source: str,
        title: str,
        body: str,
        owner_chat: str = "",
    ) -> InboxItem:
        """Store result in inbox and push it back to the originating chat session or channel."""
        item = InboxItem(
            id=f"inb_{uuid.uuid4().hex[:10]}",
            brain=brain,
            source=source,
            title=title,
            body=body,
            owner_chat=owner_chat,
            read=False,
            created_at=time.time(),
        )
        with self._lock:
            raw = self._load_raw()
            raw.append(item.to_dict())
            self._save_raw(raw)

        # Deliver directly into the originating web chat session if owner_chat == "web:<session_id>"
        if owner_chat.startswith("web:"):
            sid = owner_chat[4:].strip()
            if sid and self._sessions.get_session(sid) is not None:
                formatted = f"**[{source.upper()}] {title}**\n\n{body}"
                saved = self._sessions.add_message(
                    sid,
                    "assistant",
                    formatted,
                    provider=f"background:{source}",
                    model="inbox",
                    meta={"inbox_id": item.id},
                )
                if self._ws_publisher is not None:
                    self._ws_publisher(
                        {
                            "type": "final",
                            "session_id": sid,
                            "message_id": saved.id,
                            "content": formatted,
                            "provider": f"background:{source}",
                            "model": "inbox",
                        }
                    )

        if self._ws_publisher is not None:
            self._ws_publisher({"type": "inbox_item", "item": item.to_dict()})

        for notifier in self._channel_notifiers:
            try:
                await notifier(owner_chat, title, body)
            except Exception:  # noqa: BLE001
                continue

        return item
