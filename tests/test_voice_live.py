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
