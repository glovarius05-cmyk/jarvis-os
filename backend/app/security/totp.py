"""Pure-Python RFC 6238 TOTP (Time-based One-Time Password) implementation — zero extra dependencies."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


def generate_totp_secret(num_bytes: int = 20) -> str:
    """Return an uppercase Base32 secret compatible with Google Authenticator / 1Password."""
    return base64.b32encode(secrets.token_bytes(num_bytes)).decode("ascii").rstrip("=")


def _normalize_b32(secret: str) -> bytes:
    cleaned = (secret or "").strip().replace(" ", "").upper()
    pad = (-len(cleaned)) % 8
    return base64.b32decode(cleaned + ("=" * pad), casefold=True)


def totp_code(secret: str, *, for_time: float | None = None, step: int = 30, digits: int = 6) -> str:
    """Compute the 6-digit RFC 6238 HMAC-SHA1 TOTP code for `for_time`."""
    key = _normalize_b32(secret)
    counter = int((time.time() if for_time is None else for_time) // step)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


def verify_totp(
    secret: str,
    code: str,
    *,
    for_time: float | None = None,
    step: int = 30,
    window: int = 1,
) -> bool:
    """Verify a user-entered TOTP code within +/- `window` time steps."""
    clean = (code or "").strip().replace(" ", "")
    if not clean.isdigit():
        return False
    now = time.time() if for_time is None else for_time
    for drift in range(-window, window + 1):
        expected = totp_code(secret, for_time=now + drift * step, step=step, digits=len(clean))
        if hmac.compare_digest(clean, expected):
            return True
    return False


def provisioning_uri(secret: str, username: str, issuer: str = "Jarvis OS") -> str:
    label = quote(f"{issuer}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"
