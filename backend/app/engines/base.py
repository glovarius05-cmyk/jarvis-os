"""Unified engine contract shared by every AI provider (API and CLI alike).

Every engine yields the same EngineEvent stream so ChatRuntime, WebSocket clients,
Telegram, and background workers never branch on vendor-specific wire formats.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Literal, Protocol

EventType = Literal["text", "tool_call", "tool_result", "status", "final", "error"]

# Lone UTF-16 surrogates from some open-weight models break UTF-8 disk writes and JSON resends.
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")
# Private Use Area markers (e.g. OpenAI internal citation tokens U+E200..U+E202) render as garbage boxes.
_PUA_RE = re.compile(r"[\ue000-\uf8ff]")


def sanitize_text(text: str) -> str:
    """Clean lone surrogates and Private Use Area artifacts from streamed model text."""
    if not text:
        return ""
    cleaned = _SURROGATE_RE.sub("\ufffd", text)
    cleaned = _PUA_RE.sub("", cleaned)
    return cleaned


@dataclass(frozen=True)
class EngineEvent:
    type: EventType
    content: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str = ""
    provider: str = ""
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": self.type}
        if self.content:
            payload["content"] = self.content
        if self.tool_name:
            payload["tool_name"] = self.tool_name
        if self.tool_args:
            payload["tool_args"] = self.tool_args
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.provider:
            payload["provider"] = self.provider
        if self.model:
            payload["model"] = self.model
        if self.usage:
            payload["usage"] = self.usage
        return payload


ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[str]]


@dataclass
class EngineRequest:
    session_id: str
    brain: str
    system_prompt: str
    messages: list[dict[str, Any]]
    provider: str
    model: str
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_executor: ToolExecutor | None = None
    max_tool_rounds: int = 30
    tool_budget_chars: int = 40_000
    cancel_event: asyncio.Event | None = None


class EngineProtocol(Protocol):
    """Minimal contract every swappable brain adapter implements."""

    async def stream(self, request: EngineRequest) -> AsyncIterator[EngineEvent]:
        ...
