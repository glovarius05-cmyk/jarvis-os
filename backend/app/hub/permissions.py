"""Hard permission enforcement and JSONL audit logging for the MCP Hub.

Why this exists: Prompt instructions alone are not a security boundary. Every tool call
(whether from an API engine in-process or a CLI engine over /hub/mcp) passes through
this gate before execution, applying min(connection_perm, run_mode) in Python code.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any


class PermLevel(IntEnum):
    READONLY = 0
    SAFE = 1
    FULL = 2

    @classmethod
    def parse(cls, value: str | None, default: "PermLevel" = None) -> "PermLevel":  # type: ignore[assignment]
        fallback = cls.READONLY if default is None else default
        v = (value or "").strip().lower()
        mapping = {
            "readonly": cls.READONLY,
            "read": cls.READONLY,
            "suggest": cls.READONLY,
            "safe": cls.SAFE,
            "auto": cls.SAFE,
            "full": cls.FULL,
        }
        return mapping.get(v, fallback)

    def label(self) -> str:
        return {
            PermLevel.READONLY: "readonly",
            PermLevel.SAFE: "safe",
            PermLevel.FULL: "full",
        }[self]


def mode_to_level(run_mode: str | None) -> PermLevel:
    """Convert run mode ('suggest' | 'auto' | 'full') to maximum allowed PermLevel."""
    return PermLevel.parse(run_mode, default=PermLevel.FULL)


def effective_level(connection_perm: str | PermLevel, run_mode: str | PermLevel) -> PermLevel:
    """Apply the stricter of connection permission and current run mode."""
    c_lvl = connection_perm if isinstance(connection_perm, PermLevel) else PermLevel.parse(connection_perm, PermLevel.READONLY)
    r_lvl = run_mode if isinstance(run_mode, PermLevel) else mode_to_level(run_mode)
    return PermLevel(min(int(c_lvl), int(r_lvl)))


def is_tool_allowed(
    *,
    required: str | PermLevel,
    connection_perm: str | PermLevel,
    run_mode: str | PermLevel,
) -> tuple[bool, str]:
    req = required if isinstance(required, PermLevel) else PermLevel.parse(required, PermLevel.FULL)
    eff = effective_level(connection_perm, run_mode)
    if req <= eff:
        return True, f"allowed ({req.label()} <= {eff.label()})"
    return (
        False,
        f"Permission denied by MCP Hub: tool requires '{req.label()}', "
        f"but effective level is '{eff.label()}' (run_mode={run_mode}, conn_perm={connection_perm}).",
    )


@dataclass(frozen=True)
class AuditEntry:
    ts: float
    brain: str
    session_id: str
    run_mode: str
    tool: str
    source: str
    allowed: bool
    reason: str
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": round(self.ts, 3),
            "brain": self.brain,
            "session_id": self.session_id,
            "run_mode": self.run_mode,
            "tool": self.tool,
            "source": self.source,
            "allowed": self.allowed,
            "reason": self.reason,
            "duration_ms": round(self.duration_ms, 2),
        }


class AuditLogger:
    """Append-only JSONL audit log for every tool invocation."""

    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / "mcp_audit.jsonl"
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def record(self, entry: AuditEntry) -> None:
        line = json.dumps(entry.to_dict(), ensure_ascii=False) + "\n"
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        with self._lock:
            lines = self._path.read_text(encoding="utf-8", errors="replace").splitlines()
        out: list[dict[str, Any]] = []
        for raw in reversed(lines[-max(1, min(limit, 500)) :]):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    out.append(parsed)
            except json.JSONDecodeError:
                continue
        return out
