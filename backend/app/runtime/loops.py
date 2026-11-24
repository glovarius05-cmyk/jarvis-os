"""Multi-Loop runner: Markdown loop definitions + separate loop-state.json + circuit breaker.

Design invariants:
- User loop definitions live in <brain>/Javis/loops/<slug>.md (never mutated by runtime).
- Runtime state lives strictly in <brain>/Javis/loop-state.json (avoids Obsidian edit conflicts).
- Global asyncio.Lock ensures sequential execution (at most 1 loop running at a time).
- Circuit breaker: 3 consecutive failures (`fail_streak >= 3`) auto-pauses the loop in state
  and alerts the owner via Inbox.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.brain.skills import dump_markdown_frontmatter, parse_markdown_frontmatter
from app.brain.vault import VaultManager, safe_resolve
from app.engines.base import EngineProtocol, EngineRequest
from app.engines.router import EngineRouter
from app.hub.gateway import MCPHubGateway
from app.runtime.inbox import InboxStore

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
FAIL_STREAK_LIMIT = 3


@dataclass(frozen=True)
class LoopDef:
    slug: str
    name: str
    enabled: bool
    mode: str
    interval_min: int
    quiet_hours: str
    max_runs_per_day: int
    owner_chat: str
    notify: bool
    prompt: str
    path: str


def in_quiet_hours(quiet_hours: str, now_dt: datetime | None = None) -> bool:
    """Check quiet_hours like '23-07' or '00-06' against current local hour."""
    raw = (quiet_hours or "").strip()
    if "-" not in raw:
        return False
    try:
        start_s, end_s = raw.split("-", 1)
        start_h, end_h = int(start_s), int(end_s)
    except ValueError:
        return False
    hour = (now_dt or datetime.now()).hour
    if start_h <= end_h:
        return start_h <= hour < end_h
    return hour >= start_h or hour < end_h


class LoopRunner:
    """Sequential Multi-Loop manager with quiet hours, daily caps, and fail-streak circuit breaker."""

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
        self._run_lock = asyncio.Lock()

    def _state_path(self, brain: str):
        return safe_resolve(self._vaults.brain_path(brain), "Javis/loop-state.json")

    def _load_state(self, brain: str) -> dict[str, dict[str, Any]]:
        path = self._state_path(brain)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self, brain: str, state: dict[str, dict[str, Any]]) -> None:
        path = self._state_path(brain)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def list_loops(self, brain: str) -> list[dict[str, Any]]:
        root = self._vaults.brain_path(brain)
        loops_dir = root / "Javis" / "loops"
        if not loops_dir.exists():
            return []
        state = self._load_state(brain)
        out: list[dict[str, Any]] = []
        for p in sorted(loops_dir.glob("*.md"), key=lambda x: x.name.lower()):
            raw = p.read_text(encoding="utf-8", errors="replace")
            meta, body = parse_markdown_frontmatter(raw)
            slug = str(meta.get("slug") or p.stem)
            st = state.get(slug) or {}
            out.append(
                {
                    "slug": slug,
                    "name": str(meta.get("name") or slug),
                    "enabled": bool(meta.get("enabled", False)),
                    "mode": str(meta.get("mode") or "auto"),
                    "interval_min": max(5, int(meta.get("interval_min") or 60)),
                    "quiet_hours": str(meta.get("quiet_hours") or ""),
                    "max_runs_per_day": max(1, int(meta.get("max_runs_per_day") or 24)),
                    "owner_chat": str(meta.get("owner_chat") or ""),
                    "notify": bool(meta.get("notify", True)),
                    "prompt": body,
                    "path": f"Javis/loops/{p.name}",
                    "state": {
                        "last_run": float(st.get("last_run") or 0),
                        "last_status": str(st.get("last_status") or ""),
                        "last_summary": str(st.get("last_summary") or ""),
                        "runs_today": int(st.get("runs_today") or 0),
                        "fail_streak": int(st.get("fail_streak") or 0),
                        "auto_paused_reason": str(st.get("auto_paused_reason") or ""),
                    },
                }
            )
        return out

    def create_loop(
        self,
        *,
        brain: str,
        slug: str,
        name: str,
        prompt: str,
        interval_min: int = 60,
        mode: str = "auto",
        enabled: bool = False,
        quiet_hours: str = "",
        max_runs_per_day: int = 24,
        owner_chat: str = "",
        notify: bool = True,
    ) -> dict[str, Any]:
        clean_slug = slug.strip().lower()
        if not _SLUG_RE.match(clean_slug):
            raise ValueError("Loop slug must be lowercase ASCII alphanumeric/hyphen/underscore")
        target = safe_resolve(self._vaults.brain_path(brain), f"Javis/loops/{clean_slug}.md")
        meta = {
            "type": "loop",
            "name": name.strip() or clean_slug,
            "slug": clean_slug,
            "enabled": bool(enabled),
            "mode": mode if mode in {"suggest", "auto", "full"} else "auto",
            "interval_min": max(5, int(interval_min)),
            "quiet_hours": quiet_hours.strip(),
            "max_runs_per_day": max(1, int(max_runs_per_day)),
            "owner_chat": owner_chat.strip(),
            "notify": bool(notify),
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dump_markdown_frontmatter(meta, prompt), encoding="utf-8")
        for item in self.list_loops(brain):
            if item["slug"] == clean_slug:
                return item
        raise RuntimeError("Loop creation failed")

    def toggle_loop(self, brain: str, slug: str, enabled: bool) -> dict[str, Any] | None:
        target = safe_resolve(self._vaults.brain_path(brain), f"Javis/loops/{slug}.md")
        if not target.is_file():
            return None
        meta, body = parse_markdown_frontmatter(target.read_text(encoding="utf-8", errors="replace"))
        meta["enabled"] = bool(enabled)
        target.write_text(dump_markdown_frontmatter(meta, body), encoding="utf-8")
        if enabled:
            state = self._load_state(brain)
            if slug in state:
                state[slug]["fail_streak"] = 0
                state[slug]["auto_paused_reason"] = ""
                self._save_state(brain, state)
        for item in self.list_loops(brain):
            if item["slug"] == slug:
                return item
        return None

    async def run_loop(self, brain: str, slug: str, *, manual: bool = False) -> dict[str, Any]:
        """Execute a single loop iteration sequentially under the global lock."""
        async with self._run_lock:
            loops = {x["slug"]: x for x in self.list_loops(brain)}
            loop = loops.get(slug)
            if loop is None:
                raise KeyError(f"Loop '{slug}' not found in brain '{brain}'")

            state = self._load_state(brain)
            today = datetime.now().strftime("%Y-%m-%d")
            st = state.setdefault(slug, {})
            if st.get("day") != today:
                st["day"] = today
                st["runs_today"] = 0
            if manual:
                st["auto_paused_reason"] = ""
                st["fail_streak"] = 0

            try:
                loop_prov = str(loop.get("provider") or "").strip()
                loop_mod = str(loop.get("model") or "").strip()
                provider, model = self._router.resolve_target(
                    override_provider=loop_prov,
                    override_model=loop_mod,
                    role="main",
                )
                engine = self._engine_override or self._router.build_engine(
                    provider, brain=brain, run_mode=loop["mode"]
                )
                tools = await self._hub.discover_schemas(brain=brain, run_mode=loop["mode"])
                executor = self._hub.make_executor(brain=brain, run_mode=loop["mode"], session_id=f"loop:{slug}")

                req = EngineRequest(
                    session_id=f"loop:{slug}",
                    brain=brain,
                    system_prompt=(
                        f"You are Jarvis autonomous loop '{loop['name']}' (mode={loop['mode']}). "
                        "Complete exactly one concrete unit of work for this iteration and summarize concisely."
                    ),
                    messages=[{"role": "user", "content": loop["prompt"]}],
                    provider=provider,
                    model=model,
                    tools=tools,
                    tool_executor=executor,
                )

                parts: list[str] = []
                final_text = ""
                async for ev in engine.stream(req):
                    if ev.type == "text":
                        parts.append(ev.content)
                    elif ev.type == "error":
                        raise RuntimeError(ev.content)
                    elif ev.type == "final":
                        final_text = ev.content

                summary = (final_text or "".join(parts)).strip()
                st["last_run"] = time.time()
                st["last_status"] = "ok"
                st["last_summary"] = summary[:1000]
                st["runs_today"] = int(st.get("runs_today") or 0) + 1
                st["fail_streak"] = 0
                st["auto_paused_reason"] = ""
                self._save_state(brain, state)

                if loop["notify"]:
                    await self._inbox.deliver(
                        brain=brain,
                        source="loop",
                        title=f"Loop: {loop['name']}",
                        body=summary or "(no output)",
                        owner_chat=loop["owner_chat"],
                    )
                return {"ok": True, "slug": slug, "summary": summary, "state": st}
            except Exception as exc:  # noqa: BLE001
                streak = int(st.get("fail_streak") or 0) + 1
                st["last_run"] = time.time()
                st["last_status"] = "error"
                st["last_summary"] = str(exc)[:500]
                st["fail_streak"] = streak
                if streak >= FAIL_STREAK_LIMIT:
                    st["auto_paused_reason"] = f"Auto-paused after {streak} consecutive failures: {exc}"
                    await self._inbox.deliver(
                        brain=brain,
                        source="loop",
                        title=f"Loop auto-paused: {loop['name']}",
                        body=st["auto_paused_reason"],
                        owner_chat=loop["owner_chat"],
                    )
                self._save_state(brain, state)
                return {"ok": False, "slug": slug, "error": str(exc), "state": st}

    async def tick(self, brains: list[str]) -> str | None:
        """Pick the single most-overdue eligible loop across brains and execute it."""
        if self._run_lock.locked():
            return None
        now = time.time()
        today = datetime.now().strftime("%Y-%m-%d")
        best: tuple[float, str, str] | None = None

        for b in brains:
            state = self._load_state(b)
            for lp in self.list_loops(b):
                if not lp["enabled"]:
                    continue
                if in_quiet_hours(lp["quiet_hours"]):
                    continue
                st = state.get(lp["slug"]) or {}
                if st.get("auto_paused_reason"):
                    continue
                runs_today = int(st.get("runs_today") or 0) if st.get("day") == today else 0
                if runs_today >= lp["max_runs_per_day"]:
                    continue
                last_run = float(st.get("last_run") or 0)
                overdue = (now - last_run) - (lp["interval_min"] * 60.0)
                if overdue >= 0 and (best is None or overdue > best[0]):
                    best = (overdue, b, lp["slug"])

        if best is None:
            return None
        _, target_brain, target_slug = best
        await self.run_loop(target_brain, target_slug, manual=False)
        return f"{target_brain}:{target_slug}"
