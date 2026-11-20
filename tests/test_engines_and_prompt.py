"""Tests for API engines (streaming + tool loops), text sanitizer, prompt builder, and router."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.brain.prompt_builder import PromptBuilder
from app.brain.vault import VaultManager
from app.config import SettingsStore
from app.engines.api_engine import GeminiApiEngine, OpenAICompatEngine
from app.engines.base import EngineRequest, sanitize_text
from app.engines.router import EngineRouter
from app.security.secrets import SecretStore


def test_sanitize_text_strips_surrogates_and_pua() -> None:
    dirty = "Revenue is 42\ud83d and \ue200cite\ue202turn1\ue201 done."
    cleaned = sanitize_text(dirty)
    assert "\ud83d" not in cleaned
    assert "\ue200" not in cleaned
    assert "Revenue is 42\ufffd and citeturn1 done." == cleaned


def test_prompt_builder_assembles_kernel_memory_and_runtime(tmp_path: Path, project_root: Path) -> None:
    vaults = VaultManager(tmp_path / "brains")
    vaults.ensure_brain("Brain Default", init_git=False)
    vaults.write_text("Brain Default", "memory/MEMORY.md", "- Owner uses local Llama for background jobs\n")

    builder = PromptBuilder(project_root / "prompts", vaults)
    prompt = builder.build(
        brain="Brain Default",
        provider="local-llama",
        model="llama3.1:8b",
        channel="web",
        session_id="sess_123",
    )

    assert "JARVIS OS - System Prompt Kernel" in prompt
    assert "Active engine: `local-llama`" in prompt
    assert "Active model: `llama3.1:8b`" in prompt
    assert "Owner uses local Llama for background jobs" in prompt
    assert "Current session id: `sess_123`" in prompt
    assert "# === LANGUAGE ===" in prompt


@pytest.mark.asyncio
async def test_openai_compat_engine_streaming_and_tool_loop() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            sse = (
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"tc_1","function":{"name":"get_sum","arguments":"{\\"a\\":2,"}}]}}]}\n\n'
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"b\\":3}"}}]}}]}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

        body = json.loads(request.content.decode())
        assert any(m.get("role") == "tool" and m.get("content") == "5" for m in body["messages"])
        sse2 = (
            'data: {"choices":[{"delta":{"content":"Sum is "}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"5."}}],"usage":{"prompt_tokens":10,"completion_tokens":4}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, content=sse2.encode(), headers={"content-type": "text/event-stream"})

    engine = OpenAICompatEngine(
        provider_id="openrouter",
        base_url="https://mock.local/v1",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )

    async def exec_tool(name: str, args: dict) -> str:
        assert name == "get_sum"
        return str(args["a"] + args["b"])

    req = EngineRequest(
        session_id="s1",
        brain="Brain Default",
        system_prompt="You are Jarvis.",
        messages=[{"role": "user", "content": "Add 2 and 3"}],
        provider="openrouter",
        model="openrouter/auto",
        tools=[{"name": "get_sum", "description": "Add", "parameters": {"type": "object"}}],
        tool_executor=exec_tool,
    )

    events = [ev async for ev in engine.stream(req)]
    types = [e.type for e in events]
    assert "tool_call" in types
    assert "tool_result" in types
    assert events[-1].type == "final"
    assert events[-1].content == "Sum is 5."
    assert events[-1].usage["input_tokens"] == 10


@pytest.mark.asyncio
async def test_gemini_api_engine_streaming_and_function_call() -> None:
    turn = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal turn
        turn += 1
        if turn == 1:
            chunk = {"candidates": [{"content": {"parts": [{"functionCall": {"name": "ping", "args": {"x": 1}}}]}}]}
            sse = f"data: {json.dumps(chunk)}\n\n"
            return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

        chunk2 = {
            "candidates": [{"content": {"parts": [{"text": "Pong received!"}]}}],
            "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3},
        }
        sse2 = f"data: {json.dumps(chunk2)}\n\n"
        return httpx.Response(200, content=sse2.encode(), headers={"content-type": "text/event-stream"})

    engine = GeminiApiEngine(api_key="gem-key", transport=httpx.MockTransport(handler))

    async def exec_tool(name: str, args: dict) -> str:
        return f"pong:{name}:{args['x']}"

    req = EngineRequest(
        session_id="s2",
        brain="Brain Default",
        system_prompt="You are Jarvis.",
        messages=[{"role": "user", "content": "Ping"}],
        provider="gemini-api",
        model="gemini-2.0-flash",
        tools=[{"name": "ping", "description": "Ping", "parameters": {"type": "object"}}],
        tool_executor=exec_tool,
    )

    events = [ev async for ev in engine.stream(req)]
    assert any(e.type == "tool_call" and e.tool_name == "ping" for e in events)
    assert events[-1].type == "final"
    assert events[-1].content == "Pong received!"


@pytest.mark.asyncio
async def test_gemini_api_engine_schema_sanitization() -> None:
    captured_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.read().decode())
        captured_payloads.append(data)
        chunk = {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}
        sse = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

    engine = GeminiApiEngine(api_key="gem-key", transport=httpx.MockTransport(handler))

    req = EngineRequest(
        session_id="s_gemini_clean",
        brain="Brain Default",
        system_prompt="You are Jarvis.",
        messages=[{"role": "user", "content": "Check email"}],
        provider="gemini-api",
        model="gemini-2.0-flash",
        tools=[
            {
                "name": "mcp__composio__COMPOSIO_SEARCH",
                "description": "Search tools",
                "parameters": {
                    "$schema": "http://json-schema.org/draft-07/schema#",
                    "type": "object",
                    "additionalProperties": False,
                    "title": "SearchArgs",
                    "properties": {
                        "queries": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "additionalProperties": False,
                                "default": "inbox",
                            },
                        }
                    },
                    "required": ["queries"],
                },
            }
        ],
    )

    events = [ev async for ev in engine.stream(req)]
    assert len(captured_payloads) == 1
    sent_payload = captured_payloads[0]

    decls = sent_payload["tools"][0]["functionDeclarations"]
    assert len(decls) == 1
    params = decls[0]["parameters"]

    # Verify Gemini-incompatible fields are completely stripped
    assert "$schema" not in params
    assert "additionalProperties" not in params
    assert "title" not in params
    assert "additionalProperties" not in params["properties"]["queries"]["items"]
    assert "default" not in params["properties"]["queries"]["items"]

    # Verify standard valid schema structure is preserved
    assert params["type"] == "object"
    assert "queries" in params["properties"]
    assert params["properties"]["queries"]["type"] == "array"
    assert params["properties"]["queries"]["items"]["type"] == "string"
    assert params["required"] == ["queries"]

    assert events[-1].type == "final"
    assert events[-1].content == "OK"



def test_engine_router_resolution_priority(tmp_path: Path) -> None:
    settings = SettingsStore(tmp_path, SecretStore(tmp_path))
    router = EngineRouter(settings)

    # Default main and aux roles
    assert router.resolve_target(role="main") == ("openrouter", "openrouter/auto")
    assert router.resolve_target(role="aux") == ("local-llama", "llama3.1:8b")

    # Session pinned beats main default
    assert router.resolve_target(pinned_provider="gemini-api", pinned_model="gemini-2.5-pro") == (
        "gemini-api",
        "gemini-2.5-pro",
    )

    # Explicit per-turn override beats pinned
    assert router.resolve_target(
        override_provider="local-llama",
        override_model="llama3.2:3b",
        pinned_provider="gemini-api",
    ) == ("local-llama", "llama3.2:3b")


@pytest.mark.asyncio
async def test_local_llama_num_ctx_and_mcp_filtering() -> None:
    captured_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.read().decode())
        captured_payloads.append(data)
        sse = 'data: {"choices":[{"delta":{"content":"Привет"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

    engine = OpenAICompatEngine(
        provider_id="local-llama",
        base_url="http://127.0.0.1:11434/v1",
        num_ctx=16384,
        transport=httpx.MockTransport(handler),
    )

    req = EngineRequest(
        session_id="s_local",
        brain="Brain Default",
        system_prompt="You are Jarvis.",
        messages=[{"role": "user", "content": "Привет"}],
        provider="local-llama",
        model="hf.co/gguf/Qwen3.5-2B-GGUF:Q4_K_M",
        tools=[
            {"name": "vault_read", "description": "Read file", "parameters": {}},
            {"name": "mcp__composio__COMPOSIO_SEARCH", "description": "Huge remote schema", "parameters": {}},
        ],
    )

    events = [ev async for ev in engine.stream(req)]
    assert len(captured_payloads) == 1
    sent_payload = captured_payloads[0]

    # 1. num_ctx is configured in options
    assert sent_payload.get("options", {}).get("num_ctx") == 16384

    # 2. Remote MCP tool is filtered out to avoid context overflow for local models
    tool_names = [t["function"]["name"] for t in sent_payload.get("tools", [])]
    assert "vault_read" in tool_names
    assert "mcp__composio__COMPOSIO_SEARCH" not in tool_names

    # 3. Streamed result is received cleanly
    assert events[-1].type == "final"
    assert events[-1].content == "Привет"

