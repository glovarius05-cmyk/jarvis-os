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


@pytest.mark.asyncio
async def test_antigravity_syncs_mcp_config_flags_usage_and_deduplicates_result(tmp_path: Path) -> None:
    import json
    import stat as stat_mod

    user_cfg = tmp_path / ".gemini" / "config" / "mcp_config.json"
    user_cfg.parent.mkdir(parents=True, exist_ok=True)
    user_cfg.write_text(
        json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "echo"}}}),
        encoding="utf-8",
    )

    mock_bin = _write_mock_cli(
        tmp_path / "mock_agy_full.py",
        """
import json, sys
argv = sys.argv[1:]
print(json.dumps({"event": "init", "conversation_id": "c1"}))
print(json.dumps({"event": "step_update", "text_delta": "Args: " + " ".join(argv)}))
print(json.dumps({
    "event": "result",
    "result": {
        "status": "OK",
        "response": "Args: " + " ".join(argv),
        "usage": {"input_tokens": 11, "output_tokens": 22, "total_tokens": 33},
    },
}))
""",
    )

    engine = AntigravityCliEngine(
        binary=str(mock_bin),
        hub_url="http://127.0.0.1:7777/hub/mcp",
        hub_token="hub-secret-999",
        cwd=tmp_path,
        run_mode="full",
        user_mcp_config_path=user_cfg,
    )
    req = EngineRequest(
        session_id="s3",
        brain="Work Brain",
        system_prompt="You are Jarvis",
        messages=[{"role": "user", "content": "Status"}],
        provider="antigravity-cli",
        model="gemini-3.8-flash-high",
    )

    events = [ev async for ev in engine.stream(req)]
    text_events = [ev for ev in events if ev.type == "text"]
    assert len(text_events) == 1
    assert "--dangerously-skip-permissions" in text_events[0].content
    assert "--disable-slash-commands" in text_events[0].content
    assert "--model gemini-3.8-flash-high" in text_events[0].content
    assert 'mcp(jarvis/*)' in text_events[0].content

    assert events[-1].type == "final"
    assert events[-1].content == text_events[0].content  # no double emission from result.response
    assert events[-1].usage == {"input_tokens": 11, "output_tokens": 22, "total_tokens": 33}

    synced = json.loads(user_cfg.read_text(encoding="utf-8"))
    assert synced["theme"] == "dark"
    assert synced["mcpServers"]["other"] == {"command": "echo"}
    assert synced["mcpServers"]["jarvis"] == {
        "serverUrl": "http://127.0.0.1:7777/hub/mcp",
        "headers": {
            "Authorization": "Bearer hub-secret-999",
            "X-Jarvis-Mode": "full",
            "X-Jarvis-Brain": "Work Brain",
        },
    }
    assert stat_mod.S_IMODE(user_cfg.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_antigravity_run_mode_flags_and_result_only_response(tmp_path: Path) -> None:
    import json

    mock_bin = _write_mock_cli(
        tmp_path / "mock_agy_modes.py",
        """
import json, sys
print(json.dumps({"event": "result", "result": {"status": "OK", "response": "Flags: " + " ".join(sys.argv[1:])}}))
""",
    )

    for run_mode, expected_flag in [("suggest", "--mode plan"), ("auto", "--mode accept-edits")]:
        engine = AntigravityCliEngine(
            binary=str(mock_bin),
            hub_url="http://127.0.0.1:7777/hub/mcp",
            hub_token="tok",
            cwd=tmp_path,
            run_mode=run_mode,
        )
        req = EngineRequest(
            session_id="s4",
            brain="Brain Default",
            system_prompt="You are Jarvis",
            messages=[{"role": "user", "content": "Check"}],
            provider="antigravity-cli",
            model="agy-default",
        )
        events = [ev async for ev in engine.stream(req)]
        assert [e.type for e in events] == ["text", "final"]
        assert expected_flag in events[-1].content
        assert "--dangerously-skip-permissions" not in events[-1].content
        assert "--model" not in events[-1].content


@pytest.mark.asyncio
async def test_antigravity_fails_fast_on_unauthenticated_stderr_prompt(tmp_path: Path) -> None:
    import time

    mock_bin = _write_mock_cli(
        tmp_path / "mock_agy_unauth.py",
        """
import sys, time
sys.stderr.write("Authentication required. Please visit the URL to log in:\\nhttps://accounts.google.com/o/oauth2/auth\\nWaiting for authentication (timeout 60s)...\\n")
sys.stderr.flush()
time.sleep(10.0)
""",
    )
    engine = AntigravityCliEngine(
        binary=str(mock_bin),
        hub_url="http://127.0.0.1:7777/hub/mcp",
        hub_token="tok",
        cwd=tmp_path,
        run_mode="full",
    )
    req = EngineRequest(
        session_id="s5",
        brain="Brain Default",
        system_prompt="You are Jarvis",
        messages=[{"role": "user", "content": "Hello"}],
        provider="antigravity-cli",
        model="agy-default",
    )

    t0 = time.monotonic()
    events = [ev async for ev in engine.stream(req)]
    elapsed = time.monotonic() - t0
    assert elapsed < 3.0
    assert len(events) == 1
    assert events[0].type == "error"
    assert "not signed in" in events[0].content
    assert "Run 'agy' interactively" in events[0].content


@pytest.mark.asyncio
async def test_antigravity_skips_corrupted_user_mcp_config(tmp_path: Path) -> None:
    user_cfg = tmp_path / ".gemini" / "config" / "mcp_config.json"
    user_cfg.parent.mkdir(parents=True, exist_ok=True)
    user_cfg.write_text("{broken json", encoding="utf-8")

    mock_bin = _write_mock_cli(
        tmp_path / "mock_agy_ok.py",
        """
import json
print(json.dumps({"event": "step_update", "text_delta": "Still works"}))
""",
    )
    engine = AntigravityCliEngine(
        binary=str(mock_bin),
        hub_url="http://127.0.0.1:7777/hub/mcp",
        hub_token="tok",
        cwd=tmp_path,
        run_mode="full",
        user_mcp_config_path=user_cfg,
    )
    req = EngineRequest(
        session_id="s6",
        brain="Brain Default",
        system_prompt="You are Jarvis",
        messages=[{"role": "user", "content": "Hello"}],
        provider="antigravity-cli",
        model="agy-default",
    )
    events = [ev async for ev in engine.stream(req)]
    assert events[-1].type == "final"
    assert events[-1].content == "Still works"
    assert user_cfg.read_text(encoding="utf-8") == "{broken json"
