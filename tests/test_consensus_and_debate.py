"""Tests for Multi-Model Consensus, Adversarial Debate, and Gemini Fallback."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest

from app.config import SettingsStore
from app.engines.api_engine import GeminiApiEngine
from app.engines.base import EngineEvent, EngineProtocol, EngineRequest
from app.engines.consensus import ConsensusEngine
from app.engines.router import EngineRouter
from app.hub.builtin_tools import build_builtin_tools
from app.hub.shaper import ToolResultStore
from app.security.secrets import SecretStore


class MockEngine:
    def __init__(self, response_text: str = "") -> None:
        self._text = response_text

    async def stream(self, request: EngineRequest) -> AsyncIterator[EngineEvent]:
        prompt = request.system_prompt + " " + (request.messages[0]["content"] if request.messages else "")
        if "Арбитр" in prompt or "Arbiter" in prompt or "Консенсус" in prompt:
            content = f"Synthesized Verdict: Consensus reached. {self._text}"
        elif "оппонент" in prompt or "Critique" in prompt:
            content = f"Critique: Flaws found in proposal. {self._text}"
        else:
            content = f"Proposal: Base thesis. {self._text}"

        yield EngineEvent(type="text", content=content, provider="mock", model="mock-model")
        yield EngineEvent(
            type="final",
            content=content,
            provider="mock",
            model="mock-model",
            usage={"input_tokens": 10, "output_tokens": 20},
        )


class MockRouter:
    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self._responses = responses or {}
        self.settings = None

    def build_engine(
        self,
        provider: str,
        *,
        brain: str = "",
        run_mode: str = "full",
        model: str = "",
    ) -> EngineProtocol:
        resp = self._responses.get(f"{provider}:{model}", f"Response from {provider}:{model}")
        return MockEngine(resp)

    def resolve_target(self, pinned_provider: str = "", pinned_model: str = "") -> tuple[str, str]:
        return "openrouter", "google/gemini-2.0-flash-exp:free"

    def resolve_secondary_target(self, primary_provider: str, cfg: dict[str, Any]) -> tuple[str, str]:
        return "local-llama", "hf.co/gguf/Qwen3.5-2B-GGUF:Q4_K_M"


@pytest.mark.asyncio
async def test_consensus_engine_parallel_synthesis() -> None:
    router = MockRouter({
        "gemini-api:gemini-3.5-flash-lite": "Gemini recommends Redis caching with TTL.",
        "openrouter:google/gemma-4-31b-it:free": "OpenRouter recommends SQLite with WAL mode.",
    })
    engine = ConsensusEngine(
        primary_provider="gemini-api",
        primary_model="gemini-3.5-flash-lite",
        secondary_provider="openrouter",
        secondary_model="google/gemma-4-31b-it:free",
        router=router,  # type: ignore[arg-type]
        mode="consensus",
    )

    req = EngineRequest(
        session_id="test",
        brain="Brain Default",
        provider="gemini-api",
        model="gemini-3.5-flash-lite",
        messages=[{"role": "user", "content": "How to optimize state storage?"}],
        system_prompt="System instructions",
    )

    events: list[EngineEvent] = []
    async for ev in engine.stream(req):
        events.append(ev)

    types = [e.type for e in events]
    assert "status" in types
    assert "text" in types
    assert "final" in types

    final_ev = next(e for e in events if e.type == "final")
    assert final_ev.content
    assert final_ev.provider == "consensus"
    assert "Consensus Confidence" in final_ev.content or "Synthesized Verdict" in final_ev.content
    assert final_ev.usage.get("output_tokens", 0) > 0


@pytest.mark.asyncio
async def test_consensus_engine_debate_mode() -> None:
    router = MockRouter()
    engine = ConsensusEngine(
        primary_provider="gemini-api",
        primary_model="gemini-3.5-flash-lite",
        secondary_provider="local-llama",
        secondary_model="hf.co/gguf/Qwen3.5-2B-GGUF:Q4_K_M",
        router=router,  # type: ignore[arg-type]
        mode="debate",
    )

    req = EngineRequest(
        session_id="test",
        brain="Brain Default",
        provider="gemini-api",
        model="gemini-3.5-flash-lite",
        messages=[{"role": "user", "content": "Should we use Microservices or Monolith?"}],
        system_prompt="System prompt",
    )

    events: list[EngineEvent] = []
    async for ev in engine.stream(req):
        events.append(ev)

    status_events = [e for e in events if e.type == "status"]
    assert len(status_events) >= 2
    assert any(s.provider == "debate" or "Раунд" in s.content for s in status_events)

    final_ev = next(e for e in events if e.type == "final")
    assert final_ev.provider == "debate"
    assert "Proposal" in final_ev.content or "Verdict" in final_ev.content


def test_engine_router_builds_consensus_and_debate(tmp_path: Path) -> None:
    secret_store = SecretStore(tmp_path)
    settings_store = SettingsStore(tmp_path, secret_store)
    settings_store.update({
        "openrouter_api_key": "sk-or-test",
        "gemini_api_key": "test-gemini-key",
    })
    router = EngineRouter(settings_store)

    engine_consensus = router.build_engine("openrouter", run_mode="consensus", model="google/gemini-2.0-flash-exp:free")
    assert isinstance(engine_consensus, ConsensusEngine)
    assert engine_consensus._mode == "consensus"

    engine_debate = router.build_engine("gemini-api", run_mode="debate", model="gemini-3.5-flash-lite")
    assert isinstance(engine_debate, ConsensusEngine)
    assert engine_debate._mode == "debate"


@pytest.mark.asyncio
async def test_builtin_consensus_and_debate_tools(tmp_path: Path) -> None:
    from app.brain.vault import VaultManager

    vaults = VaultManager(tmp_path / "brains")
    vaults.ensure_brain("Brain Default", init_git=False)
    secret_store = SecretStore(tmp_path)
    result_store = ToolResultStore(tmp_path, secret_store)
    settings_store = SettingsStore(tmp_path, secret_store)
    settings_store.update({"openrouter_api_key": "test", "gemini_api_key": "test"})

    mock_router = MockRouter()
    mock_router.settings = settings_store  # type: ignore[assignment]

    tools = build_builtin_tools(
        vaults=vaults,
        result_store=result_store,
        list_connections_fn=lambda: [],
        router_getter=lambda: mock_router,
    )

    tool_map = {t.name: t for t in tools}
    assert "jarvis_consensus" in tool_map
    assert "jarvis_debate" in tool_map

    # Test consensus tool handler
    res_c = await tool_map["jarvis_consensus"].handler({"query": "Compare Rust vs Go"}, "Brain Default")
    assert res_c and not res_c.startswith("ERROR:")

    # Test debate tool handler
    res_d = await tool_map["jarvis_debate"].handler({"topic": "Dynamic typing vs Static typing"}, "Brain Default")
    assert res_d and not res_d.startswith("ERROR:")


@pytest.mark.asyncio
async def test_gemini_fallback_on_404_and_503() -> None:
    attempt = 0

    def mock_transport_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt
        attempt += 1
        url_str = str(request.url)
        # First call with deprecated model returns 404
        if "gemini-2.5-flash" in url_str:
            return httpx.Response(
                404,
                json={"error": {"code": 404, "message": "This model is no longer available to new users."}},
            )
        # Second call to fallback succeeds with SSE stream
        sse_chunk = 'data: {"candidates":[{"content":{"parts":[{"text":"Fallback answer OK"}]}}]}\n\n'
        return httpx.Response(200, text=sse_chunk, headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(mock_transport_handler)
    engine = GeminiApiEngine(api_key="test-key", transport=transport)

    req = EngineRequest(
        session_id="test",
        brain="Brain Default",
        provider="gemini-api",
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": "Hello"}],
        system_prompt="Test",
    )

    events: list[EngineEvent] = []
    async for ev in engine.stream(req):
        events.append(ev)

    # Verify fallback event was emitted
    status_events = [e for e in events if e.type == "status"]
    assert any("404" in s.content for s in status_events)
    # Verify successful completion from fallback model
    final_ev = next((e for e in events if e.type == "final"), None)
    assert final_ev is not None
    assert "Fallback answer OK" in final_ev.content
