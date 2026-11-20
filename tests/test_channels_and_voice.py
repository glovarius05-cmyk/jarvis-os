"""Tests for Phase 6: TurnShell, Telegram Owner Bot (whitelist + Inbox delivery), Voice V2, and TTS sanitizer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.channels.base import degrade_ask_blocks_for_plain_text
from app.channels.telegram import TelegramBotService, chunk_telegram_text
from app.container import AppContainer
from app.engines.base import EngineEvent, EngineRequest
from app.main import create_app
from app.voice.tts import strip_markdown_for_tts


class _ChannelVoiceFakeEngine:
    async def stream(self, request: EngineRequest) -> AsyncIterator[EngineEvent]:
        user_msg = request.messages[-1]["content"]
        if "Fast Voice Brain" in request.system_prompt:
            if "TV noise" in user_msg:
                out = "JARVIS_SKIP:"
            elif "open tasks" in user_msg:
                out = "Opening your tasks board.\nJARVIS_UI: open_page tasks"
            elif "check revenue" in user_msg:
                out = "Checking that now.\nJARVIS_ASK_MAIN: Check today's revenue and summarize."
            else:
                out = "Hello! How can I help you?"
        else:
            out = (
                "Which period do you want?\n"
                '<!-- JARVIS_ASK: {"question":"Period?","header":"Period","options":[{"label":"Today","desc":"Last 24h"},{"label":"This week","desc":"7 days"}]} -->'
            )
        yield EngineEvent(type="text", content=out, provider=request.provider, model=request.model)
        yield EngineEvent(type="final", content=out, provider=request.provider, model=request.model)


def test_markdown_tts_stripper_and_ask_degradation() -> None:
    md = "## **Summary**\n- Check [docs](https://example.com) and `main.py`\n```py\nprint(1)\n```\nJARVIS_UI: open_page tasks"
    spoken = strip_markdown_for_tts(md)
    assert "##" not in spoken
    assert "**" not in spoken
    assert "JARVIS_UI" not in spoken
    assert "docs" in spoken

    plain = degrade_ask_blocks_for_plain_text(
        'Pick one:\n<!-- JARVIS_ASK: {"question":"Q","options":[{"label":"A","desc":"First"},{"label":"B"}]} -->'
    )
    assert "1. A - First" in plain
    assert "2. B" in plain
    assert "JARVIS_ASK" not in plain

    chunks = chunk_telegram_text("A" * 4500, limit=2000)
    assert len(chunks) == 3
    assert all(len(c) <= 2000 for c in chunks)


@pytest.mark.asyncio
async def test_telegram_owner_whitelist_reply_and_inbox_delivery(
    tmp_path: Path, project_root: Path
) -> None:
    sent_calls: list[dict] = []

    def mock_tg_api(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        if request.url.path.endswith("/sendMessage"):
            sent_calls.append(body)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent_calls)}})
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"username": "jarvis_test_bot"}})
        return httpx.Response(404)

    ctr = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        engine_override=_ChannelVoiceFakeEngine(),
    )
    ctr.settings.update(
        {
            "telegram_bot_token": "12345:FAKE_TOKEN",
            "telegram": {"enabled": True, "allowed_chat_ids": ["777001"]},
        }
    )

    bot = TelegramBotService(
        settings=ctr.settings,
        turn_shell=ctr.turn_shell,
        http_transport=httpx.MockTransport(mock_tg_api),
    )

    # Stranger blocked
    ok_stranger = await bot.handle_update({"update_id": 1, "message": {"chat": {"id": 999}, "text": "hi"}})
    assert ok_stranger is False
    assert "Access denied" in sent_calls[-1]["text"]

    # Owner whitelisted -> reply degrades JARVIS_ASK into plain-text numbered list
    ok_owner = await bot.handle_update({"update_id": 2, "message": {"chat": {"id": 777001}, "text": "Revenue?"}})
    assert ok_owner is True
    assert "1. Today - Last 24h" in sent_calls[-1]["text"]

    # /status slash command works
    await bot.handle_update({"update_id": 3, "message": {"chat": {"id": 777001}, "text": "/status"}})
    assert "Brain Default" in sent_calls[-1]["text"]

    # Inbox background report automatically pushed to tg:777001
    await bot.notify_from_inbox("tg:777001", "Kanban Done", "Weekly report is ready.")
    assert "Weekly report is ready." in sent_calls[-1]["text"]
    assert sent_calls[-1]["chat_id"] == "777001"


@pytest.mark.asyncio
async def test_voice_v2_skip_ui_and_main_brain_delegation(
    tmp_path: Path, project_root: Path
) -> None:
    ctr = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        engine_override=_ChannelVoiceFakeEngine(),
    )

    # 1. Background TV noise -> skipped silently
    r_skip = await ctr.voice_brain.process_utterance(brain="Brain Default", transcript="random TV noise")
    assert r_skip.skipped is True
    assert r_skip.spoken_text == ""

    # 2. UI shortcut -> returns open_page tasks without waking main brain
    r_ui = await ctr.voice_brain.process_utterance(brain="Brain Default", transcript="please open tasks")
    assert r_ui.skipped is False
    assert r_ui.ui_action == "open_page"
    assert r_ui.ui_target == "tasks"
    assert r_ui.delegated_session_id == ""

    # 3. Real data request -> speaks confirmation AND spawns main brain turn via ChatRuntime!
    r_main = await ctr.voice_brain.process_utterance(brain="Brain Default", transcript="check revenue today")
    assert r_main.spoken_text == "Checking that now."
    assert r_main.delegated_session_id != ""
    await ctr.chat_runtime.wait_turn(r_main.delegated_session_id, timeout=5.0)
    msgs = ctr.sessions.list_messages(r_main.delegated_session_id)
    assert len(msgs) == 2
    assert "Check today's revenue" in msgs[0].content

    # HTTP route check
    app = create_app(ctr, enable_scheduler=False)
    with TestClient(app) as tc:
        assert tc.get("/api/channels/telegram/status").status_code == 200
        resp = tc.post("/api/voice/turn", json={"transcript": "please open tasks"})
        assert resp.status_code == 200
        assert resp.json()["ui_target"] == "tasks"

        # TTS endpoint
        tts_resp = tc.post("/api/voice/tts", json={"text": "Привет! Как дела?"})
        assert tts_resp.status_code == 200
        # Either audio/mpeg if edge-tts works offline, or fallback JSON
        assert tts_resp.headers["content-type"] in ("audio/mpeg", "application/json")


@pytest.mark.asyncio
async def test_telegram_voice_inbound_and_outbound(
    tmp_path: Path, project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent_messages: list[dict] = []
    sent_voices: list[dict] = []

    def mock_tg_api(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/sendMessage"):
            body = json.loads(request.content.decode())
            sent_messages.append(body)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        if path.endswith("/sendVoice"):
            sent_voices.append({"url": str(request.url)})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 2}})
        if path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/file_1.oga"}})
        if "voice/file_1.oga" in path:
            return httpx.Response(200, content=b"fake-ogg-audio-content")
        return httpx.Response(404)

    ctr = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        engine_override=_ChannelVoiceFakeEngine(),
    )
    ctr.settings.update(
        {
            "telegram_bot_token": "12345:VOICE_TOKEN",
            "telegram": {"enabled": True, "allowed_chat_ids": ["777001"]},
        }
    )

    # Mock transcribe_audio_bytes to avoid heavy neural transcription during unit tests
    async def fake_transcribe(data: bytes, suffix: str = ".ogg", language: str = "ru") -> str:
        return "Привет, Джарвис"

    monkeypatch.setattr("app.voice.stt.transcribe_audio_bytes", fake_transcribe)

    # Mock synthesize_edge_tts to return fake mp3 bytes
    async def fake_synthesize(text: str, voice: str = "", rate: str = "") -> tuple[bytes | None, str]:
        return b"fake-mp3-bytes", ""

    monkeypatch.setattr("app.voice.tts.synthesize_edge_tts", fake_synthesize)

    bot = TelegramBotService(
        settings=ctr.settings,
        turn_shell=ctr.turn_shell,
        http_transport=httpx.MockTransport(mock_tg_api),
    )

    # Inbound voice note from owner
    voice_update = {
        "update_id": 10,
        "message": {
            "chat": {"id": 777001},
            "voice": {"file_id": "voice_123", "duration": 3},
        },
    }
    handled = await bot.handle_update(voice_update)
    assert handled is True
    assert len(sent_messages) == 1
    assert len(sent_voices) == 1
