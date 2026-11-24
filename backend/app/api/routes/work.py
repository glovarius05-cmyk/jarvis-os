"""Autonomous background work routes: Kanban tasks, Multi-Loop, Reminders/Cron, and Inbox."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import get_container
from app.container import AppContainer

router = APIRouter(prefix="/api/work", tags=["work"])


class CreateTaskPayload(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    mode: str = Field(default="auto", pattern="^(suggest|auto|full)$")
    route: str = "auto"
    owner_chat: str = ""


class AutoTogglePayload(BaseModel):
    enabled: bool


class AnswerTaskPayload(BaseModel):
    answer: str = Field(..., min_length=1)


class CreateLoopPayload(BaseModel):
    slug: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=120)
    prompt: str = Field(..., min_length=1)
    interval_min: int = Field(default=60, ge=5, le=10080)
    mode: str = Field(default="auto", pattern="^(suggest|auto|full)$")
    enabled: bool = False
    quiet_hours: str = ""
    max_runs_per_day: int = Field(default=24, ge=1, le=288)
    owner_chat: str = ""


class CreateReminderPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    prompt: str = Field(..., min_length=1)
    cron: str = ""
    run_at_ts: float = 0.0
    notify_only: bool = False
    mode: str = Field(default="auto", pattern="^(suggest|auto|full)$")
    owner_chat: str = ""
    required_connections: list[str] = Field(default_factory=list)
    allow_missing: bool = False


def _brain(container: AppContainer, brain: str | None) -> str:
    return brain or container.settings.load_public()["active_brain"]


# ---- Kanban ----

@router.get("/tasks")
def list_tasks(brain: str | None = Query(default=None), container: AppContainer = Depends(get_container)) -> dict:
    b = _brain(container, brain)
    return {
        "brain": b,
        "auto_enabled": container.kanban.is_auto_enabled(b),
        "tasks": [t.to_dict() for t in container.kanban.list_tasks(brain=b)],
    }


@router.post("/tasks")
def create_task(
    payload: CreateTaskPayload,
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    b = _brain(container, brain)
    try:
        task = container.kanban.create_task(
            brain=b,
            title=payload.title,
            description=payload.description,
            mode=payload.mode,
            route=payload.route,
            owner_chat=payload.owner_chat,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return task.to_dict()


@router.post("/tasks/auto")
def toggle_kanban_auto(
    payload: AutoTogglePayload,
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    b = _brain(container, brain)
    enabled = container.kanban.set_auto_enabled(b, payload.enabled)
    return {"brain": b, "auto_enabled": enabled}


@router.post("/tasks/{task_id}/run")
async def run_task_now(task_id: str, container: AppContainer = Depends(get_container)) -> dict:
    try:
        task = await container.kanban.execute_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return task.to_dict()


@router.post("/tasks/{task_id}/answer")
def answer_task(
    task_id: str,
    payload: AnswerTaskPayload,
    container: AppContainer = Depends(get_container),
) -> dict:
    try:
        return container.kanban.answer_task(task_id, payload.answer).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/accept")
def accept_task(task_id: str, container: AppContainer = Depends(get_container)) -> dict:
    try:
        return container.kanban.accept_task(task_id).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, container: AppContainer = Depends(get_container)) -> dict:
    try:
        return container.kanban.cancel_task(task_id).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---- Loops ----

@router.get("/loops")
def list_loops(brain: str | None = Query(default=None), container: AppContainer = Depends(get_container)) -> dict:
    b = _brain(container, brain)
    return {"brain": b, "loops": container.loops.list_loops(b)}


@router.post("/loops")
def create_loop(
    payload: CreateLoopPayload,
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    b = _brain(container, brain)
    try:
        return container.loops.create_loop(
            brain=b,
            slug=payload.slug,
            name=payload.name,
            prompt=payload.prompt,
            interval_min=payload.interval_min,
            mode=payload.mode,
            enabled=payload.enabled,
            quiet_hours=payload.quiet_hours,
            max_runs_per_day=payload.max_runs_per_day,
            owner_chat=payload.owner_chat,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/loops/{slug}/toggle")
def toggle_loop(
    slug: str,
    payload: AutoTogglePayload,
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    b = _brain(container, brain)
    updated = container.loops.toggle_loop(b, slug, payload.enabled)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Loop '{slug}' not found")
    return updated


@router.post("/loops/{slug}/run")
async def run_loop_now(
    slug: str,
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    b = _brain(container, brain)
    try:
        return await container.loops.run_loop(b, slug, manual=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---- Reminders ----

@router.get("/reminders")
def list_reminders(brain: str | None = Query(default=None), container: AppContainer = Depends(get_container)) -> dict:
    b = _brain(container, brain)
    return {"brain": b, "reminders": container.reminders.list_reminders(b)}


@router.post("/reminders")
def create_reminder(
    payload: CreateReminderPayload,
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    b = _brain(container, brain)
    try:
        return container.reminders.create_reminder(
            brain=b,
            name=payload.name,
            prompt=payload.prompt,
            cron=payload.cron,
            run_at_ts=payload.run_at_ts,
            notify_only=payload.notify_only,
            mode=payload.mode,
            owner_chat=payload.owner_chat,
            required_connections=payload.required_connections,
            allow_missing=payload.allow_missing,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/reminders/{reminder_id}")
def delete_reminder(
    reminder_id: str,
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    b = _brain(container, brain)
    if not container.reminders.delete_reminder(b, reminder_id):
        raise HTTPException(status_code=404, detail="Reminder not found")
    return {"ok": True}


@router.post("/reminders/{reminder_id}/run")
async def run_reminder(
    reminder_id: str,
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    b = _brain(container, brain)
    reminders = container.reminders.list_reminders(b)
    target = next((r for r in reminders if r.get("id") == reminder_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Reminder not found")
    result = await container.reminders.fire_reminder(b, target)
    return {"ok": True, "result": result}


# ---- Inbox ----

@router.get("/inbox")
def list_inbox(
    brain: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    container: AppContainer = Depends(get_container),
) -> dict:
    items = container.inbox.list_items(brain=brain, limit=limit)
    return {
        "unread_count": container.inbox.unread_count(brain=brain),
        "items": [i.to_dict() for i in items],
    }


@router.post("/inbox/read")
def mark_inbox_read(
    item_id: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    changed = container.inbox.mark_read(item_id)
    return {"ok": True, "marked": changed}
