"""Text-to-Speech helper with Markdown stripping and Edge TTS support (graceful fallback)."""

from __future__ import annotations

import io
import re

_CODE_FENCE_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_MARKER_LINE_RE = re.compile(r"^\s*JARVIS_(?:ASK_MAIN|UI|SKIP):.*$", re.MULTILINE)
_ASK_COMMENT_RE = re.compile(r"<!--\s*JARVIS_ASK:[\s\S]*?-->")


def strip_markdown_for_tts(text: str) -> str:
    """Convert Markdown assistant output into clean, natural spoken prose."""
    s = _MARKER_LINE_RE.sub("", text or "")
    s = _ASK_COMMENT_RE.sub("", s)
    s = _CODE_FENCE_RE.sub(" (code block omitted) ", s)
    s = _LINK_RE.sub(r"\1", s)
    s = _INLINE_CODE_RE.sub(r"\1", s)
    s = _HEADING_RE.sub("", s)
    s = _BULLET_RE.sub("", s)
    s = s.replace("**", "").replace("__", "").replace("*", "").replace("—", ", ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


async def synthesize_edge_tts(
    text: str,
    *,
    voice: str = "ru-RU-DmitryNeural",
    rate: str = "+0%",
) -> tuple[bytes | None, str]:
    """Synthesize MP3 audio via edge-tts with automatic retry; returns (mp3_bytes, error_or_empty)."""
    clean = strip_markdown_for_tts(text)
    if not clean:
        return None, "Empty text"
    try:
        import asyncio
        import edge_tts  # type: ignore[import-not-found]
    except ImportError:
        return None, "edge_tts_not_installed"

    last_err = ""
    for attempt in range(3):
        try:
            kwargs: dict[str, str] = {"voice": voice}
            if rate and rate != "+0%" and attempt < 2:
                kwargs["rate"] = rate
            comm = edge_tts.Communicate(clean[:3000], **kwargs)
            buf = io.BytesIO()
            async for chunk in comm.stream():
                if chunk.get("type") == "audio":
                    buf.write(chunk["data"])
            audio = buf.getvalue()
            if audio:
                return audio, ""
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            await asyncio.sleep(0.5 * (attempt + 1))

    return None, last_err or "No audio generated"
