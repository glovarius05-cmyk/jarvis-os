"""Tests for newly added bundled plugins: jarvis-memory, jarvis-weather, jarvis-youtube, jarvis-notify."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.container import AppContainer


@pytest.mark.asyncio
async def test_new_bundled_plugins_registered_and_memory_notify(tmp_path: Path, project_root: Path) -> None:
    ctr = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
    )
    hub = ctr.hub
    tools = await hub.list_tools_info(brain="Brain Default", run_mode="full")
    tool_map = {t["name"]: t for t in tools}

    for name in ("jarvis_memory_save", "jarvis_memory_recall", "jarvis_weather", "jarvis_youtube", "jarvis_notify"):
        assert name in tool_map

    assert tool_map["jarvis_memory_recall"]["required_perm"] == "readonly"
    assert tool_map["jarvis_memory_save"]["required_perm"] == "safe"
    assert tool_map["jarvis_weather"]["required_perm"] == "readonly"
    assert tool_map["jarvis_youtube"]["required_perm"] == "readonly"
    assert tool_map["jarvis_notify"]["required_perm"] == "safe"

    # Memory save -> hybrid reindex -> recall
    save_res = await hub.call_tool(
        name="jarvis_memory_save",
        args={"title": "Project Stack", "content": "Jarvis OS uses FastAPI and React Tailwind."},
        brain="Brain Default",
        run_mode="safe",
    )
    assert "memory/facts/project-stack.md" in save_res

    ctr.hybrid_index.index_brain(vaults=ctr.vaults, brain="Brain Default", force=True)
    recall_res = await hub.call_tool(
        name="jarvis_memory_recall",
        args={"query": "FastAPI"},
        brain="Brain Default",
        run_mode="readonly",
    )
    assert "project-stack.md" in recall_res

    # Notify delivers to Inbox
    notify_res = await hub.call_tool(
        name="jarvis_notify",
        args={"title": "Test Alert", "body": "Deployment finished!"},
        brain="Brain Default",
        run_mode="safe",
    )
    assert "Notification delivered to Inbox" in notify_res
    assert ctr.inbox.unread_count(brain="Brain Default") == 1


@pytest.mark.asyncio
async def test_weather_and_youtube_plugins_mocked(
    tmp_path: Path, project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctr = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
    )

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, url, params=None):
            if "geocoding-api.open-meteo.com" in url:
                return httpx.Response(
                    200,
                    json={"results": [{"name": "Warsaw", "country": "Poland", "latitude": 52.23, "longitude": 21.01}]},
                )
            if "api.open-meteo.com" in url:
                return httpx.Response(
                    200,
                    json={
                        "current": {
                            "temperature_2m": 5.5,
                            "apparent_temperature": 2.1,
                            "relative_humidity_2m": 78,
                            "wind_speed_10m": 12.0,
                        },
                        "daily": {
                            "time": ["2026-11-25"],
                            "temperature_2m_max": [7.0],
                            "temperature_2m_min": [1.0],
                            "precipitation_probability_max": [20],
                        },
                    },
                )
            if "youtube.com/watch" in url:
                return httpx.Response(
                    200,
                    text=(
                        "<html><title>Demo Video - YouTube</title>"
                        '"captionTracks":[{"baseUrl":"https://yt.test/cap","languageCode":"ru"}]</html>'
                    ),
                )
            if "yt.test/cap" in url:
                return httpx.Response(
                    200,
                    text="<transcript><text start='0' dur='2'>Привет мир</text><text start='2' dur='2'>тест субтитров</text></transcript>",
                )
            return httpx.Response(404)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    wx_out = await ctr.hub.call_tool(
        name="jarvis_weather",
        args={"location": "Warsaw", "days": 1},
        brain="Brain Default",
        run_mode="readonly",
    )
    assert "Warsaw, Poland" in wx_out
    assert "5.5°C" in wx_out

    yt_out = await ctr.hub.call_tool(
        name="jarvis_youtube",
        args={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        brain="Brain Default",
        run_mode="readonly",
    )
    assert "Demo Video" in yt_out
    assert "Привет мир тест субтитров" in yt_out
