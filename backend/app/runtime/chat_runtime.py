"""Server-owned ChatRuntime decoupled from WebSocket connection lifecycles.

Why this exists: Closing or refreshing (F5) a browser tab must NEVER cancel an
in-flight AI turn. Every turn runs as a server asyncio.Task that persists its final
answer to SQLite regardless of subscribers; WebSockets merely subscribe to events
and resync from active_snapshot() on reconnect.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.brain.prompt_builder import PromptBuilder
from app.engines.base import EngineEvent, EngineProtocol, EngineRequest
from app.engines.compaction import SessionCompactor
from app.engines.router import EngineRouter
from app.hub.gateway import MCPHubGateway
from app.runtime.honesty_guard import apply_honesty_guard
from app.runtime.sessions import SessionStore
from app.runtime.usage import UsageStore

Writer = Callable[[dict[str, Any]], Awaitable[None]]

SEND_TIMEOUT_S = 3.0
BACKLOG_MAX = 400
HISTORY_MAX_MESSAGES = 60


def coalesce_event(backlog: deque[dict[str, Any]], event: dict[str, Any]) -> None:
    """Append event to a slow client's backlog, folding adjacent stream deltas of the same session."""
    if event.get("type") == "stream" and backlog:
        last = backlog[-1]
        if (
            last.get("type") == "stream"
            and last.get("session_id") == event.get("session_id")
            and last.get("provider") == event.get("provider")
            and last.get("model") == event.get("model")
        ):
            backlog[-1] = {**last, "content": f"{last.get('content', '')}{event.get('content', '')}"}
            return
    backlog.append(event)


@dataclass
class _Client:
    client_id: str
    writer: Writer
    on_drop: Callable[[], None] | None = None
    backlog: deque[dict[str, Any]] = field(default_factory=deque)
    drain_task: asyncio.Task[None] | None = None


@dataclass
class ChatJob:
    session_id: str
    brain: str
    provider: str
    model: str
    task: asyncio.Task[None]
    cancel_event: asyncio.Event
    started_at: float = field(default_factory=time.time)
    accumulated_text: str = ""
    tool_events: list[dict[str, Any]] = field(default_factory=list)


class ChatRuntime:
    """In-memory turn registry and resilient WebSocket event fan-out."""

    def __init__(
        self,
        *,
        sessions: SessionStore,
        router: EngineRouter,
        prompt_builder: PromptBuilder,
        hub: MCPHubGateway | None = None,
        compactor: SessionCompactor | None = None,
        usage: UsageStore | None = None,
        engine_override: EngineProtocol | None = None,
    ) -> None:
        self._sessions = sessions
        self._router = router
        self._prompt_builder = prompt_builder
        self._hub = hub
        self._compactor = compactor
        self._usage = usage
        self._engine_override = engine_override
        self._clients: dict[str, _Client] = {}
        self._jobs: dict[str, ChatJob] = {}

    def add_client(
        self,
        client_id: str,
        writer: Writer,
        on_drop: Callable[[], None] | None = None,
    ) -> None:
        self._clients[client_id] = _Client(client_id=client_id, writer=writer, on_drop=on_drop)

    def remove_client(self, client_id: str) -> None:
        client = self._clients.pop(client_id, None)
        if client is not None:
            client.backlog.clear()
            if client.drain_task is not None and not client.drain_task.done():
                client.drain_task.cancel()

    def _drop_client(self, client_id: str) -> None:
        client = self._clients.get(client_id)
        if client is None:
            return
        self.remove_client(client_id)
        if client.on_drop is not None:
            try:
                client.on_drop()
            except Exception:  # noqa: BLE001
                pass

    def publish(self, event: dict[str, Any]) -> None:
        """Non-blocking fan-out to every connected subscriber."""
        for client_id, client in list(self._clients.items()):
            coalesce_event(client.backlog, event)
            if len(client.backlog) > BACKLOG_MAX:
                self._drop_client(client_id)
                continue
            if client.drain_task is None or client.drain_task.done():
                client.drain_task = asyncio.create_task(self._drain_client(client_id, client))

    async def _drain_client(self, client_id: str, client: _Client) -> None:
        try:
            while client.backlog and self._clients.get(client_id) is client:
                event = client.backlog.popleft()
                await asyncio.wait_for(client.writer(event), timeout=SEND_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            self._drop_client(client_id)

    def active_snapshot(self) -> list[dict[str, Any]]:
        """Snapshot of currently running turns so reconnecting browsers resume seamlessly."""
        out: list[dict[str, Any]] = []
        for sid, job in list(self._jobs.items()):
            if job.task.done():
                self._jobs.pop(sid, None)
                continue
            out.append(
                {
                    "session_id": job.session_id,
                    "brain": job.brain,
                    "provider": job.provider,
                    "model": job.model,
                    "started_at": job.started_at,
                    "accumulated_text": job.accumulated_text,
                    "tool_events": list(job.tool_events),
                }
            )
        return out

    def is_running(self, session_id: str) -> bool:
        job = self._jobs.get(session_id)
        return job is not None and not job.task.done()

    def start_turn(
        self,
        *,
        session_id: str,
        user_text: str,
        provider: str = "",
        model: str = "",
        run_mode: str = "full",
    ) -> ChatJob:
        text = (user_text or "").strip()
        if not text:
            raise ValueError("Message content cannot be empty")
        if self.is_running(session_id):
            raise RuntimeError("A turn is already running for this session")

        session = self._sessions.get_session(session_id)
        if session is None:
            raise KeyError(f"Session not found: {session_id}")

        resolved_provider, resolved_model = self._router.resolve_target(
            override_provider=provider,
            override_model=model,
            pinned_provider=session.pinned_provider,
            pinned_model=session.pinned_model,
            role="main",
        )
        normalized_mode = run_mode if run_mode in {"suggest", "auto", "full", "consensus", "debate"} else "full"

        # Auto-title brand new sessions from the first user prompt
        if session.message_count == 0 and session.title in {"New chat", "Новый чат"}:
            auto_title = text.splitlines()[0][:64].strip()
            if auto_title:
                self._sessions.update_session(session_id, title=auto_title)

        user_msg = self._sessions.add_message(
            session_id,
            "user",
            text,
            provider=resolved_provider,
            model=resolved_model,
        )

        cancel_event = asyncio.Event()
        job = ChatJob(
            session_id=session_id,
            brain=session.brain,
            provider=resolved_provider,
            model=resolved_model,
            task=asyncio.create_task(
                self._run_turn(
                    session_id=session_id,
                    brain=session.brain,
                    channel=session.channel,
                    provider=resolved_provider,
                    model=resolved_model,
                    run_mode=normalized_mode,
                    cancel_event=cancel_event,
                    user_message_id=user_msg.id,
                )
            ),
            cancel_event=cancel_event,
        )
        self._jobs[session_id] = job
        return job

    def stop_turn(self, session_id: str) -> bool:
        job = self._jobs.get(session_id)
        if job is None or job.task.done():
            return False
        job.cancel_event.set()
        return True

    async def wait_turn(self, session_id: str, timeout: float = 15.0) -> None:
        job = self._jobs.get(session_id)
        if job is not None:
            await asyncio.wait_for(asyncio.shield(job.task), timeout=timeout)

    async def shutdown(self) -> None:
        for job in list(self._jobs.values()):
            if not job.task.done():
                job.cancel_event.set()
                job.task.cancel()
        if self._jobs:
            await asyncio.gather(*(j.task for j in self._jobs.values()), return_exceptions=True)
        self._jobs.clear()
        for cid in list(self._clients.keys()):
            self.remove_client(cid)

    async def _run_turn(
        self,
        *,
        session_id: str,
        brain: str,
        channel: str,
        provider: str,
        model: str,
        run_mode: str,
        cancel_event: asyncio.Event,
        user_message_id: int,
    ) -> None:
        self.publish(
            {
                "type": "turn_start",
                "session_id": session_id,
                "user_message_id": user_message_id,
                "provider": provider,
                "model": model,
                "mode": run_mode,
            }
        )

        try:
            engine = self._engine_override or self._router.build_engine(
                provider, brain=brain, run_mode=run_mode, model=model
            )
            history = self._sessions.list_messages(session_id, limit=HISTORY_MAX_MESSAGES)
            if self._compactor is not None:
                if self._compactor.needs_compaction(session_id, history):
                    try:
                        await self._compactor.compact_session(session_id, force=False)
                    except Exception:  # noqa: BLE001
                        pass
                messages_payload = self._compactor.build_model_messages(session_id, history)
            else:
                messages_payload = [{"role": m.role, "content": m.content} for m in history]
            system_prompt = self._prompt_builder.build(
                brain=brain,
                provider=provider,
                model=model,
                channel=channel,
                session_id=session_id,
            )

            tools = (
                await self._hub.discover_schemas(brain=brain, run_mode=run_mode)
                if self._hub is not None
                else []
            )
            if provider == "local-llama" and tools:
                # Local models (2B-8B parameter) have limited context windows and reasoning budgets.
                # Exclude heavy external remote MCP tools (like Composio Remote MCP with huge multi-kilobyte schemas)
                # while preserving all native built-in tools (vault, web, run_command, jarvis_task) and bundled plugins.
                tools = [t for t in tools if not t.get("name", "").startswith("mcp__")]
            tool_executor = (
                self._hub.make_executor(brain=brain, run_mode=run_mode, session_id=session_id)
                if self._hub is not None
                else None
            )

            req = EngineRequest(
                session_id=session_id,
                brain=brain,
                system_prompt=system_prompt,
                messages=messages_payload,
                provider=provider,
                model=model,
                tools=tools,
                tool_executor=tool_executor,
                cancel_event=cancel_event,
            )

            final_text = ""
            final_usage: dict[str, Any] = {}
            had_error = False
            created_bg_job = False

            async for ev in engine.stream(req):
                job = self._jobs.get(session_id)
                if ev.type == "text":
                    if job is not None:
                        job.accumulated_text += ev.content
                    self.publish(
                        {
                            "type": "stream",
                            "session_id": session_id,
                            "content": ev.content,
                            "provider": provider,
                            "model": model,
                        }
                    )
                elif ev.type in {"tool_call", "tool_result"}:
                    if ev.type == "tool_call" and ev.tool_name in {"jarvis_task", "jarvis_schedule"}:
                        created_bg_job = True
                    tool_payload = {"session_id": session_id, **ev.to_dict()}
                    if job is not None:
                        job.tool_events.append(tool_payload)
                    self.publish(tool_payload)
                elif ev.type == "error":
                    had_error = True
                    self.publish(
                        {
                            "type": "error",
                            "session_id": session_id,
                            "content": ev.content,
                            "provider": provider,
                            "model": model,
                        }
                    )
                    break
                elif ev.type == "final":
                    final_text = ev.content
                    final_usage = ev.usage

            if not had_error:
                job = self._jobs.get(session_id)
                raw_answer = final_text or (job.accumulated_text if job is not None else "")
                answer, honesty_flag = apply_honesty_guard(
                    raw_answer, created_background_job=created_bg_job
                )
                saved = self._sessions.add_message(
                    session_id,
                    "assistant",
                    answer,
                    provider=provider,
                    model=model,
                    meta={
                        "usage": final_usage,
                        "cancelled": cancel_event.is_set(),
                        "honesty_guard": honesty_flag,
                    },
                )
                if self._usage is not None:
                    self._usage.record(
                        brain=brain,
                        source="chat",
                        provider=provider,
                        model=model,
                        input_tokens=int(final_usage.get("input_tokens") or 0),
                        output_tokens=int(final_usage.get("output_tokens") or 0),
                    )
                self.publish(
                    {
                        "type": "final",
                        "session_id": session_id,
                        "message_id": saved.id,
                        "content": answer,
                        "provider": provider,
                        "model": model,
                        "usage": final_usage,
                        "cancelled": cancel_event.is_set(),
                    }
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.publish(
                {
                    "type": "error",
                    "session_id": session_id,
                    "content": str(exc),
                    "provider": provider,
                    "model": model,
                }
            )
        finally:
            current = self._jobs.get(session_id)
            if current is not None and current.cancel_event is cancel_event:
                self._jobs.pop(session_id, None)
