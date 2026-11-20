"""Unit tests for Second Brain Remote Git Sync."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.brain.vault import VaultManager
from app.container import AppContainer
from app.main import create_app


def test_vault_git_remote_lifecycle(tmp_path: Path):
    brains_dir = tmp_path / "brains"
    vaults = VaultManager(brains_dir)
    brain_name = "GitRemoteBrain"
    vaults.ensure_brain(brain_name, init_git=True)

    # 1. Initial status: is_git=True, configured=False
    status = vaults.get_git_remote_status(brain_name)
    assert status["is_git"] is True
    assert status["configured"] is False
    assert status["remote_url"] == ""

    # 2. Configure remote URL
    repo_url = "git@github.com:example/my-second-brain.git"
    updated = vaults.configure_git_remote(brain_name, repo_url)
    assert updated["configured"] is True
    assert updated["remote_url"] == repo_url

    # 3. Check status again
    status2 = vaults.get_git_remote_status(brain_name)
    assert status2["configured"] is True
    assert status2["remote_url"] == repo_url

    # 4. Remove remote URL
    cleared = vaults.configure_git_remote(brain_name, "")
    assert cleared["configured"] is False
    assert cleared["remote_url"] == ""


def test_sync_git_remote_handles_missing_remote_gracefully(tmp_path: Path):
    brains_dir = tmp_path / "brains"
    vaults = VaultManager(brains_dir)
    brain_name = "NoRemoteBrain"
    vaults.ensure_brain(brain_name, init_git=True)

    res = vaults.sync_git_remote(brain_name, action="all")
    assert res["ok"] is False
    assert "No remote origin configured" in res["error"]


def test_git_remote_api_endpoints(tmp_path: Path):
    state_dir = tmp_path / "state"
    brains_dir = tmp_path / "brains"
    container = AppContainer.build(
        state_dir=state_dir,
        brains_dir=brains_dir,
        force_login=False,
    )
    app = create_app(container, enable_scheduler=False)
    client = TestClient(app)

    # 1. GET /api/brains/git/remote
    res_get = client.get("/api/brains/git/remote")
    assert res_get.status_code == 200
    data = res_get.json()
    assert data["is_git"] is True
    assert data["configured"] is False

    # 2. POST /api/brains/git/remote (set origin)
    test_url = "https://github.com/test-user/brain-notes.git"
    res_post = client.post("/api/brains/git/remote", json={"remote_url": test_url})
    assert res_post.status_code == 200
    post_data = res_post.json()
    assert post_data["ok"] is True
    assert post_data["remote_url"] == test_url

    # 3. POST /api/brains/git/sync (fails gracefully without network / non-existent remote)
    res_sync = client.post("/api/brains/git/sync", json={"action": "pull"})
    assert res_sync.status_code == 200
    sync_data = res_sync.json()
    assert "ok" in sync_data
