"""Speech-to-Text (STT) engine using faster-whisper with ffmpeg numpy decoding.

Provides:
- transcribe_audio_file: Transcribe local audio file (.ogg, .mp3, .wav, .webm, .m4a).
- transcribe_audio_bytes: Transcribe in-memory audio bytes from Telegram voice notes or browser mic.
Converts input audio to 16kHz mono float32 array via ffmpeg + wave for 100% platform portability.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any

import numpy as np

_WHISPER_MODEL: Any = None
FFMPEG_PATH = "/opt/homebrew/bin/ffmpeg" if Path("/opt/homebrew/bin/ffmpeg").exists() else "ffmpeg"


def _is_ffmpeg_available() -> bool:
    return bool(shutil.which(FFMPEG_PATH) or shutil.which("ffmpeg"))


def _get_whisper_model() -> Any:
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        from faster_whisper import WhisperModel

        # 'tiny' int8 runs in <0.5s on Apple Silicon / modern CPUs
        _WHISPER_MODEL = WhisperModel("tiny", device="cpu", compute_type="int8")
    return _WHISPER_MODEL


def _load_audio_as_float32(file_path: str) -> np.ndarray:
    """Convert any audio file to 16kHz mono 16-bit PCM WAV, then return normalized float32 array."""
    if not _is_ffmpeg_available():
        return np.array([], dtype=np.float32)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f_wav:
        temp_wav = f_wav.name

    try:
        subprocess.run(
            [
                FFMPEG_PATH,
                "-i",
                file_path,
                "-ar",
                "16000",
                "-ac",
                "1",
                "-f",
                "wav",
                temp_wav,
                "-y",
                "-loglevel",
                "error",
            ],
            check=True,
            timeout=30.0,
        )
        with wave.open(temp_wav, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            return samples
    except Exception:
        return np.array([], dtype=np.float32)
    finally:
        if os.path.exists(temp_wav):
            try:
                os.unlink(temp_wav)
            except OSError:
                pass


def _transcribe_sync(file_path: str, language: str = "ru") -> str:
    samples = _load_audio_as_float32(file_path)
    if len(samples) == 0:
        return ""
    try:
        model = _get_whisper_model()
        segments, _info = model.transcribe(
            samples,
            beam_size=5,
            language=language if language else None,
            vad_filter=True,
        )
        return " ".join(s.text.strip() for s in segments).strip()
    except Exception:
        return ""


async def transcribe_audio_file(file_path: str, language: str = "ru") -> str:
    """Transcribe a local audio file asynchronously."""
    return await asyncio.to_thread(_transcribe_sync, file_path, language)


async def _transcribe_cloud(
    data: bytes,
    filename: str = "audio.ogg",
    language: str = "ru",
    openai_key: str = "",
    groq_key: str = "",
) -> str:
    import httpx

    # 1. Try Groq Whisper (free & ultra-fast)
    clean_groq = str(groq_key or "").strip()
    if clean_groq:
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                files = {"file": (filename, data, "application/octet-stream")}
                form_data = {"model": "whisper-large-v3-turbo"}
                if language:
                    form_data["language"] = language
                res = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {clean_groq}"},
                    data=form_data,
                    files=files,
                )
                if res.status_code == 200:
                    text = str(res.json().get("text") or "").strip()
                    if text:
                        return text
        except Exception:
            pass

    # 2. Try OpenAI Whisper (whisper-1)
    clean_openai = str(openai_key or "").strip()
    if clean_openai:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                files = {"file": (filename, data, "application/octet-stream")}
                form_data = {"model": "whisper-1"}
                if language:
                    form_data["language"] = language
                res = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {clean_openai}"},
                    data=form_data,
                    files=files,
                )
                if res.status_code == 200:
                    text = str(res.json().get("text") or "").strip()
                    if text:
                        return text
        except Exception:
            pass

    return ""


async def transcribe_audio_bytes(
    data: bytes,
    suffix: str = ".ogg",
    language: str = "ru",
    openai_key: str = "",
    groq_key: str = "",
) -> str:
    """Transcribe in-memory audio bytes (.ogg from Telegram or .webm from browser mic)."""
    if not data:
        return ""

    # 1. Try local faster-whisper if ffmpeg is installed
    if _is_ffmpeg_available():
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(data)
            temp_input = f.name
        try:
            local_text = await transcribe_audio_file(temp_input, language=language)
            if local_text:
                return local_text
        except Exception:
            pass
        finally:
            if os.path.exists(temp_input):
                try:
                    os.unlink(temp_input)
                except OSError:
                    pass

    # 2. Fall back to cloud Whisper (Groq / OpenAI)
    if groq_key or openai_key:
        cloud_text = await _transcribe_cloud(
            data,
            filename=f"audio{suffix}",
            language=language,
            openai_key=openai_key,
            groq_key=groq_key,
        )
        if cloud_text:
            return cloud_text

    return ""
