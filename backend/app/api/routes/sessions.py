"""Chat sessions CRUD and FTS5 search routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import get_container
from app.container import AppContainer
from app.runtime.sessions import MessageRecord, SessionRecord

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class CreateSessionPayload(BaseModel):
    title: str = Field(default="New chat", max_length=120)
    brain: str | None = None
    channel: str = Field(default="web", max_length=32)
    pinned_provider: str = ""
    pinned_model: str = ""


class UpdateSessionPayload(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    pinned_provider: str | None = None
    pinned_model: str | None = None


def _session_dict(s: SessionRecord) -> dict:
    return {
        "id": s.id,
        "title": s.title,
        "brain": s.brain,
        "channel": s.channel,
        "pinned_provider": s.pinned_provider,
        "pinned_model": s.pinned_model,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "message_count": s.message_count,
    }


def _message_dict(m: MessageRecord) -> dict:
    return {
        "id": m.id,
        "session_id": m.session_id,
        "role": m.role,
        "content": m.content,
        "provider": m.provider,
        "model": m.model,
        "meta": m.meta,
        "created_at": m.created_at,
    }


@router.get("")
def list_sessions(
    brain: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    container: AppContainer = Depends(get_container),
) -> dict:
    items = container.sessions.list_sessions(brain=brain, channel=channel, limit=limit)
    return {"sessions": [_session_dict(s) for s in items]}


@router.get("/search")
def search_sessions(
    q: str = Query(..., min_length=1),
    brain: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    container: AppContainer = Depends(get_container),
) -> dict:
    hits = container.sessions.search_messages(q, brain=brain, limit=limit)
    return {
        "query": q,
        "hits": [
            {
                "session_id": h.session_id,
                "session_title": h.session_title,
                "message_id": h.message_id,
                "role": h.role,
                "snippet": h.snippet,
                "created_at": h.created_at,
            }
            for h in hits
        ],
    }


@router.post("")
def create_session(payload: CreateSessionPayload, container: AppContainer = Depends(get_container)) -> dict:
    brain_name = payload.brain or container.settings.load_public()["active_brain"]
    rec = container.sessions.create_session(
        brain=brain_name,
        title=payload.title,
        channel=payload.channel,
        pinned_provider=payload.pinned_provider,
        pinned_model=payload.pinned_model,
    )
    return _session_dict(rec)


@router.get("/{session_id}")
def get_session(session_id: str, container: AppContainer = Depends(get_container)) -> dict:
    rec = container.sessions.get_session(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Session not found")
    msgs = container.sessions.list_messages(session_id)
    return {
        "session": _session_dict(rec),
        "messages": [_message_dict(m) for m in msgs],
    }


@router.patch("/{session_id}")
def update_session(
    session_id: str,
    payload: UpdateSessionPayload,
    container: AppContainer = Depends(get_container),
) -> dict:
    rec = container.sessions.update_session(
        session_id,
        title=payload.title,
        pinned_provider=payload.pinned_provider,
        pinned_model=payload.pinned_model,
    )
    if rec is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_dict(rec)


@router.delete("/{session_id}")
def delete_session(session_id: str, container: AppContainer = Depends(get_container)) -> dict:
    deleted = container.sessions.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}
