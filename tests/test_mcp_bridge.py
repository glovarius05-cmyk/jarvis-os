"""Tests for Jarvis OS Stdio MCP Bridge (Cursor, VS Code & Claude Desktop integration)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path so `cli` is importable
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from starlette.testclient import TestClient

from app.container import AppContainer


def test_bridge_find_default_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cli.jarvis_mcp_bridge import find_default_token

    # 1. Environment variable priority
    monkeypatch.setenv("JARVIS_HUB_TOKEN", "env-test-token-123")
    assert find_default_token(tmp_path) == "env-test-token-123"

    # 2. File lookup in state_dir
    monkeypatch.delenv("JARVIS_HUB_TOKEN")
    token_file = tmp_path / ".hub_token"
    token_file.write_text("file-test-token-456\n", encoding="utf-8")
    assert find_default_token(tmp_path) == "file-test-token-456"


def test_bridge_generate_config_snippet(capsys: pytest.CaptureFixture[str]) -> None:
    from cli.jarvis_mcp_bridge import generate_config_snippet

    generate_config_snippet("http://127.0.0.1:7777/hub/mcp", "test-token-789")
    captured = capsys.readouterr()
    cfg = json.loads(captured.out)

    assert "mcpServers" in cfg
    assert "jarvis" in cfg["mcpServers"]
    srv = cfg["mcpServers"]["jarvis"]
    assert "command" in srv
    assert srv["args"][0].endswith("jarvis_mcp_bridge.py")
    assert srv["env"]["JARVIS_HUB_URL"] == "http://127.0.0.1:7777/hub/mcp"
    assert srv["env"]["JARVIS_HUB_TOKEN"] == "test-token-789"


def test_bridge_offline_error_handling() -> None:
    from cli.jarvis_mcp_bridge import send_hub_request

    # Send to an unused local port
    resp = send_hub_request(
        "http://127.0.0.1:59999/hub/mcp",
        "invalid-token",
        {"jsonrpc": "2.0", "id": 42, "method": "tools/list"},
        timeout=1.0,
    )

    assert resp.get("id") == 42
    assert "error" in resp
    assert resp["error"]["code"] == -32000
    assert "offline or unreachable" in resp["error"]["message"].lower()


def test_bridge_stdio_subprocess_loop(tmp_path: Path) -> None:
    """Test full stdio pipe interaction: ping, initialize, and notifications."""
    bridge_script = Path(__file__).resolve().parents[1] / "cli" / "jarvis_mcp_bridge.py"

    proc = subprocess.Popen(
        [sys.executable, str(bridge_script), "--url", "http://127.0.0.1:59999/hub/mcp", "--token", "fake"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # 1. Ping (handled locally in bridge with zero network)
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n")
        # 2. Notification (no response line expected)
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        # 3. Content-Length framed ping
        ping_body = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        framed = f"Content-Length: {len(ping_body)}\r\n\r\n{ping_body}\n"
        proc.stdin.write(framed)
        proc.stdin.flush()

        # Read line 1
        line1 = proc.stdout.readline()
        data1 = json.loads(line1)
        assert data1["id"] == 1
        assert data1["result"] == {}

        # Read line 2 (from framed request)
        line2 = proc.stdout.readline()
        data2 = json.loads(line2)
        assert data2["id"] == 2
        assert data2["result"] == {}
    finally:
        proc.terminate()
        proc.wait(timeout=2.0)


def test_hub_mcp_config_api_endpoint(tmp_path: Path) -> None:
    from app.main import app

    container = AppContainer.build(
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
    )
    app.state.container = container

    client = TestClient(app, base_url="http://127.0.0.1:7777")
    resp = client.get("/api/hub/mcp-config")
    assert resp.status_code == 200
    data = resp.json()

    assert "cursor_config" in data
    assert "claude_desktop_config" in data
    assert "jarvis" in data["cursor_config"]["mcpServers"]
    assert "jarvis" in data["claude_desktop_config"]["mcpServers"]
    assert data["bridge_script"].endswith("jarvis_mcp_bridge.py")
    assert data["has_token"] is True
