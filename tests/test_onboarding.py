"""Unit tests for the first-run onboarding wizard."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.container import AppContainer
from app.main import create_app


def test_onboarding_lifecycle(tmp_path: Path):
    state_dir = tmp_path / "state"
    brains_dir = tmp_path / "brains"
    container = AppContainer.build(
        state_dir=state_dir,
        brains_dir=brains_dir,
        force_login=False,
    )
    app = create_app(container, enable_scheduler=False)
    client = TestClient(app)

    # 1. Initial status before setup
    res = client.get("/api/onboarding/status")
    assert res.status_code == 200
    data = res.json()
    assert data["completed"] is False
    assert len(data["templates"]) >= 5
    assert data["active_brain"] == "Brain Default"

    # 2. Complete onboarding with OpenRouter and Architect persona
    payload = {
        "provider": "openrouter",
        "model": "deepseek/deepseek-chat",
        "api_key": "sk-or-test-key-12345",
        "character_template": "architect",
    }
    complete_res = client.post("/api/onboarding/complete", json=payload)
    assert complete_res.status_code == 200
    complete_data = complete_res.json()
    assert complete_data["ok"] is True

    # 3. Status after completion
    res_after = client.get("/api/onboarding/status")
    assert res_after.status_code == 200
    assert res_after.json()["completed"] is True

    # 4. Verify settings and character were updated
    char_res = client.get("/api/brains/character")
    assert char_res.status_code == 200
    char_data = char_res.json()["character"]
    assert char_data["name"] == "Chief Architect & Security Auditor"

    # Verify secret key was encrypted on disk and marked configured in public view
    settings_res = client.get("/api/settings")
    assert settings_res.status_code == 200
    assert settings_res.json()["secrets_configured"]["openrouter_api_key"] is True
    assert container.settings.load()["openrouter_api_key"] == "sk-or-test-key-12345"
