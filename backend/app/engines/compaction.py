"""Long-session Compaction for API engines so conversations never hit hard context cutoffs."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from app.engines.base import EngineProtocol, EngineRequest
from app.engines.router import EngineRouter
from app.runtime.sessions import MessageRecord, SessionStore

COMPACT_THRESHOLD_CHARS = 24_000
COMPACT_THRESHOLD_MESSAGES = 36
KEEP_RECENT_MESSAGES = 8


class SessionCompactor:
    """Summarizes older turns of long chat sessions via aux_engine while keeping recent turns intact."""

    def __init__(
        self,
        *,
        state_dir: Path,
        sessions: SessionStore,
        router: EngineRouter,
        engine_override: EngineProtocol | None = None,
    ) -> None:
        self._path = state_dir / "compactions.json"
        self._sessions = sessions
        self._router = router
        self._engine_override = engine_override
        self._lock = threading.RLock()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self._path)

    def get_summary(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._load().get(session_id)

    def build_model_messages(self, session_id: str, messages: list[MessageRecord]) -> list[dict[str, str]]:
        """Return compacted history: [summary message] + messages after compacted_until_id."""
        rec = self.get_summary(session_id)
        if not rec or not rec.get("summary"):
            return [{"role": m.role, "content": m.content} for m in messages]

        until_id = int(rec.get("compacted_until_id") or 0)
        tail = [m for m in messages if m.id > until_id]
        out = [
            {
                "role": "assistant",
                "content": f"[Earlier conversation summary]\n{rec['summary']}",
            }
        ]
        out.extend({"role": m.role, "content": m.content} for m in tail)
        return out

    def needs_compaction(self, session_id: str, messages: list[MessageRecord]) -> bool:
        rec = self.get_summary(session_id)
        until_id = int(rec.get("compacted_until_id") or 0) if rec else 0
        uncompacted = [m for m in messages if m.id > until_id]
        if len(uncompacted) <= KEEP_RECENT_MESSAGES + 2:
            return False
        chars = sum(len(m.content) for m in uncompacted)
        return len(uncompacted) >= COMPACT_THRESHOLD_MESSAGES or chars >= COMPACT_THRESHOLD_CHARS

    async def compact_session(self, session_id: str, *, force: bool = False) -> dict[str, Any]:
        session = self._sessions.get_session(session_id)
        if session is None:
            raise KeyError(f"Session '{session_id}' not found")
        messages = self._sessions.list_messages(session_id, limit=500)
        if not force and not self.needs_compaction(session_id, messages):
            return {"compacted": False, "reason": "Session below compaction threshold"}
        if len(messages) <= KEEP_RECENT_MESSAGES:
            return {"compacted": False, "reason": "Too few messages to compact"}

        older = messages[:-KEEP_RECENT_MESSAGES]
        prev = self.get_summary(session_id)
        prev_summary = str(prev.get("summary") or "") if prev else ""
        until_id = older[-1].id

        transcript = "\n".join(f"{m.role.upper()}: {m.content[:800]}" for m in older[-40:])
        prompt = (
            (f"Previous summary:\n{prev_summary}\n\n" if prev_summary else "")
            + f"Newer messages to merge into the summary:\n{transcript}\n\n"
            "Write a concise, factual summary preserving key decisions, numbers, file paths, and open tasks."
        )

        provider, model = self._router.resolve_target(role="aux")
        engine = self._engine_override or self._router.build_engine(provider, brain=session.brain, run_mode="suggest")
        req = EngineRequest(
            session_id=f"compact:{session_id}",
            brain=session.brain,
            system_prompt="You are a concise conversation compactor. Preserve facts and decisions accurately.",
            messages=[{"role": "user", "content": prompt}],
            provider=provider,
            model=model,
            tools=[],
            tool_executor=None,
            max_tool_rounds=1,
        )

        parts: list[str] = []
        final = ""
        async for ev in engine.stream(req):
            if ev.type == "text":
                parts.append(ev.content)
            elif ev.type == "error":
                raise RuntimeError(ev.content)
            elif ev.type == "final":
                final = ev.content

        summary_text = (final or "".join(parts)).strip()
        record = {
            "session_id": session_id,
            "compacted_until_id": until_id,
            "compacted_count": len(older),
            "summary": summary_text,
            "updated_at": time.time(),
        }
        with self._lock:
            all_data = self._load()
            all_data[session_id] = record
            self._save(all_data)
        return {"compacted": True, **record}
