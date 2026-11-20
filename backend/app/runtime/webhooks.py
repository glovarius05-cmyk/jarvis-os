"""Incoming Webhook triggers authenticated by SHA-256 token hashes (never stores raw tokens)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any


def _hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


class WebhookStore:
    """Manages webhook definitions in STATE_DIR/webhooks.json with hashed bearer tokens."""

    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / "webhooks.json"
        self._lock = threading.RLock()

    def _load(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self, items: list[dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self._path)

    def list_hooks(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {k: v for k, v in item.items() if k != "token_hash"}
                for item in self._load()
            ]

    def create_hook(
        self,
        *,
        name: str,
        brain: str,
        action: str = "kanban",
        prompt_template: str = "Webhook event received:\n{{payload}}",
        owner_chat: str = "",
        mode: str = "auto",
    ) -> dict[str, Any]:
        if action not in {"kanban", "chat"}:
            raise ValueError("Webhook action must be 'kanban' or 'chat'")
        raw_token = f"whk_{secrets.token_urlsafe(24)}"
        hook_id = f"hk_{uuid.uuid4().hex[:10]}"
        record = {
            "id": hook_id,
            "name": name.strip() or hook_id,
            "brain": brain,
            "action": action,
            "prompt_template": prompt_template,
            "owner_chat": owner_chat.strip(),
            "mode": mode if mode in {"suggest", "auto", "full"} else "auto",
            "token_hash": _hash_token(raw_token),
            "enabled": True,
            "created_at": time.time(),
            "last_triggered_at": 0.0,
        }
        with self._lock:
            items = self._load()
            items.append(record)
            self._save(items)
        public = {k: v for k, v in record.items() if k != "token_hash"}
        # Raw token returned ONCE at creation time
        return {**public, "token": raw_token}

    def delete_hook(self, hook_id: str) -> bool:
        with self._lock:
            items = self._load()
            kept = [h for h in items if h.get("id") != hook_id]
            if len(kept) == len(items):
                return False
            self._save(kept)
            return True

    def verify_and_touch(self, hook_id: str, provided_token: str) -> dict[str, Any] | None:
        if not provided_token:
            return None
        cand_hash = _hash_token(provided_token)
        with self._lock:
            items = self._load()
            for item in items:
                if item.get("id") == hook_id and item.get("enabled", True):
                    stored_hash = str(item.get("token_hash") or "")
                    if stored_hash and hmac.compare_digest(cand_hash, stored_hash):
                        item["last_triggered_at"] = time.time()
                        self._save(items)
                        return {k: v for k, v in item.items() if k != "token_hash"}
        return None
