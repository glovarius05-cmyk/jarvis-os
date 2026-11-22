"""Tests for FastAPI endpoints, CSRF/Host protection, and forced auth on public bind."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.container import AppContainer
from app.main import create_app


def test_health_status_brains_and_sessions_api(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.headers["X-Robots-Tag"] == "noindex, nofollow"

    # Settings patch encrypts secrets and never echoes them back
    r = client.patch("/api/settings", json={"updates": {"openrouter_api_key": "sk-or-secret"}})
    assert r.status_code == 200
    body = r.json()
    assert "openrouter_api_key" not in body
    assert body["secrets_configured"]["openrouter_api_key"] is True

    # Create a second brain and verify active switch
    r = client.post("/api/brains", json={"name": "Work Brain", "activate": True})
    assert r.status_code == 200
    assert r.json()["is_git_repo"] is True

    r = client.get("/api/brains")
    assert r.status_code == 200
    assert r.json()["active_brain"] == "Work Brain"
    assert len(r.json()["brains"]) == 2

    # Create and fetch a chat session
    r = client.post("/api/sessions", json={"title": "First chat"})
    assert r.status_code == 200
    sid = r.json()["id"]

    r = client.get(f"/api/sessions/{sid}")
    assert r.status_code == 200
    assert r.json()["session"]["title"] == "First chat"


def test_csrf_and_host_rebinding_protection(client: TestClient) -> None:
    # Malicious Host header rejected
    r = client.get("/api/health", headers={"Host": "evil.attacker.com"})
    assert r.status_code == 403

    # Cross-origin POST rejected
    r = client.post(
        "/api/sessions",
        json={"title": "CSRF attempt"},
        headers={"Origin": "https://evil.attacker.com"},
    )
    assert r.status_code == 403


def test_public_bind_forces_authentication_flow(tmp_path: Path, project_root: Path) -> None:
    public_container = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        host="0.0.0.0",
        port=7777,
    )
    app = create_app(public_container)

    with TestClient(app) as tc:
        # Protected route blocked before admin setup
        assert tc.get("/api/settings").status_code == 401

        st = tc.get("/api/auth/status").json()
        assert st["login_required"] is True
        assert st["has_admin"] is False
        assert st["authenticated"] is False

        # Setup admin creates account and sets session cookie
        r = tc.post("/api/auth/setup", json={"username": "owner", "password": "strong-password-123"})
        assert r.status_code == 200
        assert tc.get("/api/settings").status_code == 200

        # Logout revokes session
        assert tc.post("/api/auth/logout").status_code == 200
        assert tc.get("/api/settings").status_code == 401

        # Wrong password rejected, correct password restores access
        assert tc.post("/api/auth/login", json={"username": "owner", "password": "wrong-password"}).status_code == 401
        assert tc.post("/api/auth/login", json={"username": "owner", "password": "strong-password-123"}).status_code == 200
        assert tc.get("/api/settings").status_code == 200


def test_chat_file_upload_and_raw_endpoint(client: TestClient) -> None:
    import base64
    test_content = b"print('Hello from uploaded file!')"
    b64 = base64.b64encode(test_content).decode("ascii")
    r = client.post(
        "/api/chat/upload-base64",
        json={"filename": "test_script.py", "content_base64": b64, "content_type": "text/x-python"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["filename"] == "test_script.py"
    assert data["is_text"] is True
    assert "print('Hello from uploaded file!')" in data["text_content"]
    assert data["path"].startswith("uploads/")

    # Fetch raw file
    r_raw = client.get(f"/api/brain/raw?path={data['path']}")
    assert r_raw.status_code == 200
    assert r_raw.content == test_content

    # Multipart file upload
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    r2 = client.post(
        "/api/chat/upload",
        files={"file": ("screenshot.png", png_bytes, "image/png")},
    )
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["ok"] is True
    assert data2["filename"] == "screenshot.png"
    assert data2["is_image"] is True
    assert data2["size"] == len(png_bytes)

    r_raw2 = client.get(f"/api/brain/raw?path={data2['path']}")
    assert r_raw2.status_code == 200
    assert r_raw2.content == png_bytes

    # Agensi status endpoint
    r_agensi = client.get("/api/hub/agensi/status")
    assert r_agensi.status_code == 200
    assert r_agensi.json()["connected"] is False

    # Agensi reset endpoint
    r_reset = client.post("/api/hub/agensi/reset")
    assert r_reset.status_code == 200
    assert r_reset.json()["ok"] is True
