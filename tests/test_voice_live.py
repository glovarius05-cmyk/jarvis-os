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
    assert "vault_write" in names
    assert "vault_edit" in names
    assert "vault_read" in names
    assert "vault_search" in names
    assert "vault_list" in names
    assert "composio_status" in names
    assert "composio_gmail" in names
    assert "composio_calendar" in names
    assert "composio_github" in names
    assert "composio_feed" in names
    assert "composio_search" in names
    assert "composio_action" in names
    assert "jarvis_weather" in names
    assert "jarvis_task" in names
    assert "end_voice_conversation" in names


def test_voice_stop_phrase_detection():
    from app.api.routes.voice_live import _is_voice_stop_phrase

    assert _is_voice_stop_phrase("Джарвис, закончим разговор!") is True
    assert _is_voice_stop_phrase("Всё, завершить разговор") is True
    assert _is_voice_stop_phrase("Stop conversation please") is True
    assert _is_voice_stop_phrase("Какая сегодня погода в Москве?") is False


def test_voice_mapping_and_gender_persona(tmp_path: Path):
    from app.api.routes.voice_live import _build_live_system_instruction, _resolve_live_voice
    from app.voice.tts import resolve_edge_voice_and_pitch

    # Gemini Live voice mapping (female Edge voice -> Aoede, male -> Charon, direct Gemini -> preserved)
    assert _resolve_live_voice("ru-RU-SvetlanaNeural") == "Aoede"
    assert _resolve_live_voice("ru-RU-DmitryNeural") == "Charon"
    assert _resolve_live_voice("Kore") == "Kore"
    assert _resolve_live_voice("Fenrir") == "Fenrir"
    assert _resolve_live_voice("Zephyr") == "Zephyr"

    # Edge TTS fallback preserves male/female + pitch distinction for all 12 voices
    edge_v, edge_p = resolve_edge_voice_and_pitch("Kore")
    assert edge_v == "ru-RU-SvetlanaNeural"
    assert edge_p == "-8Hz"

    edge_v2, edge_p2 = resolve_edge_voice_and_pitch("Umbriel")
    assert edge_v2 == "ru-RU-DmitryNeural"
    assert edge_p2 == "-14Hz"

    ctr = AppContainer.build(
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        force_login=False,
    )
    ctr.settings.update({"composio_connected_apps": ["gmail", "googlecalendar", "notion"]})
    fem_instr = _build_live_system_instruction(ctr, "Brain Default", voice_name="Aoede", with_tools=True)
    assert "женском роде" in fem_instr
    assert "vault_write" in fem_instr
    assert "gmail" in fem_instr and "googlecalendar" in fem_instr and "notion" in fem_instr
    no_tools_instr = _build_live_system_instruction(ctr, "Brain Default", voice_name="Aoede", with_tools=False)
    assert "отключены" in no_tools_instr
    male_instr = _build_live_system_instruction(ctr, "Brain Default", voice_name="Charon")
    assert "мужском роде" in male_instr
