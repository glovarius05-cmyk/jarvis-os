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
    assert "composio_gmail" in tool_names
    assert "composio_calendar" in tool_names

    # Check permission levels
    tool_map = {t["name"]: t for t in tools}
    assert tool_map["composio_status"]["required_perm"] == "readonly"
    assert tool_map["composio_connect"]["required_perm"] == "safe"
    assert tool_map["composio_feed"]["required_perm"] == "readonly"
    assert tool_map["composio_post"]["required_perm"] == "full"
    assert tool_map["composio_gmail"]["required_perm"] == "safe"
    assert tool_map["composio_calendar"]["required_perm"] == "safe"

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

    # 4. Connect with key succeeds and returns auth_url
    res_gmail = client.post("/api/hub/composio/connect", json={"app": "gmail"})
    assert res_gmail.status_code == 200
    assert "auth_url" in res_gmail.json()
    assert "gmail" in res_gmail.json()["auth_url"]

    res_cal = client.post("/api/hub/composio/connect", json={"app": "googlecalendar"})
    assert res_cal.status_code == 200
    assert "auth_url" in res_cal.json()
    assert "googlecalendar" in res_cal.json()["auth_url"]

    # 5. Confirm app endpoint persists and shows up in status
    res_conf = client.post("/api/hub/composio/confirm", json={"app": "gmail", "connected": True})
    assert res_conf.status_code == 200
    assert "gmail" in res_conf.json()["connected_apps"]

    res_st = client.get("/api/hub/composio/status")
    assert res_st.status_code == 200
    accounts = res_st.json()["accounts"]
    assert any(a["app"] == "gmail" for a in accounts)


def test_extract_app_slug() -> None:
    from app.api.routes.hub import extract_app_slug

    assert extract_app_slug({"toolkit": {"slug": "gmail"}}) == "gmail"
    assert extract_app_slug({"toolkit": {"slug": "google_calendar"}}) == "googlecalendar"
    assert extract_app_slug({"toolkit": {"slug": "googlecalendar"}}) == "googlecalendar"
    assert extract_app_slug({"appName": "Google Calendar"}) == "googlecalendar"
    assert extract_app_slug({"app": "x"}) == "twitter"
    assert extract_app_slug({"app": "twitter"}) == "twitter"
    assert extract_app_slug("calendar") == "googlecalendar"
    assert extract_app_slug("google-calendar") == "googlecalendar"
    assert extract_app_slug("gmail") == "gmail"


def test_extract_email_from_account() -> None:
    from app.api.routes.hub import extract_email_from_obj

    assert extract_email_from_obj({"email": "user@gmail.com"}) == "user@gmail.com"
    assert extract_email_from_obj({"alias": "my.user@company.com"}) == "my.user@company.com"
    assert extract_email_from_obj({"account_identifier": "dev@test.org"}) == "dev@test.org"
    assert extract_email_from_obj({"state": {"val": {"email": "nested@domain.com"}}}) == "nested@domain.com"
    assert extract_email_from_obj({"params": {"user_email": "params@domain.com"}}) == "params@domain.com"
    assert extract_email_from_obj({"raw": "random string with bob@customdomain.io inside"}) == "bob@customdomain.io"


@pytest.mark.asyncio
async def test_composio_gmail_and_calendar_tools_action_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    code_dir = Path(__file__).resolve().parent.parent
    ctr = AppContainer.build(
        code_dir=code_dir,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
    )
    ctr.settings.update({"composio_api_key": "ak_mock_live_test_key"})
    hub = ctr.hub

    captured_posts: list[dict] = []

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, headers=None):
            if "connected_accounts" in url:
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "id": "ca_test_gmail_12345",
                                "user_id": "default",
                                "status": "ACTIVE",
                                "toolkit": {"slug": "gmail"},
                                "alias": "metadata_user@gmail.com",
                            },
                            {
                                "id": "ca_test_cal_67890",
                                "user_id": "default",
                                "status": "ACTIVE",
                                "toolkit": {"slug": "googlecalendar"},
                            },
                        ]
                    },
                )
            return httpx.Response(404)

        async def post(self, url, headers=None, json=None):
            captured_posts.append({"url": url, "json": json})
            if "GMAIL_GET_PROFILE" in url:
                return httpx.Response(
                    200,
                    json={
                        "response_data": {
                            "emailAddress": "live_user@gmail.com",
                            "messagesTotal": 42,
                            "threadsTotal": 10,
                        }
                    },
                )
            if "GOOGLECALENDAR_LIST_EVENTS" in url:
                return httpx.Response(
                    200,
                    json={
                        "response_data": [
                            {"id": "ev1", "summary": "Team Sync"}
                        ]
                    },
                )
            return httpx.Response(500)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    # 1. Profile action fetches live profile and sends connected_account_id
    res_prof = await hub.call_tool(name="composio_gmail", args={"action": "profile"}, brain="Brain Default", run_mode="safe")
    assert "live_user@gmail.com" in res_prof
    assert "42" in res_prof

    # Verify connected_account_id was sent in the payload to Composio
    profile_calls = [p for p in captured_posts if "GMAIL_GET_PROFILE" in p["url"]]
    assert len(profile_calls) > 0
    assert profile_calls[0]["json"].get("connected_account_id") == "ca_test_gmail_12345"

    # 2. Status lists both Gmail (with email) and Calendar
    res_status = await hub.call_tool(name="composio_status", args={}, brain="Brain Default", run_mode="readonly")
    assert "GMAIL" in res_status
    assert "GOOGLECALENDAR" in res_status

    # 3. Calendar list action passes ca_test_cal_67890
    res_cal = await hub.call_tool(name="composio_calendar", args={"action": "list"}, brain="Brain Default", run_mode="safe")
    assert "Team Sync" in res_cal
    cal_calls = [p for p in captured_posts if "GOOGLECALENDAR_LIST_EVENTS" in p["url"]]
    assert len(cal_calls) > 0
    assert cal_calls[0]["json"].get("connected_account_id") == "ca_test_cal_67890"




