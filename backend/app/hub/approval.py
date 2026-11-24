"""Interactive Approval Gate (Human-in-the-Loop) for sensitive / FULL-mode tool calls.

Why this exists: Dangerous operations (shell commands, autonomous browser actions,
sending emails or publishing to social media) should optionally pause and ask the
owner for explicit confirmation in the Web Dashboard or Telegram before executing.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

ApprovalNotifier = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass
class ApprovalRequest:
    approval_id: str
    session_id: str
    brain: str
    tool_name: str
    args: dict[str, Any]
    reason: str
    channel: str = "web"
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # "pending" | "approved" | "denied" | "timeout"
    event: asyncio.Event = field(default_factory=asyncio.Event)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "session_id": self.session_id,
            "brain": self.brain,
            "tool_name": self.tool_name,
            "args": self.args,
            "reason": self.reason,
            "channel": self.channel,
            "status": self.status,
            "created_at": round(self.created_at, 3),
        }


class ApprovalManager:
    """Thread/task-safe in-memory coordinator for interactive tool call approvals."""

    def __init__(self, *, default_timeout_s: float = 120.0) -> None:
        self._default_timeout_s = max(5.0, float(default_timeout_s))
        self._pending: dict[str, ApprovalRequest] = {}
        self._notifiers: list[ApprovalNotifier] = []

    def add_notifier(self, notifier: ApprovalNotifier) -> None:
        self._notifiers.append(notifier)

    def list_pending(self, session_id: str | None = None) -> list[dict[str, Any]]:
        items = []
        for req in self._pending.values():
            if req.status != "pending":
                continue
            if session_id and req.session_id != session_id:
                continue
            items.append(req.to_public_dict())
        return items

    async def _notify(self, payload: dict[str, Any]) -> None:
        for fn in list(self._notifiers):
            try:
                res = fn(payload)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:  # noqa: BLE001
                continue

    async def request_approval(
        self,
        *,
        session_id: str,
        brain: str,
        tool_name: str,
        args: dict[str, Any],
        reason: str = "",
        channel: str = "web",
        timeout_s: float | None = None,
    ) -> tuple[bool, str]:
        """Register a pending approval request, notify UI/channels, and await decision."""
        aid = f"apr_{uuid.uuid4().hex[:10]}"
        req = ApprovalRequest(
            approval_id=aid,
            session_id=session_id,
            brain=brain,
            tool_name=tool_name,
            args=args,
            reason=reason or f"Tool '{tool_name}' requires confirmation",
            channel=channel,
        )
        self._pending[aid] = req

        await self._notify(
            {
                "type": "approval_request",
                **req.to_public_dict(),
            }
        )

        wait_s = max(1.0, float(timeout_s if timeout_s is not None else self._default_timeout_s))
        try:
            await asyncio.wait_for(req.event.wait(), timeout=wait_s)
        except asyncio.TimeoutError:
            req.status = "timeout"
            await self._notify(
                {
                    "type": "approval_resolved",
                    "approval_id": aid,
                    "session_id": session_id,
                    "status": "timeout",
                }
            )
            self._pending.pop(aid, None)
            return False, f"Approval timed out after {int(wait_s)}s for tool '{tool_name}'."

        approved = req.status == "approved"
        status_label = req.status
        self._pending.pop(aid, None)
        if approved:
            return True, "approved by owner"
        return False, f"Action '{tool_name}' was denied by owner ({status_label})."

    async def resolve(self, approval_id: str, *, approved: bool) -> dict[str, Any] | None:
        """Resolve a pending approval request and wake up the waiting tool execution."""
        req = self._pending.get(approval_id)
        if req is None or req.status != "pending":
            return None
        req.status = "approved" if approved else "denied"
        req.event.set()
        payload = {
            "type": "approval_resolved",
            "approval_id": approval_id,
            "session_id": req.session_id,
            "status": req.status,
        }
        await self._notify(payload)
        return req.to_public_dict()
