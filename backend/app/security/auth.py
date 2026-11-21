"""Admin account and cookie-based session authentication.

Uses Python stdlib hashlib.scrypt (no extra native deps) and stores hashed admin
credentials + active session tokens in STATE_DIR.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.security.totp import generate_totp_secret, provisioning_uri, verify_totp

SESSION_COOKIE_NAME = "jarvis_session"
SESSION_TTL_SECONDS = 30 * 24 * 3600
MIN_PASSWORD_LENGTH = 8
MAX_LOGIN_ATTEMPTS = 8
RATE_WINDOW_SECONDS = 300


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt_bytes = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt_bytes,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return f"scrypt${base64.b64encode(salt_bytes).decode('ascii')}${base64.b64encode(derived).decode('ascii')}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = encoded.split("$", 2)
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(digest_b64.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=32,
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


@dataclass(frozen=True)
class AuthStatus:
    login_required: bool
    has_admin: bool
    authenticated: bool
    username: str | None
    totp_enabled: bool = False


class AuthManager:
    """Manages local admin credentials and 30-day session tokens."""

    def __init__(self, state_dir: Path, force_login: bool = False) -> None:
        self._auth_path = state_dir / ".auth.json"
        self._sessions_path = state_dir / ".sessions.json"
        self._force_login = force_login
        self._lock = threading.RLock()
        self._failed_attempts: dict[str, list[float]] = {}
        self._bootstrap_from_env()

    def _bootstrap_from_env(self) -> None:
        env_user = (os.getenv("JARVIS_ADMIN_USER") or "").strip()
        env_pass = os.getenv("JARVIS_ADMIN_PASSWORD") or ""
        if env_user and len(env_pass) >= MIN_PASSWORD_LENGTH and not self.has_admin():
            self.setup_admin(env_user, env_pass)

    def _load_json(self, path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)

    def has_admin(self) -> bool:
        with self._lock:
            data = self._load_json(self._auth_path)
            return bool(data.get("username") and data.get("password_hash"))

    def login_required(self) -> bool:
        """Auth is enforced whenever an admin exists OR public bind forces login."""
        return self._force_login or self.has_admin()

    def setup_admin(self, username: str, password: str) -> str:
        clean_user = username.strip()
        if not clean_user or len(clean_user) > 64:
            raise ValueError("Username must be 1..64 characters")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")

        with self._lock:
            if self.has_admin():
                raise ValueError("Admin account already exists")
            self._save_json(
                self._auth_path,
                {
                    "username": clean_user,
                    "password_hash": _hash_password(password),
                    "created_at": time.time(),
                },
            )
            return self._create_session_locked(clean_user)

    def _check_rate_limit(self, client_key: str) -> None:
        now = time.time()
        window = [ts for ts in self._failed_attempts.get(client_key, []) if now - ts < RATE_WINDOW_SECONDS]
        self._failed_attempts[client_key] = window
        if len(window) >= MAX_LOGIN_ATTEMPTS:
            raise PermissionError("Too many failed login attempts. Try again later.")

    def _record_failure(self, client_key: str) -> None:
        self._failed_attempts.setdefault(client_key, []).append(time.time())

    def has_totp(self) -> bool:
        with self._lock:
            data = self._load_json(self._auth_path)
            return bool(data.get("totp_enabled") and data.get("totp_secret"))

    def begin_totp_setup(self) -> dict[str, str]:
        with self._lock:
            data = self._load_json(self._auth_path)
            username = str(data.get("username") or "admin")
            secret = generate_totp_secret()
            data["totp_pending_secret"] = secret
            self._save_json(self._auth_path, data)
            return {"secret": secret, "uri": provisioning_uri(secret, username)}

    def enable_totp(self, code: str, *, secret_override: str = "") -> bool:
        with self._lock:
            data = self._load_json(self._auth_path)
            secret = (secret_override or str(data.get("totp_pending_secret") or "")).strip()
            if not secret:
                raise ValueError("Run TOTP setup first")
            if not verify_totp(secret, code):
                raise PermissionError("Invalid 2FA verification code")
            data["totp_secret"] = secret
            data["totp_enabled"] = True
            data.pop("totp_pending_secret", None)
            self._save_json(self._auth_path, data)
            return True

    def disable_totp(self) -> None:
        with self._lock:
            data = self._load_json(self._auth_path)
            data["totp_enabled"] = False
            data.pop("totp_secret", None)
            data.pop("totp_pending_secret", None)
            self._save_json(self._auth_path, data)

    def login(self, username: str, password: str, client_key: str = "default", totp_code_val: str = "") -> str:
        with self._lock:
            self._check_rate_limit(client_key)
            data = self._load_json(self._auth_path)
            stored_user = data.get("username", "")
            stored_hash = data.get("password_hash", "")
            if not stored_user or not stored_hash:
                raise ValueError("Admin account is not configured yet")
            if not (hmac.compare_digest(username.strip(), stored_user) and _verify_password(password, stored_hash)):
                self._record_failure(client_key)
                raise PermissionError("Invalid username or password")
            if data.get("totp_enabled") and data.get("totp_secret"):
                if not verify_totp(str(data["totp_secret"]), totp_code_val):
                    self._record_failure(client_key)
                    raise PermissionError("Invalid or missing 2FA TOTP code")
            self._failed_attempts.pop(client_key, None)
            return self._create_session_locked(stored_user)

    def _create_session_locked(self, username: str) -> str:
        sessions = self._prune_sessions_locked(self._load_json(self._sessions_path))
        token = secrets.token_urlsafe(32)
        sessions[token] = {
            "username": username,
            "created_at": time.time(),
            "expires_at": time.time() + SESSION_TTL_SECONDS,
        }
        self._save_json(self._sessions_path, sessions)
        return token

    def _prune_sessions_locked(self, sessions: dict) -> dict:
        now = time.time()
        return {
            tok: meta
            for tok, meta in sessions.items()
            if isinstance(meta, dict) and float(meta.get("expires_at", 0)) > now
        }

    def verify_admin_password(self, password: str) -> bool:
        """Verify an admin password directly (for HTTP Bearer token authentication)."""
        if not password:
            return False
        with self._lock:
            data = self._load_json(self._auth_path)
            stored_hash = data.get("password_hash", "")
            if not stored_hash:
                return False
            return _verify_password(password, stored_hash)

    def validate_session(self, token: str | None) -> str | None:
        if not token:
            return None
        with self._lock:
            sessions = self._load_json(self._sessions_path)
            meta = sessions.get(token)
            if not isinstance(meta, dict):
                return None
            if float(meta.get("expires_at", 0)) <= time.time():
                sessions.pop(token, None)
                self._save_json(self._sessions_path, sessions)
                return None
            return str(meta.get("username") or "")

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            sessions = self._load_json(self._sessions_path)
            if token in sessions:
                sessions.pop(token, None)
                self._save_json(self._sessions_path, sessions)

    def status(self, token: str | None) -> AuthStatus:
        user = self.validate_session(token)
        return AuthStatus(
            login_required=self.login_required(),
            has_admin=self.has_admin(),
            authenticated=bool(user) or not self.login_required(),
            username=user,
            totp_enabled=self.has_totp(),
        )
