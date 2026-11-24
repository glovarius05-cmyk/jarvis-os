"""Telemetry & Observability API routes for inspecting agent traces and spans."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_container
from app.container import AppContainer

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])


@router.get("/traces")
def list_traces(
    brain: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    container: AppContainer = Depends(get_container),
) -> dict:
    traces = container.telemetry.list_traces(brain=brain, session_id=session_id, limit=limit)
    return {"traces": traces, "count": len(traces)}


@router.get("/traces/{trace_id}")
def get_trace(
    trace_id: str,
    container: AppContainer = Depends(get_container),
) -> dict:
    trace = container.telemetry.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace not found: {trace_id}")
    return trace


@router.get("/summary")
def telemetry_summary(
    days: int = Query(default=7, ge=1, le=365),
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    return container.telemetry.summary(days=days, brain=brain)
