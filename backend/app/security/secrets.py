"""Fernet encryption at rest for secret fields stored in STATE_DIR.

Why this exists: API keys and bot tokens live in STATE_DIR/settings.json alongside
non-secret preferences. Encrypting secret values with a per-machine key stored at
STATE_DIR/.secret_key (0600) prevents accidental plaintext leaks in backups or logs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

ENC_PREFIX = "enc:"


class SecretStore:
    """Encrypts and decrypts individual secret strings using a local Fernet key."""

    def __init__(self, state_dir: Path) -> None:
        self._key_path = state_dir / ".secret_key"
        self._fernet: Fernet | None = None

    def _get_fernet(self) -> Fernet:
        if self._fernet is not None:
            return self._fernet

        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        if self._key_path.exists():
            raw = self._key_path.read_bytes().strip()
        else:
            raw = Fernet.generate_key()
            tmp = self._key_path.with_suffix(".tmp")
            tmp.write_bytes(raw)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self._key_path)
            try:
                os.chmod(self._key_path, 0o600)
            except OSError:
                pass

        self._fernet = Fernet(raw)
        return self._fernet

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a non-empty string and prefix with 'enc:'. Already encrypted values pass through."""
        if not plaintext:
            return ""
        if plaintext.startswith(ENC_PREFIX):
            return plaintext
        token = self._get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
        return f"{ENC_PREFIX}{token}"

    def decrypt(self, value: str) -> str:
        """Decrypt an 'enc:'-prefixed string. Plaintext values are returned as-is for migration."""
        if not value:
            return ""
        if not value.startswith(ENC_PREFIX):
            return value
        token = value[len(ENC_PREFIX) :].encode("ascii")
        try:
            return self._get_fernet().decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Failed to decrypt secret: key mismatch or corrupted ciphertext") from exc

    def encrypt_dict_fields(self, data: dict[str, Any], secret_fields: set[str]) -> dict[str, Any]:
        """Return a shallow copy with specified secret string fields encrypted."""
        out = dict(data)
        for field in secret_fields:
            val = out.get(field)
            if isinstance(val, str) and val:
                out[field] = self.encrypt(val)
        return out

    def decrypt_dict_fields(self, data: dict[str, Any], secret_fields: set[str]) -> dict[str, Any]:
        """Return a shallow copy with specified secret string fields decrypted."""
        out = dict(data)
        for field in secret_fields:
            val = out.get(field)
            if isinstance(val, str) and val:
                out[field] = self.decrypt(val)
        return out
