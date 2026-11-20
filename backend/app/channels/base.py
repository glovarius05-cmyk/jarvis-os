"""Unified TurnShell shared by messaging channels (Telegram, CLI, Webhooks).

Why this exists: Every non-web channel needs the same session routing, slash commands
(`/new`, `/stop`, `/status`, `/brain`), plain-text degradation of `<!-- JARVIS_ASK -->`
buttons, Honesty Guard, and Usage tracking — implemented once here instead of copy-pasted.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from app.brain.prompt_builder import PromptBuilder
from app.config import SettingsStore
from app.engines.base import EngineProtocol, EngineRequest
from app.engines.compaction import SessionCompactor
from app.engines.router import EngineRouter
from app.hub.gateway import MCPHubGateway
from app.runtime.honesty_guard import apply_honesty_guard
from app.runtime.sessions import SessionStore
from app.runtime.usage import UsageStore

_ASK_BLOCK_RE = re.compile(r"<!--\s*JARVIS_ASK:\s*(\{[\s\S]*?\})\s*-->")


def degrade_ask_blocks_for_plain_text(text: str) -> str:
    """Turn dashboard button blocks `<!-- JARVIS_ASK: {...} -->` into numbered plain-text choices."""
    def _replace(match: re.Match[str]) -> str:
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            return ""
        options = data.get("options") if isinstance(data, dict) else None
        if not isinstance(options, list) or not options:
            return ""
        lines = []
        for idx, opt in enumerate(options[:4], start=1):
            if isinstance(opt, dict):
                label = str(opt.get("label") or "").strip()
                desc = str(opt.get("desc") or "").strip()
                lines.append(f"{idx}. {label}" + (f" - {desc}" if desc else ""))
        return ("\n" + "\n".join(lines)) if lines else ""

    return _ASK_BLOCK_RE.sub(_replace, text or "").strip()


class TurnShell:
    """Channel-agnostic turn executor with per-peer session mapping and slash commands."""

    def __init__(
        self,
        *,
        settings: SettingsStore,
        sessions: SessionStore,
        router: EngineRouter,
        prompt_builder: PromptBuilder,
        hub: MCPHubGateway,
        compactor: SessionCompactor,
        usage: UsageStore,
        engine_override: EngineProtocol | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._router = router
        self._prompt_builder = prompt_builder
        self._hub = hub
        self._compactor = compactor
        self._usage = usage
        self._engine_override = engine_override
        self._active_sessions: dict[str, str] = {}  # "<channel>:<peer_id>" -> session_id
        self._cancels: dict[str, asyncio.Event] = {}

    def _peer_key(self, channel: str, peer_id: str) -> str:
        return f"{channel}:{peer_id}"

    def _get_or_create_session(self, channel: str, peer_id: str, brain: str, *, force_new: bool = False) -> str:
        key = self._peer_key(channel, peer_id)
        if not force_new:
            existing_id = self._active_sessions.get(key)
            if existing_id and self._sessions.get_session(existing_id) is not None:
                return existing_id
            # Reuse most recent session for this channel+peer if server restarted
            chan_tag = f"{channel}:{peer_id}"
            prev = self._sessions.list_sessions(brain=brain, channel=chan_tag, limit=1)
            if prev:
                self._active_sessions[key] = prev[0].id
                return prev[0].id

        rec = self._sessions.create_session(
            brain=brain,
            title=f"{channel.capitalize()} ({peer_id})",
            channel=f"{channel}:{peer_id}",
        )
        self._active_sessions[key] = rec.id
        return rec.id

    async def handle_message(
        self,
        *,
        channel: str,
        peer_id: str,
        text: str,
        run_mode: str = "full",
    ) -> dict[str, Any]:
        clean = (text or "").strip()
        if not clean:
            return {"reply": "", "session_id": ""}

        cfg = self._settings.load_public()
        brain = str(cfg["active_brain"])
        key = self._peer_key(channel, peer_id)

        # Shared slash commands across messaging channels
        if clean == "/start":
            return {
                "reply": (
                    f"Jarvis OS online (brain: {brain}).\n"
                    "Commands: /new (fresh session), /status, /stop."
                ),
                "session_id": "",
            }

        if clean == "/new":
            sid = self._get_or_create_session(channel, peer_id, brain, force_new=True)
            return {"reply": f"Started a new session ({sid[:8]}) on brain '{brain}'.", "session_id": sid}

        if clean == "/stop":
            ev = self._cancels.get(key)
            if ev is not None:
                ev.set()
                return {"reply": "Stopping the current response...", "session_id": self._active_sessions.get(key, "")}
            return {"reply": "No active response to stop.", "session_id": ""}

        if clean == "/status":
            m = cfg["model"]
            return {
                "reply": (
                    f"Brain: {brain}\n"
                    f"Main engine: {m['main_provider']} / {m['main_model']}\n"
                    f"Aux engine: {m['aux_provider']} / {m['aux_model']}"
                ),
                "session_id": self._active_sessions.get(key, ""),
            }

        session_id = self._get_or_create_session(channel, peer_id, brain)
        provider, model = self._router.resolve_target(role="main")

        self._sessions.add_message(session_id, "user", clean, provider=provider, model=model)
        history = self._sessions.list_messages(session_id, limit=50)
        messages_payload = self._compactor.build_model_messages(session_id, history)

        system_prompt = self._prompt_builder.build(
            brain=brain,
            provider=provider,
            model=model,
            channel=channel,
            session_id=session_id,
        )
        tools = await self._hub.discover_schemas(brain=brain, run_mode=run_mode)
        executor = self._hub.make_executor(brain=brain, run_mode=run_mode, session_id=session_id)

        cancel_ev = asyncio.Event()
        self._cancels[key] = cancel_ev
        try:
            engine = self._engine_override or self._router.build_engine(provider, brain=brain, run_mode=run_mode)
            req = EngineRequest(
                session_id=session_id,
                brain=brain,
                system_prompt=system_prompt,
                messages=messages_payload,
                provider=provider,
                model=model,
                tools=tools,
                tool_executor=executor,
                cancel_event=cancel_ev,
            )

            parts: list[str] = []
            final_text = ""
            final_usage: dict[str, Any] = {}
            created_bg = False

            async for ev in engine.stream(req):
                if ev.type == "text":
                    parts.append(ev.content)
                elif ev.type == "tool_call" and ev.tool_name in {"jarvis_task", "jarvis_schedule"}:
                    created_bg = True
                elif ev.type == "error":
                    return {"reply": f"Error: {ev.content}", "session_id": session_id}
                elif ev.type == "final":
                    final_text = ev.content
                    final_usage = ev.usage

            raw_reply = (final_text or "".join(parts)).strip()
            guarded, honesty_flag = apply_honesty_guard(raw_reply, created_background_job=created_bg)
            plain_reply = degrade_ask_blocks_for_plain_text(guarded) if channel != "web" else guarded

            self._sessions.add_message(
                session_id,
                "assistant",
                plain_reply,
                provider=provider,
                model=model,
                meta={"usage": final_usage, "honesty_guard": honesty_flag},
            )
            self._usage.record(
                brain=brain,
                source=channel,
                provider=provider,
                model=model,
                input_tokens=int(final_usage.get("input_tokens") or 0),
                output_tokens=int(final_usage.get("output_tokens") or 0),
            )
            return {
                "reply": plain_reply,
                "session_id": session_id,
                "provider": provider,
                "model": model,
            }
        finally:
            self._cancels.pop(key, None)
