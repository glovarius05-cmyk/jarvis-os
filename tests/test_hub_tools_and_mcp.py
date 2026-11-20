"""Tests for built-in Vault/Web tools, SSRF guard, external HTTP MCP client, and /hub/mcp endpoint."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.brain.vault import VaultManager
from app.container import AppContainer
from app.hub.builtin_tools import is_safe_public_url
from app.hub.gateway import MCPHubGateway
from app.hub.mcp_client import MCPConnection
from app.main import create_app
from app.security.secrets import SecretStore


def test_ssrf_guard_blocks_private_and_non_http() -> None:
    for bad in (
        "http://127.0.0.1:7777/api/settings",
        "http://localhost/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/internal",
        "http://192.168.1.1/",
        "file:///etc/passwd",
        "ftp://example.com/a",
    ):
        ok, _ = is_safe_public_url(bad)
        assert ok is False, f"Expected SSRF guard to block {bad}"

    ok, _ = is_safe_public_url("https://8.8.8.8/")
    assert ok is True


@pytest.mark.asyncio
async def test_builtin_vault_tools_and_external_http_mcp_with_permissions(
    tmp_path: Path, project_root: Path
) -> None:
    def mock_mcp_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("X-Api-Key") == "secret-mcp-token"
        body = json.loads(request.content.decode())
        method = body["method"]
        if method == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "tools": [
                            {"name": "get_sales", "description": "Read sales", "inputSchema": {"type": "object"}},
                            {"name": "create_order", "description": "Create order", "inputSchema": {"type": "object"}},
                        ]
                    },
                },
            )
        if method == "tools/call":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"content": [{"type": "text", "text": f"called:{body['params']['name']}"}]},
                },
            )
        return httpx.Response(404)

    vaults = VaultManager(tmp_path / "brains")
    vaults.ensure_brain("Brain Default", init_git=False)
    secrets = SecretStore(tmp_path / "state")
    hub = MCPHubGateway(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        port=7777,
        vaults=vaults,
        secrets_store=secrets,
        http_transport=httpx.MockTransport(mock_mcp_handler),
    )

    # Vault write blocked in suggest (readonly) mode, allowed in auto/full
    res_denied = await hub.call_tool(
        name="vault_write",
        args={"path": "wiki/test.md", "content": "hello"},
        brain="Brain Default",
        run_mode="suggest",
    )
    assert "Permission denied" in res_denied

    res_ok = await hub.call_tool(
        name="vault_write",
        args={"path": "wiki/test.md", "content": "Line 1\nTarget line\n"},
        brain="Brain Default",
        run_mode="auto",
    )
    assert "Wrote" in res_ok

    res_edit = await hub.call_tool(
        name="vault_edit",
        args={"path": "wiki/test.md", "old_text": "Target line", "new_text": "Updated line"},
        brain="Brain Default",
        run_mode="auto",
    )
    assert "Updated" in res_edit

    res_search = await hub.call_tool(
        name="vault_search",
        args={"query": "Updated line"},
        brain="Brain Default",
        run_mode="suggest",
    )
    assert "wiki/test.md:2" in res_search

    # Path traversal blocked even in full mode
    res_trav = await hub.call_tool(
        name="vault_read",
        args={"path": "../../etc/passwd"},
        brain="Brain Default",
        run_mode="full",
    )
    assert res_trav.startswith("ERROR:")

    # Register external HTTP MCP connection with perm='readonly' and encrypted header
    hub.connections.upsert(
        MCPConnection(
            id="shop",
            name="Demo Shop",
            transport="http",
            perm="readonly",
            url="https://mock-mcp.local/mcp",
            headers={"X-Api-Key": "secret-mcp-token"},
        )
    )
    raw_store = (tmp_path / "state" / "mcp_servers.json").read_text(encoding="utf-8")
    assert "secret-mcp-token" not in raw_store
    assert "enc:" in raw_store

    # Read tool works; write tool blocked by connection's readonly perm even when run_mode='full'!
    sales = await hub.call_tool(name="mcp__shop__get_sales", args={}, brain="Brain Default", run_mode="full")
    assert sales == "called:get_sales"

    blocked_order = await hub.call_tool(name="mcp__shop__create_order", args={}, brain="Brain Default", run_mode="full")
    assert "Permission denied" in blocked_order

    # Audit log recorded both ALLOW and DENY entries
    entries = hub.audit.recent(limit=20)
    assert any(e["tool"] == "vault_write" and e["allowed"] is False for e in entries)
    assert any(e["tool"] == "mcp__shop__get_sales" and e["allowed"] is True for e in entries)
    assert any(e["tool"] == "mcp__shop__create_order" and e["allowed"] is False for e in entries)


def test_hub_mcp_http_jsonrpc_endpoint_auth_and_mode_enforcement(
    tmp_path: Path, project_root: Path
) -> None:
    ctr = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        force_login=False,
    )
    app = create_app(ctr)
    token = ctr.hub.hub_token()

    with TestClient(app) as tc:
        # Missing or wrong bearer token -> 401
        assert tc.post("/hub/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}).status_code == 401
        assert (
            tc.post(
                "/hub/mcp",
                headers={"Authorization": "Bearer wrong-token"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            ).status_code
            == 401
        )

        # Valid initialize + tools/list under suggest mode omits vault_write
        r_init = tc.post(
            "/hub/mcp",
            headers={"Authorization": f"Bearer {token}", "X-Jarvis-Mode": "suggest"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        )
        assert r_init.status_code == 200
        assert r_init.json()["result"]["serverInfo"]["name"] == "jarvis-mcp-hub"

        r_list = tc.post(
            "/hub/mcp",
            headers={"Authorization": f"Bearer {token}", "X-Jarvis-Mode": "suggest"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        tool_names = {t["name"] for t in r_list.json()["result"]["tools"]}
        assert "vault_read" in tool_names
        assert "vault_write" not in tool_names

        # Direct tools/call to vault_write under X-Jarvis-Mode: suggest is hard-blocked
        r_call = tc.post(
            "/hub/mcp",
            headers={"Authorization": f"Bearer {token}", "X-Jarvis-Mode": "suggest"},
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "vault_write", "arguments": {"path": "x.md", "content": "hi"}},
            },
        )
        assert r_call.json()["result"]["isError"] is True
        assert "Permission denied" in r_call.json()["result"]["content"][0]["text"]
