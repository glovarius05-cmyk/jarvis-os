"""Tests for server-owned ChatRuntime (F5 resilience, coalescing, cancellation) and /ws/chat."""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.container import AppContainer
from app.engines.base import EngineEvent, EngineRequest
from app.main import create_app
from app.runtime.chat_runtime import coalesce_event


class _SlowFakeEngine:
    """Streams deterministic chunks with small delays to test disconnect survival and stop."""

    def __init__(self, chunks: list[str], delay: float = 0.02) -> None:
        self._chunks = chunks
        self._delay = delay

    async def stream(self, request: EngineRequest) -> AsyncIterator[EngineEvent]:
        acc = []
        for chunk in self._chunks:
            if request.cancel_event and request.cancel_event.is_set():
                break
            await asyncio.sleep(self._delay)
            acc.append(chunk)
            yield EngineEvent(type="text", content=chunk, provider=request.provider, model=request.model)
        yield EngineEvent(type="final", content="".join(acc), provider=request.provider, model=request.model)


def test_coalesce_event_merges_adjacent_streams() -> None:
    q: deque[dict] = deque()
    coalesce_event(q, {"type": "stream", "session_id": "s1", "provider": "p", "model": "m", "content": "Hel"})
    coalesce_event(q, {"type": "stream", "session_id": "s1", "provider": "p", "model": "m", "content": "lo!"})
    coalesce_event(q, {"type": "final", "session_id": "s1", "content": "Hello!"})
    assert len(q) == 2
    assert q[0]["content"] == "Hello!"
    assert q[1]["type"] == "final"


@pytest.mark.asyncio
async def test_chat_runtime_survives_client_disconnect_and_persists(tmp_path: Path, project_root: Path) -> None:
    ctr = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        engine_override=_SlowFakeEngine(["Привет, ", "я ", "Джарвис!"], delay=0.02),
    )
    session = ctr.sessions.create_session(brain="Brain Default", title="New chat")
    received: list[dict] = []

    async def writer(ev: dict) -> None:
        received.append(ev)

    ctr.chat_runtime.add_client("tab1", writer)
    ctr.chat_runtime.start_turn(session_id=session.id, user_text="Поздоровайся")

    # Simulate browser tab closing / F5 mid-stream
    await asyncio.sleep(0.03)
    ctr.chat_runtime.remove_client("tab1")

    # Turn MUST still complete and persist to SQLite
    await ctr.chat_runtime.wait_turn(session.id, timeout=5.0)
    msgs = ctr.sessions.list_messages(session.id)
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[1].role == "assistant"
    assert msgs[1].content == "Привет, я Джарвис!"

    # Session auto-titled from first message
    refreshed = ctr.sessions.get_session(session.id)
    assert refreshed is not None
    assert refreshed.title == "Поздоровайся"


@pytest.mark.asyncio
async def test_chat_runtime_stop_cancels_in_flight_turn(tmp_path: Path, project_root: Path) -> None:
    ctr = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        engine_override=_SlowFakeEngine(["a", "b", "c", "d", "e", "f"], delay=0.05),
    )
    session = ctr.sessions.create_session(brain="Brain Default", title="Cancel test")
    ctr.chat_runtime.start_turn(session_id=session.id, user_text="Long answer")

    await asyncio.sleep(0.07)
    assert ctr.chat_runtime.stop_turn(session.id) is True
    await ctr.chat_runtime.wait_turn(session.id, timeout=5.0)

    msgs = ctr.sessions.list_messages(session.id)
    assert len(msgs) == 2
    assert msgs[1].meta.get("cancelled") is True
    assert len(msgs[1].content) < 6


def test_websocket_chat_end_to_end_and_providers_endpoint(tmp_path: Path, project_root: Path) -> None:
    ctr = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        engine_override=_SlowFakeEngine(["Hello ", "world!"], delay=0.005),
    )
    app = create_app(ctr)

    with TestClient(app) as tc:
        # Providers endpoint lists all 5 hybrid providers
        prov_resp = tc.get("/api/models/providers")
        assert prov_resp.status_code == 200
        ids = {p["id"] for p in prov_resp.json()["providers"]}
        assert {"openrouter", "local-llama", "gemini-api", "codex-cli", "antigravity-cli"} <= ids

        sess = tc.post("/api/sessions", json={"title": "WS Session"}).json()
        sid = sess["id"]

        with tc.websocket_connect("/ws/chat") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"

            ws.send_json({"type": "send", "session_id": sid, "content": "Say hello"})
            seen_types: list[str] = []
            final_ev: dict | None = None
            for _ in range(10):
                ev = ws.receive_json()
                seen_types.append(ev["type"])
                if ev["type"] == "final":
                    final_ev = ev
                    break

            assert "turn_start" in seen_types
            assert "stream" in seen_types
            assert final_ev is not None
            assert final_ev["content"] == "Hello world!"

        # Verify persisted in SQLite
        history = tc.get(f"/api/sessions/{sid}").json()["messages"]
        assert len(history) == 2
        assert history[1]["content"] == "Hello world!"
