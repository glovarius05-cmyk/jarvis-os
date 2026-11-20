"""Chat WebSocket and HTTP turn endpoints backed by ChatRuntime."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.api.deps import get_container
from app.container import AppContainer
from app.security.auth import SESSION_COOKIE_NAME

router = APIRouter(tags=["chat"])


class SendChatPayload(BaseModel):
    session_id: str | None = None
    content: str = Field(..., min_length=1)
    provider: str = ""
    model: str = ""
    mode: str = Field(default="full", pattern="^(suggest|auto|full)$")


@router.get("/api/chat/active")
def active_turns(container: AppContainer = Depends(get_container)) -> dict:
    return {"active": container.chat_runtime.active_snapshot()}


@router.post("/api/chat/send")
async def send_chat(payload: SendChatPayload, container: AppContainer = Depends(get_container)) -> dict:
    sid = payload.session_id
    if not sid:
        brain = container.settings.load_public()["active_brain"]
        session = container.sessions.create_session(brain=brain, title="New chat", channel="web")
        sid = session.id

    try:
        job = container.chat_runtime.start_turn(
            session_id=sid,
            user_text=payload.content,
            provider=payload.provider,
            model=payload.model,
            run_mode=payload.mode,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "ok": True,
        "session_id": job.session_id,
        "provider": job.provider,
        "model": job.model,
    }


@router.post("/api/chat/stop/{session_id}")
def stop_chat(session_id: str, container: AppContainer = Depends(get_container)) -> dict:
    stopped = container.chat_runtime.stop_turn(session_id)
    return {"ok": True, "stopped": stopped}


@router.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket) -> None:
    container: AppContainer = websocket.app.state.container

    if container.auth.login_required():
        token = websocket.cookies.get(SESSION_COOKIE_NAME)
        if not container.auth.validate_session(token):
            await websocket.close(code=4401, reason="Authentication required")
            return

    await websocket.accept()
    client_id = uuid.uuid4().hex

    async def _send(event: dict) -> None:
        await websocket.send_json(event)

    def _on_drop() -> None:
        try:
            websocket._send  # noqa: B018
        except Exception:  # noqa: BLE001
            pass

    container.chat_runtime.add_client(client_id, _send, on_drop=_on_drop)
    try:
        # Immediately resync browser with any turns still running on the server
        await websocket.send_json(
            {
                "type": "hello",
                "active_jobs": container.chat_runtime.active_snapshot(),
            }
        )

        while True:
            msg = await websocket.receive_json()
            if not isinstance(msg, dict):
                continue
            action = str(msg.get("type") or "")

            if action == "ping":
                await websocket.send_json({"type": "pong"})
            elif action == "stop":
                sid = str(msg.get("session_id") or "")
                container.chat_runtime.stop_turn(sid)
            elif action == "send":
                sid = str(msg.get("session_id") or "")
                content = str(msg.get("content") or "")
                provider = str(msg.get("provider") or "")
                model = str(msg.get("model") or "")
                mode = str(msg.get("mode") or "full")
                if not sid:
                    brain = container.settings.load_public()["active_brain"]
                    sid = container.sessions.create_session(brain=brain, title="New chat", channel="web").id
                try:
                    container.chat_runtime.start_turn(
                        session_id=sid,
                        user_text=content,
                        provider=provider,
                        model=model,
                        run_mode=mode,
                    )
                except Exception as exc:  # noqa: BLE001
                    await websocket.send_json(
                        {
                            "type": "error",
                            "session_id": sid,
                            "content": str(exc),
                        }
                    )
    except WebSocketDisconnect:
        pass
    finally:
        # Removing subscriber NEVER cancels running server-side turns
        container.chat_runtime.remove_client(client_id)
