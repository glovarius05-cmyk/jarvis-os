"""Gemini Multimodal Live API WebSocket bridge for full-duplex real-time voice Q&A."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
import websockets

from app.api.deps import get_container
from app.container import AppContainer
from app.security.auth import SESSION_COOKIE_NAME

logger = logging.getLogger("jarvis.voice_live")

router = APIRouter(tags=["voice_live"])

DEFAULT_GEMINI_LIVE_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"
GEMINI_LIVE_WS_BASE = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)


def _resolve_gemini_key(container: AppContainer) -> str:
    cfg = container.settings.load()
    return str(cfg.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY") or "").strip()


def _build_live_system_instruction(container: AppContainer, brain: str) -> str:
    now_utc = datetime.now(timezone.utc)
    now_msk = now_utc.astimezone(timezone(timedelta(hours=3)))
    parts = [
        "Ты Jarvis - персональный голосовой ИИ-ассистент.",
        f"Текущая дата и время: {now_msk.strftime('%Y-%m-%d %H:%M')} (MSK, UTC+3) / {now_utc.strftime('%Y-%m-%d %H:%M')} UTC.",
        "Отвечай естественно, кратко и по делу (1-3 предложения, если пользователь не просит подробностей).",
        "Говори на языке пользователя (обычно на русском).",
        "Если вопрос касается актуальных событий, новостей, курсов, погоды или информации на сегодня - используй Google Search или инструменты браузера (jarvis_browser_navigate / jarvis_browser_task).",
    ]
    try:
        core_mem = container.core_memory.render(brain)
        if core_mem and core_mem.strip():
            parts.append(f"\nКонтекст памяти пользователя:\n{core_mem[:2000]}")
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(parts)


def _build_live_tools() -> list[dict[str, Any]]:
    return [
        {"googleSearch": {}},
        {
            "functionDeclarations": [
                {
                    "name": "jarvis_browser_navigate",
                    "description": "Open a specific webpage URL and extract its title and text content.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "Target webpage URL to visit"},
                        },
                        "required": ["url"],
                    },
                },
                {
                    "name": "jarvis_browser_task",
                    "description": "Autonomous browser agent for multi-step web tasks (clicking, forms, SPA pages).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "task": {"type": "string", "description": "Goal or instruction for the browser agent"},
                            "url": {"type": "string", "description": "Initial target website URL"},
                        },
                        "required": ["task"],
                    },
                },
            ]
        },
    ]


@router.get("/api/voice/live/status")
def voice_live_status(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    key = _resolve_gemini_key(container)
    return {
        "configured": bool(key),
        "provider": "gemini-live",
        "model": DEFAULT_GEMINI_LIVE_MODEL,
    }


@router.websocket("/ws/voice/live")
async def ws_voice_live(websocket: WebSocket) -> None:
    container: AppContainer = websocket.app.state.container

    if container.auth.login_required():
        token = websocket.cookies.get(SESSION_COOKIE_NAME)
        if not container.auth.validate_session(token):
            await websocket.close(code=4401, reason="Authentication required")
            return

    await websocket.accept()

    api_key = _resolve_gemini_key(container)
    if not api_key:
        await websocket.send_json({"type": "error", "message": "Gemini API key не настроен в Models."})
        await websocket.close(code=4400, reason="Gemini API key missing")
        return

    session_id = str(websocket.query_params.get("session_id") or "").strip()
    voice_name = str(websocket.query_params.get("voice") or "Charon").strip() or "Charon"
    model = str(websocket.query_params.get("model") or DEFAULT_GEMINI_LIVE_MODEL).strip()
    if not model.startswith("models/"):
        model = f"models/{model}"

    active_brain = container.settings.load_public().get("active_brain", "Brain Default")
    system_prompt = _build_live_system_instruction(container, active_brain)
    ws_url = f"{GEMINI_LIVE_WS_BASE}?key={quote(api_key, safe='')}"

    user_buf: list[str] = []
    assistant_buf: list[str] = []

    def _flush_transcripts() -> None:
        if not session_id:
            return
        u_text = "".join(user_buf).strip()
        a_text = "".join(assistant_buf).strip()
        user_buf.clear()
        assistant_buf.clear()
        try:
            if u_text:
                container.sessions.add_message(session_id, "user", u_text, provider="gemini-live", model=model)
            if a_text:
                container.sessions.add_message(session_id, "assistant", a_text, provider="gemini-live", model=model)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to persist live voice transcript: %s", exc)

    try:
        async with websockets.connect(ws_url, max_size=8 * 1024 * 1024) as gemini_ws:
            setup_msg = {
                "setup": {
                    "model": model,
                    "generationConfig": {
                        "responseModalities": ["AUDIO"],
                        "speechConfig": {
                            "voiceConfig": {
                                "prebuiltVoiceConfig": {
                                    "voiceName": voice_name,
                                }
                            }
                        },
                    },
                    "systemInstruction": {
                        "parts": [{"text": system_prompt}],
                    },
                    "tools": _build_live_tools(),
                    "inputAudioTranscription": {},
                    "outputAudioTranscription": {},
                }
            }
            await gemini_ws.send(json.dumps(setup_msg))

            async def _browser_to_gemini() -> None:
                while True:
                    raw = await websocket.receive_text()
                    msg = json.loads(raw)
                    mtype = msg.get("type")
                    if mtype == "audio":
                        b64 = msg.get("data") or ""
                        if b64:
                            await gemini_ws.send(
                                json.dumps(
                                    {
                                        "realtimeInput": {
                                            "mediaChunks": [
                                                {
                                                    "mimeType": "audio/pcm;rate=16000",
                                                    "data": b64,
                                                }
                                            ]
                                        }
                                    }
                                )
                            )
                    elif mtype == "text":
                        text = str(msg.get("text") or "").strip()
                        if text:
                            await gemini_ws.send(
                                json.dumps(
                                    {
                                        "clientContent": {
                                            "turns": [{"role": "user", "parts": [{"text": text}]}],
                                            "turnComplete": True,
                                        }
                                    }
                                )
                            )
                    elif mtype == "ping":
                        await websocket.send_json({"type": "pong"})
                    elif mtype == "stop":
                        break

            async def _gemini_to_browser() -> None:
                async for raw in gemini_ws:
                    data = json.loads(raw)
                    if "setupComplete" in data:
                        await websocket.send_json({"type": "ready"})
                        continue

                    tool_call = data.get("toolCall")
                    if isinstance(tool_call, dict):
                        fcalls = tool_call.get("functionCalls") or []
                        responses: list[dict[str, Any]] = []
                        for fc in fcalls:
                            if not isinstance(fc, dict):
                                continue
                            fc_id = str(fc.get("id") or "")
                            fc_name = str(fc.get("name") or "")
                            fc_args = fc.get("args") if isinstance(fc.get("args"), dict) else {}
                            await websocket.send_json({"type": "tool", "name": fc_name, "status": "running"})
                            try:
                                result = await container.hub.call_tool(
                                    brain=active_brain,
                                    name=fc_name,
                                    args=fc_args,
                                    run_mode="full",
                                    session_id=session_id or "voice-live",
                                )
                            except Exception as exc:  # noqa: BLE001
                                result = f"Tool error: {exc}"
                            await websocket.send_json({"type": "tool", "name": fc_name, "status": "done"})
                            responses.append(
                                {
                                    "id": fc_id,
                                    "name": fc_name,
                                    "response": {"result": str(result)[:12000]},
                                }
                            )
                        if responses:
                            await gemini_ws.send(json.dumps({"toolResponse": {"functionResponses": responses}}))
                        continue

                    sc = data.get("serverContent")
                    if not isinstance(sc, dict):
                        continue

                    if sc.get("interrupted"):
                        _flush_transcripts()
                        await websocket.send_json({"type": "interrupted"})

                    in_tr = sc.get("inputTranscription")
                    if isinstance(in_tr, dict) and in_tr.get("text"):
                        txt = str(in_tr["text"])
                        user_buf.append(txt)
                        await websocket.send_json({"type": "transcript", "role": "user", "text": txt})

                    out_tr = sc.get("outputTranscription")
                    if isinstance(out_tr, dict) and out_tr.get("text"):
                        txt = str(out_tr["text"])
                        assistant_buf.append(txt)
                        await websocket.send_json({"type": "transcript", "role": "assistant", "text": txt})

                    mt = sc.get("modelTurn")
                    if isinstance(mt, dict):
                        for part in mt.get("parts") or []:
                            inline = part.get("inlineData") if isinstance(part, dict) else None
                            if isinstance(inline, dict) and inline.get("data"):
                                mime = str(inline.get("mimeType") or "audio/pcm;rate=24000")
                                rate = 24000
                                if "rate=" in mime:
                                    try:
                                        rate = int(mime.split("rate=")[1].split(";")[0])
                                    except ValueError:
                                        rate = 24000
                                await websocket.send_json(
                                    {
                                        "type": "audio",
                                        "data": inline["data"],
                                        "rate": rate,
                                    }
                                )

                    if sc.get("turnComplete"):
                        _flush_transcripts()
                        await websocket.send_json({"type": "turn_complete"})

            t_in = asyncio.create_task(_browser_to_gemini())
            t_out = asyncio.create_task(_gemini_to_browser())
            done, pending = await asyncio.wait({t_in, t_out}, return_when=asyncio.FIRST_COMPLETED)
            for p in pending:
                p.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for d in done:
                exc = d.exception()
                if exc and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
                    raise exc
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini Live WS error: %s", exc)
        try:
            await websocket.send_json({"type": "error", "message": f"Gemini Live error: {exc}"})
        except Exception:  # noqa: BLE001
            pass
    finally:
        _flush_transcripts()
