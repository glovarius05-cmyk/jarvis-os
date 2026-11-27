from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient

from app.container import AppContainer
from app.main import create_app


@pytest.mark.asyncio
async def test_voice_live_status_endpoint(tmp_path: Path):
    ctr = AppContainer.build(
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        force_login=False,
    )
    app = create_app(ctr)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        res1 = await client.get("/api/voice/live/status")
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["configured"] is False
        assert data1["provider"] == "gemini-live"

        ctr.settings.update({"gemini_api_key": "AIzaSyTestLiveKey123"})
        res2 = await client.get("/api/voice/live/status")
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["configured"] is True
        assert "gemini-2.5-flash-native-audio" in data2["model"]


def test_voice_live_tools_and_date_instruction(tmp_path: Path):
    from app.api.routes.voice_live import _build_live_system_instruction, _build_live_tools

    ctr = AppContainer.build(
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        force_login=False,
    )
    instr = _build_live_system_instruction(ctr, "Brain Default")
    assert "Текущая дата и время:" in instr
    assert "MSK" in instr

    tools = _build_live_tools()
    fn_decls = next(t["functionDeclarations"] for t in tools if "functionDeclarations" in t)
    names = {f["name"] for f in fn_decls}
    assert "web_search" in names
    assert "web_fetch" in names
    assert "jarvis_browser_navigate" in names
    assert "jarvis_browser_task" in names
    assert "end_voice_conversation" in names


def test_voice_stop_phrase_detection():
    from app.api.routes.voice_live import _is_voice_stop_phrase

    assert _is_voice_stop_phrase("Джарвис, закончим разговор!") is True
    assert _is_voice_stop_phrase("Всё, завершить разговор") is True
    assert _is_voice_stop_phrase("Stop conversation please") is True
    assert _is_voice_stop_phrase("Какая сегодня погода в Москве?") is False
