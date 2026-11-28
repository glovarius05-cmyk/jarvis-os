"""Tests for new model providers: Perplexity, DeepSeek, Moonshot (Kimi), and Alibaba DashScope (Qwen)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import SettingsStore
from app.engines.api_engine import OpenAICompatEngine
from app.engines.base import EngineRequest
from app.engines.router import PROVIDER_MAP, EngineRouter
from app.security.secrets import SecretStore


def test_provider_specs_registered() -> None:
    expected_providers = ["perplexity", "deepseek", "moonshot", "dashscope"]
    for pid in expected_providers:
        assert pid in PROVIDER_MAP, f"Provider {pid} should be registered in PROVIDER_MAP"
        spec = PROVIDER_MAP[pid]
        assert spec.kind == "api"
        assert len(spec.suggested_models) > 0
        assert spec.key_field.endswith("_api_key")

    or_spec = PROVIDER_MAP["openrouter"]
    assert len(or_spec.suggested_models) > 0
    assert all(m.endswith(":free") for m in or_spec.suggested_models)


def test_router_builds_new_engines(tmp_path: Path) -> None:
    sec = SecretStore(tmp_path / "secrets")
    store = SettingsStore(tmp_path / "state", sec)
    store.update({
        "perplexity_api_key": "test-pplx-key",
        "deepseek_api_key": "test-deepseek-key",
        "moonshot_api_key": "test-kimi-key",
        "dashscope_api_key": "test-qwen-key",
    })

    router = EngineRouter(settings=store)

    engine_pplx = router.build_engine("perplexity")
    assert isinstance(engine_pplx, OpenAICompatEngine)
    assert engine_pplx._base_url == "https://api.perplexity.ai"
    assert engine_pplx._api_key == "test-pplx-key"

    engine_deepseek = router.build_engine("deepseek")
    assert isinstance(engine_deepseek, OpenAICompatEngine)
    assert engine_deepseek._base_url == "https://api.deepseek.com"
    assert engine_deepseek._api_key == "test-deepseek-key"

    engine_kimi = router.build_engine("moonshot")
    assert isinstance(engine_kimi, OpenAICompatEngine)
    assert engine_kimi._base_url == "https://api.moonshot.cn/v1"
    assert engine_kimi._api_key == "test-kimi-key"

    engine_qwen = router.build_engine("dashscope")
    assert isinstance(engine_qwen, OpenAICompatEngine)
    assert engine_qwen._base_url == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    assert engine_qwen._api_key == "test-qwen-key"


@pytest.mark.asyncio
async def test_reasoning_models_strip_tools() -> None:
    captured_payloads: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_payloads.append(data)
        sse = 'data: {"choices":[{"delta":{"content":"Thinking complete."}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, content=sse.encode("utf-8"), headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    engine = OpenAICompatEngine(
        provider_id="deepseek",
        base_url="https://api.deepseek.com",
        api_key="sk-test",
        transport=transport,
    )

    req = EngineRequest(
        session_id="test_sess",
        brain="Brain Default",
        provider="deepseek",
        system_prompt="Test system",
        messages=[{"role": "user", "content": "Explain relativity"}],
        model="deepseek-reasoner",
        tools=[{"name": "calculator", "description": "calc", "parameters": {}}],
    )

    events = [e async for e in engine.stream(req)]
    assert any(e.type == "text" and "Thinking complete" in e.content for e in events)
    assert len(captured_payloads) == 1
    # Tools should be omitted for deepseek-reasoner
    assert "tools" not in captured_payloads[0]


@pytest.mark.asyncio
async def test_reasoning_content_delta_streaming() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        sse = (
            'data: {"choices":[{"delta":{"reasoning_content":"Let us analyze step 1."}}]}\n\n'
            'data: {"choices":[{"delta":{"content":" Final conclusion."}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, content=sse.encode("utf-8"), headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    engine = OpenAICompatEngine(
        provider_id="deepseek",
        base_url="https://api.deepseek.com",
        api_key="sk-test",
        transport=transport,
    )

    req = EngineRequest(
        session_id="test_sess",
        brain="Brain Default",
        provider="deepseek",
        system_prompt="Test system",
        messages=[{"role": "user", "content": "Think deeply"}],
        model="deepseek-r1",
    )

    events = [e async for e in engine.stream(req)]
    text_parts = [e.content for e in events if e.type == "text"]
    assert "Let us analyze step 1." in text_parts
    assert " Final conclusion." in text_parts
