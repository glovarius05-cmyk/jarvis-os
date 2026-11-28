"""Chat WebSocket and HTTP turn endpoints backed by ChatRuntime."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from pathlib import Path
import re
from urllib.parse import quote_plus
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.api.deps import get_container
from app.container import AppContainer
from app.security.auth import extract_request_session_token

router = APIRouter(tags=["chat"])

MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".csv",
    ".tsv", ".html", ".css", ".yaml", ".yml", ".xml", ".sh", ".bash",
    ".sql", ".log", ".env", ".toml", ".ini", ".conf", ".rs", ".go",
    ".java", ".c", ".cpp", ".h", ".hpp",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp"}


class SendChatPayload(BaseModel):
    session_id: str | None = None
    content: str = Field(..., min_length=1)
    provider: str = ""
    model: str = ""
    mode: str = Field(default="full", pattern="^(suggest|auto|full|consensus|debate)$")


class UploadBase64Payload(BaseModel):
    filename: str = Field(..., min_length=1)
    content_base64: str = Field(..., min_length=1)
    content_type: str = ""
    brain: str | None = None


def _process_uploaded_file(
    container: AppContainer,
    filename: str,
    raw_bytes: bytes,
    content_type: str,
    brain: str | None = None,
) -> dict:
    if len(raw_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail=f"File exceeds maximum allowed size of {MAX_UPLOAD_SIZE // (1024 * 1024)}MB")

    active_brain = brain or container.settings.load_public()["active_brain"]
    clean_filename = re.sub(r"[^\w\.\-\_ ]", "_", Path(filename).name).strip() or "file"
    ext = Path(clean_filename).suffix.lower()

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    unique_prefix = uuid.uuid4().hex[:8]
    rel_path = f"uploads/{date_str}/{unique_prefix}_{clean_filename}"

    container.vaults.write_bytes(active_brain, rel_path, raw_bytes)

    is_image = content_type.startswith("image/") or ext in IMAGE_EXTENSIONS
    is_text = content_type.startswith("text/") or ext in TEXT_EXTENSIONS

    text_content: str | None = None
    if is_text:
        text_content = raw_bytes.decode("utf-8", errors="replace")[:100_000]

    # Index in hybrid index if text/markdown
    if ext in {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".js", ".ts", ".csv"}:
        try:
            container.hybrid_index.index_brain(container.vaults, active_brain)
        except Exception:  # noqa: BLE001
            pass

    return {
        "ok": True,
        "filename": clean_filename,
        "path": rel_path,
        "size": len(raw_bytes),
        "is_image": is_image,
        "is_text": is_text,
        "text_content": text_content,
        "url": f"/api/brain/raw?path={quote_plus(rel_path)}",
    }


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


@router.post("/api/chat/upload")
async def upload_chat_file(
    file: UploadFile = File(...),
    brain: str | None = Form(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    raw_bytes = await file.read()
    filename = file.filename or "file"
    content_type = file.content_type or ""
    return _process_uploaded_file(container, filename, raw_bytes, content_type, brain=brain)


@router.post("/api/chat/upload-base64")
def upload_chat_file_base64(
    payload: UploadBase64Payload,
    container: AppContainer = Depends(get_container),
) -> dict:
    try:
        b64 = payload.content_base64
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        raw_bytes = base64.b64decode(b64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid base64 payload") from exc
    return _process_uploaded_file(container, payload.filename, raw_bytes, payload.content_type, brain=payload.brain)


@router.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket) -> None:
    container: AppContainer = websocket.app.state.container

    if container.auth.login_required():
        token = extract_request_session_token(
            websocket.headers,
            websocket.cookies,
            websocket.query_params,
        )
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
