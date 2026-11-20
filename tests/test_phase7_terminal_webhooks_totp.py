"""Tests for Phase 7: Pure-Python 2FA TOTP, fenced shell + secret redaction, Webhooks, and CLI channel."""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.container import AppContainer
from app.engines.base import EngineEvent, EngineRequest
from app.main import create_app
from app.security.totp import generate_totp_secret, totp_code, verify_totp


class _CliFakeEngine:
    async def stream(self, request: EngineRequest) -> AsyncIterator[EngineEvent]:
        out = f"CLI reply to: {request.messages[-1]['content']}"
        yield EngineEvent(type="text", content=out, provider=request.provider, model=request.model)
        yield EngineEvent(type="final", content=out, provider=request.provider, model=request.model)


def test_pure_python_totp_and_auth_enforcement(tmp_path: Path, project_root: Path) -> None:
    secret = generate_totp_secret()
    code = totp_code(secret, for_time=1_700_000_000)
    assert len(code) == 6 and code.isdigit()
    assert verify_totp(secret, code, for_time=1_700_000_000) is True
    assert verify_totp(secret, "000000", for_time=1_700_000_000) is False

    ctr = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        force_login=True,
    )
    app = create_app(ctr, enable_scheduler=False)

    with TestClient(app) as tc:
        assert tc.post("/api/auth/setup", json={"username": "admin", "password": "strong-password-1"}).status_code == 200
        setup_res = tc.post("/api/auth/totp/setup").json()
        assert setup_res["uri"].startswith("otpauth://totp/")

        valid_now = totp_code(setup_res["secret"])
        assert tc.post("/api/auth/totp/enable", json={"code": valid_now}).status_code == 200

        # Logout and verify password alone is now rejected without 2FA code
        tc.post("/api/auth/logout")
        assert tc.post("/api/auth/login", json={"username": "admin", "password": "strong-password-1"}).status_code == 401
        assert (
            tc.post(
                "/api/auth/login",
                json={"username": "admin", "password": "strong-password-1", "totp_code": totp_code(setup_res["secret"])},
            ).status_code
            == 200
        )


@pytest.mark.asyncio
async def test_fenced_shell_full_mode_only_and_secret_redaction(
    tmp_path: Path, project_root: Path
) -> None:
    ctr = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
    )

    # Blocked in suggest & auto modes!
    for mode in ("suggest", "auto"):
        blocked = await ctr.hub.call_tool(
            name="run_command",
            args={"command": "echo hi"},
            brain="Brain Default",
            run_mode=mode,
        )
        assert "Permission denied" in blocked

    # Allowed in full mode and redacts API keys + .hub_token from stdout
    hub_tok = ctr.hub.hub_token()
    out = await ctr.hub.call_tool(
        name="run_command",
        args={"command": f"echo ok sk-or-1234567890abcdefghij {hub_tok}"},
        brain="Brain Default",
        run_mode="full",
    )
    assert "exit_code=0" in out
    assert "ok" in out
    assert "sk-or-1234567890abcdefghij" not in out
    assert hub_tok not in out
    assert "[REDACTED]" in out


def test_webhooks_hashed_tokens_and_cli_channel(tmp_path: Path, project_root: Path) -> None:
    ctr = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        engine_override=_CliFakeEngine(),
    )
    app = create_app(ctr, enable_scheduler=False)

    with TestClient(app) as tc:
        created = tc.post("/api/webhooks", json={"name": "Deploy Hook", "action": "kanban"}).json()
        hook_id = created["id"]
        raw_token = created["token"]

        # Raw token must NEVER be stored on disk (only SHA-256 hash)
        raw_file = (tmp_path / "state" / "webhooks.json").read_text(encoding="utf-8")
        assert raw_token not in raw_file
        assert "token_hash" in raw_file

        # Missing or wrong token rejected with 401
        assert tc.post(f"/hooks/{hook_id}", json={"event": "push"}).status_code == 401
        assert (
            tc.post(f"/hooks/{hook_id}", headers={"X-Webhook-Token": "wrong"}, json={"event": "push"}).status_code
            == 401
        )

        # Valid token queues a Kanban task
        ok_resp = tc.post(
            f"/hooks/{hook_id}",
            headers={"X-Webhook-Token": raw_token},
            json={"event": "release", "tag": "v1.0"},
        )
        assert ok_resp.status_code == 200
        assert ok_resp.json()["action"] == "kanban"
        assert len(ctr.kanban.list_tasks(brain="Brain Default")) == 1

        # CLI channel endpoint runs through TurnShell
        cli_resp = tc.post("/api/chat/cli", json={"prompt": "Hello from terminal", "peer_id": "term1"})
        assert cli_resp.status_code == 200
        assert "CLI reply to: Hello from terminal" in cli_resp.json()["reply"]
