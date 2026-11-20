"""Tests for Composio Social Networks integration in Jarvis OS.

Verifies:
1. Fernet encryption of `composio_api_key` in SettingsStore.
2. Bundled plugin discovery and registration of all 4 tools.
3. Hard permission enforcement (read tools in readonly/safe, post tool strictly in full).
4. Platform-specific length constraints (e.g. 280 chars for Twitter).
5. FastAPI /api/hub/composio/* endpoints.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.api.routes.hub import router as hub_router
from app.brain.vault import VaultManager
from app.config import SettingsStore
from app.container import AppContainer
from app.hub.gateway import MCPHubGateway
from app.hub.permissions import PermLevel
from app.hub.plugins import PluginHost
from app.security.secrets import ENC_PREFIX, SecretStore


def test_composio_secret_encryption_and_masking(tmp_path: Path) -> None:
    secrets = SecretStore(tmp_path)
    settings = SettingsStore(tmp_path, secrets)

    public = settings.update({"composio_api_key": "comp_live_sec_99999"})

    assert "composio_api_key" not in public
    assert public["secrets_configured"]["composio_api_key"] is True

    raw_on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert raw_on_disk["composio_api_key"].startswith(ENC_PREFIX)
    assert "comp_live_sec_99999" not in json.dumps(raw_on_disk)

    decrypted = settings.load()
    assert decrypted["composio_api_key"] == "comp_live_sec_99999"


@pytest.mark.asyncio
async def test_composio_social_plugin_tools_and_permissions(tmp_path: Path) -> None:
    code_dir = Path(__file__).resolve().parent.parent
    ctr = AppContainer.build(
        code_dir=code_dir,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
    )
    hub = ctr.hub

    tools = await hub.list_tools_info(brain="Brain Default", run_mode="full")
    tool_names = {t["name"] for t in tools}
    assert "composio_status" in tool_names
    assert "composio_connect" in tool_names
    assert "composio_feed" in tool_names
    assert "composio_post" in tool_names

    # Check permission levels
    tool_map = {t["name"]: t for t in tools}
    assert tool_map["composio_status"]["required_perm"] == "readonly"
    assert tool_map["composio_connect"]["required_perm"] == "safe"
    assert tool_map["composio_feed"]["required_perm"] == "readonly"
    assert tool_map["composio_post"]["required_perm"] == "full"

    # Status check with no key configured
    status_out = await hub.call_tool(name="composio_status", args={}, brain="Brain Default", run_mode="suggest")
    assert "NOT configured" in status_out

    # Connect tool requires safe mode, blocked in suggest (readonly)
    connect_denied = await hub.call_tool(name="composio_connect", args={"app": "twitter"}, brain="Brain Default", run_mode="suggest")
    assert "Permission denied by MCP Hub" in connect_denied

    # In safe mode, connect tool runs (returns missing key error if unconfigured)
    connect_out = await hub.call_tool(name="composio_connect", args={"app": "twitter"}, brain="Brain Default", run_mode="auto")
    assert "ERROR: Composio API key is not configured" in connect_out

    # Post tool requires FULL mode: strictly denied in suggest and auto
    post_denied_suggest = await hub.call_tool(
        name="composio_post",
        args={"platform": "twitter", "content": "Hello world!"},
        brain="Brain Default",
        run_mode="suggest",
    )
    assert "Permission denied by MCP Hub" in post_denied_suggest
    assert "tool requires 'full'" in post_denied_suggest

    post_denied_auto = await hub.call_tool(
        name="composio_post",
        args={"platform": "twitter", "content": "Hello world!"},
        brain="Brain Default",
        run_mode="auto",
    )
    assert "Permission denied by MCP Hub" in post_denied_auto
    assert "tool requires 'full'" in post_denied_auto

    # In full mode, post tool runs but validates length
    long_tweet = "A" * 281
    len_err = await hub.call_tool(
        name="composio_post",
        args={"platform": "twitter", "content": long_tweet},
        brain="Brain Default",
        run_mode="full",
    )
    assert "exceeds maximum allowed for Twitter / X (280 chars)" in len_err


def test_composio_hub_endpoints(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from app.api.deps import get_container

    code_dir = Path(__file__).resolve().parent.parent
    container = AppContainer.build(
        code_dir=code_dir,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
    )

    app = FastAPI()
    app.include_router(hub_router)
    app.dependency_overrides[get_container] = lambda: container

    client = TestClient(app)

    # 1. Status without key
    res = client.get("/api/hub/composio/status")
    assert res.status_code == 200
    data = res.json()
    assert data["configured"] is False

    # 2. Connect without key returns 400
    res = client.post("/api/hub/composio/connect", json={"app": "twitter"})
    assert res.status_code == 400
    assert "Composio API key is not configured" in res.json()["detail"]

    # 3. Configure key and verify status updates
    container.settings.update({"composio_api_key": "comp_dummy_test_key"})
    res = client.get("/api/hub/composio/status")
    assert res.status_code == 200
    data = res.json()
    assert data["configured"] is True
