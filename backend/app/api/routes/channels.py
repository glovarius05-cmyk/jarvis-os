"""Telegram owner bot management and Voice V2 (turn + TTS) routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Response, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import get_container
from app.container import AppContainer
from app.voice.tts import strip_markdown_for_tts, synthesize_edge_tts

router = APIRouter(tags=["channels-voice"])


class TelegramConfigPayload(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    allowed_chat_ids: list[str] = Field(default_factory=list)


class VoiceTurnPayload(BaseModel):
    transcript: str = Field(..., min_length=1)
    session_id: str = ""
    brain: str | None = None


class TTSPayload(BaseModel):
    text: str = Field(..., min_length=1)
    voice: str = ""
    rate: str = ""


@router.get("/api/channels/telegram/status")
def telegram_status(container: AppContainer = Depends(get_container)) -> dict:
    return container.telegram.status()


@router.post("/api/channels/telegram/config")
async def telegram_config(
    payload: TelegramConfigPayload,
    container: AppContainer = Depends(get_container),
) -> dict:
    updates: dict = {
        "telegram": {
            "enabled": payload.enabled,
            "allowed_chat_ids": [str(x).strip() for x in payload.allowed_chat_ids if str(x).strip()],
        }
    }
    if payload.bot_token.strip():
        updates["telegram_bot_token"] = payload.bot_token.strip()
    container.settings.update(updates)

    if container.telegram.is_enabled():
        container.telegram.start()
    else:
        await container.telegram.stop()

    return container.telegram.status()


@router.post("/api/channels/telegram/test")
async def telegram_test(container: AppContainer = Depends(get_container)) -> dict:
    return await container.telegram.test_connection()


@router.post("/api/voice/turn")
async def voice_turn(
    payload: VoiceTurnPayload,
    container: AppContainer = Depends(get_container),
) -> dict:
    brain = payload.brain or container.settings.load_public()["active_brain"]
    result = await container.voice_brain.process_utterance(
        brain=brain,
        transcript=payload.transcript,
        session_id=payload.session_id,
    )
    return result.to_dict()


@router.post("/api/voice/tts")
async def voice_tts(
    payload: TTSPayload,
    container: AppContainer = Depends(get_container),
):
    voice_cfg = container.settings.load_public().get("voice") or {}
    voice = payload.voice or str(voice_cfg.get("tts_voice") or "ru-RU-DmitryNeural")
    rate = payload.rate or str(voice_cfg.get("tts_rate") or "+5%")

    audio, err = await synthesize_edge_tts(payload.text, voice=voice, rate=rate)
    if audio:
        return Response(content=audio, media_type="audio/mpeg")
    return {
        "fallback": "browser_speech_synthesis",
        "spoken_text": strip_markdown_for_tts(payload.text),
        "reason": err,
    }


@router.post("/api/voice/stt")
async def voice_stt(
    file: UploadFile = File(...),
    container: AppContainer = Depends(get_container),
) -> dict[str, str]:
    from pathlib import Path
    from app.voice.stt import transcribe_audio_bytes

    data = await file.read()
    suffix = Path(file.filename or "audio.webm").suffix or ".webm"
    settings = container.settings.load()
    openai_key = str(settings.get("openai_api_key") or "")
    groq_key = str(settings.get("groq_api_key") or "")
    try:
        text = await transcribe_audio_bytes(
            data,
            suffix=suffix,
            language="ru",
            openai_key=openai_key,
            groq_key=groq_key,
        )
    except TypeError:
        text = await transcribe_audio_bytes(data, suffix=suffix, language="ru")
    return {"text": text}
