"""Unit tests for the OpenAI-compatible /v1 API routes."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.container import AppContainer
from app.engines.base import EngineEvent, EngineProtocol, EngineRequest
from app.main import create_app


class MockEngine(EngineProtocol):
    def __init__(self, reply: str = "Hello from Jarvis!") -> None:
        self.reply = reply

    async def stream(self, request: EngineRequest):
        yield EngineEvent(type="text", content=self.reply)
        yield EngineEvent(type="final", content=self.reply, usage={"input_tokens": 12, "output_tokens": 8})


@pytest.fixture
def v1_client(tmp_path: Path):
    container = AppContainer.build(
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        force_login=False,
    )
    # Use mock engine in router
    container.engine_router._engine_override = MockEngine("Hello from Jarvis v1!")
    app = create_app(container, enable_scheduler=False)
    with TestClient(app) as client:
        yield client, container


def test_v1_models_list(v1_client):
    client, _ = v1_client
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    ids = [m["id"] for m in data["data"]]
    assert "jarvis-os" in ids
    assert "jarvis-architect" in ids
    assert "jarvis-researcher" in ids


def test_v1_chat_completions_non_streaming(v1_client):
    client, container = v1_client
    container.chat_runtime._engine_override = MockEngine("Structured answer from Jarvis")

    payload = {
        "model": "jarvis-os",
        "messages": [
            {"role": "user", "content": "How are you?"}
        ],
        "stream": False,
    }
    resp = client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["object"] == "chat.completion"
    assert res_data["choices"][0]["message"]["role"] == "assistant"
    assert "Hello from Jarvis v1!" in res_data["choices"][0]["message"]["content"]
    assert res_data["usage"]["total_tokens"] == 20


def test_v1_chat_completions_streaming(v1_client):
    client, _ = v1_client
    payload = {
        "model": "jarvis-architect",
        "messages": [
            {"role": "user", "content": "Audit this design"}
        ],
        "stream": True,
    }
    resp = client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    text = resp.text
    assert "data: " in text
    assert "[DONE]" in text
    assert "Hello from Jarvis v1!" in text


def test_v1_auth_rejection_when_login_required(tmp_path: Path):
    container = AppContainer.build(
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        force_login=True,
    )
    container.auth.setup_admin("admin", "secret_password_123")
    assert container.auth.login_required() is True

    app = create_app(container, enable_scheduler=False)
    with TestClient(app) as client:
        # Without key -> 401
        res_fail = client.get("/v1/models")
        assert res_fail.status_code == 401

        # With wrong key -> 401
        res_fail2 = client.get("/v1/models", headers={"Authorization": "Bearer bad_token"})
        assert res_fail2.status_code == 401

        # With correct admin password as Bearer -> 200
        res_ok = client.get("/v1/models", headers={"Authorization": "Bearer secret_password_123"})
        assert res_ok.status_code == 200

        # With hub token -> 200
        hub_tok = container.hub.hub_token()
        res_ok2 = client.get("/v1/models", headers={"Authorization": f"Bearer {hub_tok}"})
        assert res_ok2.status_code == 200
