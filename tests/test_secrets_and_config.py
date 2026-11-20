"""Tests for Fernet secret storage and encrypted settings persistence."""

from __future__ import annotations

import json
import stat
from pathlib import Path

from app.config import SettingsStore
from app.security.secrets import ENC_PREFIX, SecretStore


def test_secret_store_roundtrip_and_key_permissions(tmp_path: Path) -> None:
    store = SecretStore(tmp_path)
    encrypted = store.encrypt("sk-or-test-secret-123")
    assert encrypted.startswith(ENC_PREFIX)
    assert "sk-or-test-secret-123" not in encrypted
    assert store.decrypt(encrypted) == "sk-or-test-secret-123"
    # Encrypting already encrypted value is idempotent
    assert store.encrypt(encrypted) == encrypted

    key_mode = stat.S_IMODE((tmp_path / ".secret_key").stat().st_mode)
    assert key_mode == 0o600


def test_settings_encrypt_on_disk_and_mask_in_public_view(tmp_path: Path) -> None:
    secrets = SecretStore(tmp_path)
    settings = SettingsStore(tmp_path, secrets)

    public = settings.update(
        {
            "openrouter_api_key": "sk-or-live-999",
            "telegram_bot_token": "123456:ABCDEF",
            "model": {"main_model": "deepseek/deepseek-r1:free"},
        }
    )

    assert "openrouter_api_key" not in public
    assert "telegram_bot_token" not in public
    assert public["secrets_configured"]["openrouter_api_key"] is True
    assert public["secrets_configured"]["telegram_bot_token"] is True
    assert public["secrets_configured"]["gemini_api_key"] is False
    assert public["model"]["main_model"] == "deepseek/deepseek-r1:free"
    assert public["model"]["aux_provider"] == "local-llama"

    raw_on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert raw_on_disk["openrouter_api_key"].startswith(ENC_PREFIX)
    assert "sk-or-live-999" not in json.dumps(raw_on_disk)

    decrypted = settings.load()
    assert decrypted["openrouter_api_key"] == "sk-or-live-999"
    assert decrypted["telegram_bot_token"] == "123456:ABCDEF"
