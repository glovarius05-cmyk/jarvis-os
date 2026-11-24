"""Tests for Stage 1 upgrades: OpenTelemetry/SQLite tracing, MCP Resources/Prompts, and Provider Fallback."""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.container import AppContainer
from app.engines.base import EngineEvent, EngineRequest
from app.engines.fallback import FallbackEngine, is_retryable_provider_error
from app.main import create_app


class _FailingEngine:
    def __init__(self, error_msg: str) -> None:
        self._error_msg = error_msg

    async def stream(self, req: EngineRequest) -> AsyncIterator[EngineEvent]:
        yield EngineEvent(type="error", content=self._error_msg)


class _SuccessEngine:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def stream(self, req: EngineRequest) -> AsyncIterator[EngineEvent]:
        yield EngineEvent(type="text", content=self._reply)
        yield EngineEvent(
            type="final",
            content=self._reply,
            usage={"input_tokens": 12, "output_tokens": 8},
        )


@pytest.mark.asyncio
async def test_telemetry_trace_lifecycle_and_api(tmp_path: Path) -> None:
    ctr = AppContainer.build(
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        force_login=False,
        engine_override=_SuccessEngine("Hello from traced engine"),
    )
    session = ctr.sessions.create_session(brain="Brain Default", title="Trace test")
    ctr.chat_runtime.start_turn(session_id=session.id, user_text="Ping telemetry")
    await ctr.chat_runtime.wait_turn(session.id, timeout=5.0)

    traces = ctr.telemetry.list_traces(session_id=session.id)
    assert len(traces) == 1
    tr = traces[0]
    assert tr["status"] == "ok"
    assert tr["input_tokens"] == 12
    assert tr["output_tokens"] == 8
    assert tr["span_count"] >= 1

    full = ctr.telemetry.get_trace(tr["trace_id"])
    assert full is not None
    assert any(s["kind"] == "llm" for s in full["spans"])

    # Verify HTTP endpoints
    app = create_app(container=ctr, enable_scheduler=False)
    with TestClient(app) as client:
        r_list = client.get("/api/telemetry/traces")
        assert r_list.status_code == 200
        assert r_list.json()["count"] >= 1

        r_detail = client.get(f"/api/telemetry/traces/{tr['trace_id']}")
        assert r_detail.status_code == 200
        assert r_detail.json()["trace_id"] == tr["trace_id"]

        r_sum = client.get("/api/telemetry/summary?days=7")
        assert r_sum.status_code == 200
        assert r_sum.json()["total_traces"] >= 1


@pytest.mark.asyncio
async def test_mcp_hub_resources_and_prompts_jsonrpc(tmp_path: Path) -> None:
    ctr = AppContainer.build(
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        force_login=False,
    )
    ctr.vaults.write_text("Brain Default", "wiki/architecture.md", "# Architecture\nFastAPI + MCP")

    init_res = await ctr.hub.handle_jsonrpc({"id": 1, "method": "initialize"}, brain="Brain Default")
    caps = init_res["result"]["capabilities"]
    assert "tools" in caps
    assert "resources" in caps
    assert "prompts" in caps

    # resources/list & resources/read
    res_list = await ctr.hub.handle_jsonrpc({"id": 2, "method": "resources/list"}, brain="Brain Default")
    uris = [r["uri"] for r in res_list["result"]["resources"]]
    assert "jarvis://vault/wiki/architecture.md" in uris

    res_read = await ctr.hub.handle_jsonrpc(
        {"id": 3, "method": "resources/read", "params": {"uri": "jarvis://vault/wiki/architecture.md"}},
        brain="Brain Default",
    )
    assert "FastAPI + MCP" in res_read["result"]["contents"][0]["text"]

    # Path traversal must be rejected
    bad_read = await ctr.hub.handle_jsonrpc(
        {"id": 4, "method": "resources/read", "params": {"uri": "jarvis://vault/../../etc/passwd"}},
        brain="Brain Default",
    )
    assert "error" in bad_read

    # prompts/list & prompts/get (exposing enabled SKILL.md skills)
    p_list = await ctr.hub.handle_jsonrpc({"id": 5, "method": "prompts/list"}, brain="Brain Default")
    prompts = p_list["result"]["prompts"]
    assert len(prompts) >= 1
    first_slug = prompts[0]["name"]

    p_get = await ctr.hub.handle_jsonrpc(
        {"id": 6, "method": "prompts/get", "params": {"name": first_slug, "arguments": {"input": "Check repo"}}},
        brain="Brain Default",
    )
    msg_text = p_get["result"]["messages"][0]["content"]["text"]
    assert "Check repo" in msg_text


@pytest.mark.asyncio
async def test_fallback_engine_cascades_on_transient_error(tmp_path: Path) -> None:
    ctr = AppContainer.build(
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        force_login=False,
    )
    ctr.settings.update(
        {
            "gemini_api_key": "test-gem-key",
            "openrouter_api_key": "test-or-key",
            "model": {"fallback_chain": ["openrouter::deepseek/deepseek-chat", "local-llama"]},
        }
    )

    assert is_retryable_provider_error("HTTP 429 rate limit exceeded")
    assert is_retryable_provider_error("503 Service Unavailable")
    assert not is_retryable_provider_error("Invalid request schema")

    # Router wraps engine in FallbackEngine when fallback_chain is configured
    built = ctr.engine_router.build_engine("gemini-api", brain="Brain Default")
    assert isinstance(built, FallbackEngine)

    # Simulate primary returning 503 and fallback succeeding
    class _StubRouter:
        def build_engine(self, provider: str, **kwargs):
            return _SuccessEngine(f"Recovered via {provider}")

    handle = ctr.telemetry.start_trace(brain="Brain Default", provider="gemini-api", model="gemini-3-flash-preview")
    fb = FallbackEngine(
        primary_engine=_FailingEngine("HTTP 503 Service Unavailable"),
        primary_provider="gemini-api",
        primary_model="gemini-3-flash-preview",
        fallback_targets=[("openrouter", "deepseek/deepseek-chat")],
        router=_StubRouter(),  # type: ignore[arg-type]
        brain="Brain Default",
        telemetry=ctr.telemetry,
    )
    req = EngineRequest(
        session_id="s1",
        brain="Brain Default",
        system_prompt="sys",
        messages=[{"role": "user", "content": "hi"}],
        provider="gemini-api",
        model="gemini-3-flash-preview",
    )
    events = []
    with ctr.telemetry.bind_trace(handle):
        async for ev in fb.stream(req):
            events.append(ev)

    assert any(e.type == "text" and "Recovered via openrouter" in e.content for e in events)
    final_ev = next(e for e in events if e.type == "final")
    assert final_ev.usage["provider"] == "openrouter"
    assert final_ev.usage["fallback_from"] == "gemini-api/gemini-3-flash-preview"

    trace = ctr.telemetry.get_trace(handle.trace_id)
    assert trace is not None
    assert any(s["kind"] == "fallback" for s in trace["spans"])
