"""Self-Learning, Git rollback, Session Compaction, and Usage Tracking routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import get_container
from app.brain.git_sync import GitBrain
from app.container import AppContainer

router = APIRouter(tags=["learn"])


class RunLearnPayload(BaseModel):
    dry_run: bool = False


class RollbackPayload(BaseModel):
    sha: str


def _brain(container: AppContainer, brain: str | None) -> str:
    return brain or container.settings.load_public()["active_brain"]


@router.post("/api/learn/run")
async def run_self_learning(
    payload: RunLearnPayload,
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    b = _brain(container, brain)
    return await container.learner.run_learning_pass(brain=b, dry_run=payload.dry_run)


@router.get("/api/learn/history")
def learn_git_history(
    brain: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    container: AppContainer = Depends(get_container),
) -> dict:
    b = _brain(container, brain)
    root = container.vaults.brain_path(b)
    return {
        "brain": b,
        "is_git_repo": GitBrain.is_git_repo(root),
        "commits": GitBrain.recent_commits(root, limit=limit),
    }


@router.post("/api/learn/rollback")
def rollback_learning_commit(
    payload: RollbackPayload,
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    b = _brain(container, brain)
    root = container.vaults.brain_path(b)
    ok, msg = GitBrain.rollback_commit(root, payload.sha)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg}


@router.get("/api/learn/usage")
def usage_summary(
    days: int = Query(default=30, ge=1, le=365),
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    return container.usage.summary(days=days, brain=brain)


@router.post("/api/sessions/{session_id}/compact")
async def compact_session(
    session_id: str,
    force: bool = Query(default=True),
    container: AppContainer = Depends(get_container),
) -> dict:
    try:
        return await container.compactor.compact_session(session_id, force=force)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
