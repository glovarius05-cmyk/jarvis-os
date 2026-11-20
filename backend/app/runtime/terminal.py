"""Fenced shell execution with secret redaction for the owner Terminal and Full-mode agents."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-(?:or-|ant-|proj-)?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\benc:[A-Za-z0-9_=+-]{24,}\b"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}", re.IGNORECASE),
)


def redact_secrets(text: str, extra_secrets: Iterable[str] = ()) -> str:
    """Mask known API key patterns and explicit runtime tokens from terminal output."""
    out = text or ""
    for secret in extra_secrets:
        if secret and len(secret) >= 8 and secret in out:
            out = out.replace(secret, "[REDACTED]")
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


@dataclass(frozen=True)
class CommandResult:
    command: str
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
        }


async def run_fenced_command(
    command: str,
    *,
    cwd: Path,
    timeout_s: float = 30.0,
    max_chars: int = 12_000,
    extra_secrets: Iterable[str] = (),
) -> CommandResult:
    """Execute one shell command inside `cwd` with timeout, size cap, and secret redaction."""
    clean_cmd = (command or "").strip()
    if not clean_cmd:
        raise ValueError("Command cannot be empty")
    target_cwd = cwd.resolve()
    if not target_cwd.is_dir():
        raise ValueError(f"Working directory does not exist: {target_cwd}")

    proc = await asyncio.create_subprocess_shell(
        clean_cmd,
        cwd=str(target_cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    timed_out = False
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=max(1.0, min(timeout_s, 180.0)))
    except asyncio.TimeoutError:
        timed_out = True
        try:
            proc.kill()
            stdout_b, stderr_b = await proc.communicate()
        except Exception:  # noqa: BLE001
            stdout_b, stderr_b = b"", b"Timed out"

    stdout_txt = redact_secrets(stdout_b.decode("utf-8", errors="replace"), extra_secrets)[:max_chars]
    stderr_txt = redact_secrets(stderr_b.decode("utf-8", errors="replace"), extra_secrets)[:max_chars]

    return CommandResult(
        command=clean_cmd,
        cwd=str(target_cwd),
        exit_code=int(proc.returncode if proc.returncode is not None else -1),
        stdout=stdout_txt,
        stderr=stderr_txt,
        timed_out=timed_out,
    )
