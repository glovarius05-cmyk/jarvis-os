"""API endpoints for Experience Learning & Self-Healing Memory."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import get_container
from app.container import AppContainer

router = APIRouter(prefix="/api/experience", tags=["experience"])


class RecordFixPayload(BaseModel):
    error_text: str = Field(..., min_length=1)
    solution_patch: str = Field(..., min_length=1)
    failing_command: str = ""
    brain: str = "global"


class SearchFixPayload(BaseModel):
    query: str = Field(..., min_length=1)
    failing_command: str = ""
    min_confidence: float = 0.3
    limit: int = 5


class FeedbackPayload(BaseModel):
    solution_id: str = Field(..., min_length=1)
    success: bool = True


@router.get("/list")
def list_experience_solutions(
    limit: int = Query(default=50, ge=1, le=200),
    error_type: str = Query(default=""),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    solutions = container.experience.list_solutions(limit=limit, error_type=error_type)
    return {"solutions": solutions, "count": len(solutions)}


@router.get("/stats")
def get_experience_stats(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    return container.experience.stats()


@router.post("/record")
def record_experience_fix(
    payload: RecordFixPayload,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    rec = container.experience.record_fix(
        error_text=payload.error_text,
        solution_patch=payload.solution_patch,
        failing_cmd=payload.failing_command,
        brain=payload.brain,
    )
    return {"ok": True, "solution": rec}


@router.post("/search")
def search_experience_fixes(
    payload: SearchFixPayload,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    solutions = container.experience.find_solutions(
        error_text=payload.query,
        failing_cmd=payload.failing_command,
        min_confidence=payload.min_confidence,
        limit=payload.limit,
    )
    return {"query": payload.query, "matches": solutions, "count": len(solutions)}


@router.post("/feedback")
def submit_experience_feedback(
    payload: FeedbackPayload,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    container.experience.record_feedback(payload.solution_id, payload.success)
    updated = container.experience.get_solution(payload.solution_id)
    return {"ok": True, "solution": updated}


@router.delete("/{solution_id}")
def delete_experience_solution(
    solution_id: str,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    deleted = container.experience.delete_solution(solution_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Solution not found")
    return {"ok": True, "deleted_id": solution_id}
