"""Tests for Codex CLI and Antigravity CLI subscription adapters (parsing, token redaction, cancel)."""

from __future__ import annotations

import asyncio
import stat
import sys
from pathlib import Path

import pytest

from app.engines.base import EngineRequest
from app.engines.cli_adapters import AntigravityCliEngine, CodexCliEngine


def _write_mock_cli(script_path: Path, body: str) -> Path:
    script_path.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
    return script_path


@pytest.mark.asyncio
async def test_codex_cli_adapter_streams_and_redacts_hub_token(tmp_path: Path) -> None:
    secret_tok = "super-secret-hub-token-xyz"
    mock_bin = _write_mock_cli(
        tmp_path / "mock_codex.py",
        """
import json, os, sys
print(json.dumps({"type": "item.completed", "item": {"type": "mcp_tool_call", "tool": "vault_read"}}))
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Token leak test: " + os.environ["JARVIS_HUB_TOKEN"]}}))
""",
    )

    engine = CodexCliEngine(
        binary=str(mock_bin),
        hub_url="http://127.0.0.1:7777/hub/mcp",
        hub_token=secret_tok,
        cwd=tmp_path,
        run_mode="auto",
    )
    req = EngineRequest(
        session_id="s1",
        brain="Brain Default",
        system_prompt="You are Jarvis",
        messages=[{"role": "user", "content": "Check notes"}],
        provider="codex-cli",
        model="codex-default",
    )

    events = [ev async for ev in engine.stream(req)]
    assert any(e.type == "tool_call" and e.tool_name == "vault_read" for e in events)
    assert events[-1].type == "final"
    assert secret_tok not in events[-1].content
    assert "[REDACTED_HUB_TOKEN]" in events[-1].content


@pytest.mark.asyncio
async def test_antigravity_cli_adapter_streams_and_cancels(tmp_path: Path) -> None:
    mock_bin = _write_mock_cli(
        tmp_path / "mock_agy.py",
        """
import json, os, sys, time
cfg_path = os.environ["JARVIS_MCP_CONFIG"]
assert os.path.exists(cfg_path)
print(json.dumps({"type": "step_update", "text_delta": "Part 1 "}), flush=True)
time.sleep(0.05)
print(json.dumps({"type": "step_update", "text_delta": "Part 2"}), flush=True)
time.sleep(2.0)
print(json.dumps({"type": "step_update", "text_delta": "Should not appear"}), flush=True)
""",
    )

    engine = AntigravityCliEngine(
        binary=str(mock_bin),
        hub_url="http://127.0.0.1:7777/hub/mcp",
        hub_token="tok-123",
        cwd=tmp_path,
        run_mode="full",
    )
    cancel = asyncio.Event()
    req = EngineRequest(
        session_id="s2",
        brain="Brain Default",
        system_prompt="You are Jarvis",
        messages=[{"role": "user", "content": "Hello"}],
        provider="antigravity-cli",
        model="agy-default",
        cancel_event=cancel,
    )

    received: list[str] = []
    async for ev in engine.stream(req):
        if ev.type == "text":
            received.append(ev.content)
            if "Part 2" in ev.content:
                cancel.set()

    combined = "".join(received)
    assert "Part 1" in combined
    assert "Part 2" in combined
    assert "Should not appear" not in combined
