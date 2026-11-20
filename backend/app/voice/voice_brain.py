"""Voice V2 two-brain architecture: fast lightweight voice brain + main-brain delegation.

Why this exists: Spawning a full tool-equipped main brain takes 4-10s before the first
spoken word. VoiceBrain runs a tiny, zero-tool voice kernel (1-2s latency on local Llama
or Gemini Flash) that replies conversationally, filters ambient noise (`JARVIS_SKIP:`),
triggers UI jumps (`JARVIS_UI:`), and hands real data/action requests to the main brain
via `JARVIS_ASK_MAIN: <self-contained request>`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.engines.base import EngineProtocol, EngineRequest
from app.engines.router import EngineRouter
from app.runtime.chat_runtime import ChatRuntime
from app.runtime.sessions import SessionStore
from app.voice.tts import strip_markdown_for_tts

ASK_MAIN_MARKER = "JARVIS_ASK_MAIN:"
UI_MARKER = "JARVIS_UI:"
SKIP_MARKER = "JARVIS_SKIP:"


@dataclass(frozen=True)
class VoiceTurnResult:
    skipped: bool
    spoken_text: str
    raw_text: str
    ui_action: str = ""
    ui_target: str = ""
    delegated_request: str = ""
    delegated_session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "skipped": self.skipped,
            "spoken_text": self.spoken_text,
            "raw_text": self.raw_text,
            "ui_action": self.ui_action,
            "ui_target": self.ui_target,
            "delegated_request": self.delegated_request,
            "delegated_session_id": self.delegated_session_id,
        }


def parse_voice_output(raw: str) -> VoiceTurnResult:
    """Parse control lines (JARVIS_SKIP, JARVIS_UI, JARVIS_ASK_MAIN) from VoiceBrain output."""
    lines = (raw or "").splitlines()
    spoken_lines: list[str] = []
    skipped = False
    ui_action = ""
    ui_target = ""
    delegated = ""

    for line in lines:
        s = line.strip()
        if s.startswith(SKIP_MARKER):
            skipped = True
            continue
        if s.startswith(UI_MARKER):
            parts = s[len(UI_MARKER) :].strip().split(None, 1)
            if parts:
                ui_action = parts[0].strip()
                ui_target = parts[1].strip() if len(parts) > 1 else ""
            continue
        if s.startswith(ASK_MAIN_MARKER):
            delegated = s[len(ASK_MAIN_MARKER) :].strip()
            continue
        if s:
            spoken_lines.append(s)

    if skipped:
        return VoiceTurnResult(skipped=True, spoken_text="", raw_text=raw)

    spoken = strip_markdown_for_tts(" ".join(spoken_lines))
    return VoiceTurnResult(
        skipped=False,
        spoken_text=spoken,
        raw_text=raw,
        ui_action=ui_action,
        ui_target=ui_target,
        delegated_request=delegated,
    )


class VoiceBrain:
    """Fast spoken conversation handler that delegates heavy tasks to ChatRuntime."""

    def __init__(
        self,
        *,
        prompts_dir: Path,
        router: EngineRouter,
        sessions: SessionStore,
        chat_runtime: ChatRuntime,
        engine_override: EngineProtocol | None = None,
    ) -> None:
        self._kernel_path = prompts_dir / "voice_kernel.md"
        self._router = router
        self._sessions = sessions
        self._chat_runtime = chat_runtime
        self._engine_override = engine_override
        self._recent_turns: dict[str, list[dict[str, str]]] = {}

    async def process_utterance(
        self,
        *,
        brain: str,
        transcript: str,
        session_id: str = "",
    ) -> VoiceTurnResult:
        clean = (transcript or "").strip()
        if not clean:
            return VoiceTurnResult(skipped=True, spoken_text="", raw_text="")

        voice_kernel = self._kernel_path.read_text(encoding="utf-8").strip()
        hist_key = session_id or brain
        history = list(self._recent_turns.get(hist_key, []))[-10:]
        history.append({"role": "user", "content": clean})

        provider, model = self._router.resolve_target(role="aux")
        engine = self._engine_override or self._router.build_engine(provider, brain=brain, run_mode="suggest")

        req = EngineRequest(
            session_id=f"voice:{hist_key}",
            brain=brain,
            system_prompt=voice_kernel,
            messages=history,
            provider=provider,
            model=model,
            tools=[],
            tool_executor=None,
            max_tool_rounds=1,
        )

        parts: list[str] = []
        final_text = ""
        async for ev in engine.stream(req):
            if ev.type == "text":
                parts.append(ev.content)
            elif ev.type == "error":
                return VoiceTurnResult(
                    skipped=False,
                    spoken_text=f"Voice engine error: {ev.content}",
                    raw_text=ev.content,
                )
            elif ev.type == "final":
                final_text = ev.content

        raw = (final_text or "".join(parts)).strip()
        parsed = parse_voice_output(raw)

        if not parsed.skipped:
            history.append({"role": "assistant", "content": parsed.spoken_text or raw})
            self._recent_turns[hist_key] = history[-10:]

        # Hand heavy requests straight to the main brain via ChatRuntime!
        delegated_sid = ""
        if parsed.delegated_request:
            target_sid = session_id
            if not target_sid or self._sessions.get_session(target_sid) is None:
                created = self._sessions.create_session(brain=brain, title="Voice session", channel="web")
                target_sid = created.id
            self._chat_runtime.start_turn(
                session_id=target_sid,
                user_text=parsed.delegated_request,
                run_mode="full",
            )
            delegated_sid = target_sid

        return VoiceTurnResult(
            skipped=parsed.skipped,
            spoken_text=parsed.spoken_text,
            raw_text=parsed.raw_text,
            ui_action=parsed.ui_action,
            ui_target=parsed.ui_target,
            delegated_request=parsed.delegated_request,
            delegated_session_id=delegated_sid,
        )
