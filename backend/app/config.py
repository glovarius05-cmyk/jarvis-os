"""Application paths and encrypted settings store.

Enforces strict separation of the three storage zones:
  1. CODE_DIR: repository files (read-only in production containers)
  2. STATE_DIR: runtime databases, encrypted settings, auth sessions, audit logs
  3. BRAINS_DIR: user-owned Markdown vaults (Obsidian-compatible, git-backed)
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.security.secrets import SecretStore

SECRET_FIELDS: set[str] = {
    "openrouter_api_key",
    "gemini_api_key",
    "openai_api_key",
    "anthropic_api_key",
    "groq_api_key",
    "telegram_bot_token",
    "composio_api_key",
    "perplexity_api_key",
    "deepseek_api_key",
    "moonshot_api_key",
    "dashscope_api_key",
}

DEFAULT_BRAIN_NAME = "Brain Default"


def _default_settings() -> dict[str, Any]:
    return {
        "active_brain": DEFAULT_BRAIN_NAME,
        "ui_lang": "ru",
        "timezone": "UTC",
        "model": {
            "main_provider": "openrouter",
            "main_model": "openrouter/auto",
            "aux_provider": "local-llama",
            "aux_model": "llama3.1:8b",
            "fallback_chain": [],
        },
        "local_llama": {
            "base_url": "http://127.0.0.1:11434/v1",
            "model": "llama3.1:8b",
            "num_ctx": 16384,
        },
        "cli_providers": {
            "codex_enabled": True,
            "antigravity_enabled": True,
        },
        "telegram": {
            "enabled": False,
            "allowed_chat_ids": [],
        },
        "voice": {
            "tts_voice": "ru-RU-DmitryNeural",
            "tts_rate": "+5%",
        },
        "approval": {
            "require_for_full_tools": False,
            "timeout_s": 120,
            "auto_approve_tools": [],
        },
        "sandbox": {
            "mode": "local",
            "docker_image": "python:3.12-slim",
            "network": "none",
            "memory_limit": "512m",
            "cpus": "1.0",
        },
        "openrouter_api_key": "",
        "gemini_api_key": "",
        "openai_api_key": "",
        "anthropic_api_key": "",
        "groq_api_key": "",
        "telegram_bot_token": "",
        "composio_api_key": "",
        "composio_connected_apps": [],
        "composio_account_emails": {},
        "perplexity_api_key": "",
        "deepseek_api_key": "",
        "moonshot_api_key": "",
        "dashscope_api_key": "",
        "onboarding_completed": False,
    }


@dataclass(frozen=True)
class AppPaths:
    code_dir: Path
    state_dir: Path
    brains_dir: Path
    prompts_dir: Path
    dashboard_dist_dir: Path

    @classmethod
    def from_env(cls, code_dir: Path | None = None) -> "AppPaths":
        root = (code_dir or Path(__file__).resolve().parents[2]).resolve()
        state = Path(os.getenv("JARVIS_STATE_DIR", str(root / "state"))).expanduser().resolve()
        brains = Path(os.getenv("JARVIS_BRAINS_DIR", str(root / "brains"))).expanduser().resolve()
        return cls(
            code_dir=root,
            state_dir=state,
            brains_dir=brains,
            prompts_dir=root / "prompts",
            dashboard_dist_dir=root / "dashboard" / "dist",
        )

    def ensure_dirs(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.brains_dir.mkdir(parents=True, exist_ok=True)


def is_public_bind(host: str) -> bool:
    normalized = (host or "127.0.0.1").strip().lower()
    return normalized not in {"127.0.0.1", "localhost", "::1"}


def parse_allowed_hosts(host: str, port: int) -> set[str]:
    base = {
        "localhost",
        "127.0.0.1",
        "::1",
        f"localhost:{port}",
        f"127.0.0.1:{port}",
        f"[::1]:{port}",
        "localhost:5173",
        "127.0.0.1:5173",
        "testserver",
    }
    extra = os.getenv("JARVIS_ALLOWED_HOSTS", "")
    for item in extra.split(","):
        cleaned = item.strip().lower()
        if cleaned:
            base.add(cleaned)
    if host and host not in {"0.0.0.0", "::"}:
        base.add(host.lower())
        base.add(f"{host.lower()}:{port}")
    return base


class SettingsStore:
    """Thread-safe JSON settings persistence with transparent Fernet encryption for secrets."""

    def __init__(self, state_dir: Path, secret_store: SecretStore) -> None:
        self._path = state_dir / "settings.json"
        self._secrets = secret_store
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def _read_raw(self) -> dict[str, Any]:
        if not self._path.exists():
            return _default_settings()
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _default_settings()
        merged = _default_settings()
        if isinstance(loaded, dict):
            for key, value in loaded.items():
                if isinstance(value, dict) and isinstance(merged.get(key), dict):
                    merged[key] = {**merged[key], **value}
                else:
                    merged[key] = value
        return merged

    def load(self) -> dict[str, Any]:
        """Return decrypted settings dictionary with fallback to environment variables on initial setup."""
        with self._lock:
            raw = self._read_raw()
            decrypted = self._secrets.decrypt_dict_fields(raw, SECRET_FIELDS)
            if not self._path.exists():
                for field in SECRET_FIELDS:
                    if not decrypted.get(field):
                        env_val = os.getenv(field.upper(), "")
                        if env_val:
                            decrypted[field] = env_val
            return decrypted

    def load_public(self) -> dict[str, Any]:
        """Return settings safe to expose over HTTP: secret values replaced by boolean presence flags."""
        decrypted = self.load()
        public: dict[str, Any] = {}
        configured_secrets: dict[str, bool] = {}
        for key, value in decrypted.items():
            if key in SECRET_FIELDS:
                configured_secrets[key] = bool(value)
            else:
                public[key] = value
        public["secrets_configured"] = configured_secrets
        return public

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Merge a partial update, encrypting secret fields before atomic write."""
        with self._lock:
            current = self.load()
            for key, value in patch.items():
                if key == "secrets_configured":
                    continue
                if isinstance(value, dict) and isinstance(current.get(key), dict):
                    current[key] = {**current[key], **value}
                else:
                    current[key] = value

            encrypted = self._secrets.encrypt_dict_fields(current, SECRET_FIELDS)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(encrypted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self._path)
            return self.load_public()
