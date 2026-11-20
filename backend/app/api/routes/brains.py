"""Second Brain vault management routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_container
from app.container import AppContainer

router = APIRouter(prefix="/api/brains", tags=["brains"])


class CreateBrainPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    activate: bool = True


@router.get("")
def list_brains(container: AppContainer = Depends(get_container)) -> dict:
    active = container.settings.load_public()["active_brain"]
    brains = container.vaults.list_brains()
    return {
        "active_brain": active,
        "brains": [
            {
                "name": b.name,
                "is_git_repo": b.is_git_repo,
                "note_count": b.note_count,
                "active": b.name == active,
            }
            for b in brains
        ],
    }


@router.post("")
def create_brain(payload: CreateBrainPayload, container: AppContainer = Depends(get_container)) -> dict:
    try:
        info = container.vaults.ensure_brain(payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.activate:
        container.settings.update({"active_brain": info.name})
    return {
        "name": info.name,
        "is_git_repo": info.is_git_repo,
        "note_count": info.note_count,
        "active": payload.activate,
    }


class RememberPayload(BaseModel):
    fact: str = Field(..., min_length=1)
    section: str = Field(default="user")
    brain: str | None = None


class ForgetPayload(BaseModel):
    query: str = Field(..., min_length=1)
    section: str = Field(default="user")
    brain: str | None = None


class SaveCoreMemoryPayload(BaseModel):
    content: str = Field(...)
    brain: str | None = None


@router.get("/core-memory")
def get_core_memory(brain: str | None = None, container: AppContainer = Depends(get_container)) -> dict:
    active = brain or container.settings.load_public()["active_brain"]
    return container.core_memory.view(active)


@router.post("/core-memory/remember")
def core_memory_remember(payload: RememberPayload, container: AppContainer = Depends(get_container)) -> dict:
    active = payload.brain or container.settings.load_public()["active_brain"]
    msg = container.core_memory.remember(active, payload.fact, section=payload.section)
    return {"ok": True, "message": msg, "view": container.core_memory.view(active)}


@router.post("/core-memory/forget")
def core_memory_forget(payload: ForgetPayload, container: AppContainer = Depends(get_container)) -> dict:
    active = payload.brain or container.settings.load_public()["active_brain"]
    msg = container.core_memory.forget(active, payload.query, section=payload.section)
    return {"ok": True, "message": msg, "view": container.core_memory.view(active)}


@router.put("/core-memory")
def save_core_memory(payload: SaveCoreMemoryPayload, container: AppContainer = Depends(get_container)) -> dict:
    active = payload.brain or container.settings.load_public()["active_brain"]
    container.vaults.write_text(active, "memory/CORE_MEMORY.md", payload.content)
    return {"ok": True, "view": container.core_memory.view(active)}


class SaveCharacterPayload(BaseModel):
    content: str = Field(...)
    brain: str | None = None


class ApplyTemplatePayload(BaseModel):
    template_id: str = Field(...)
    brain: str | None = None


@router.get("/character")
def get_character(brain: str | None = None, container: AppContainer = Depends(get_container)) -> dict:
    active = brain or container.settings.load_public()["active_brain"]
    char = container.characters.get_character(active)
    rendered = container.characters.render_prompt_block(active)
    return {
        "brain": active,
        "character": char.to_dict(),
        "rendered_prompt": rendered,
    }


@router.put("/character")
def save_character(payload: SaveCharacterPayload, container: AppContainer = Depends(get_container)) -> dict:
    active = payload.brain or container.settings.load_public()["active_brain"]
    char = container.characters.save_character(active, payload.content)
    rendered = container.characters.render_prompt_block(active)
    return {
        "ok": True,
        "brain": active,
        "character": char.to_dict(),
        "rendered_prompt": rendered,
    }


@router.post("/character/template")
def apply_character_template(payload: ApplyTemplatePayload, container: AppContainer = Depends(get_container)) -> dict:
    active = payload.brain or container.settings.load_public()["active_brain"]
    try:
        char = container.characters.apply_template(active, payload.template_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rendered = container.characters.render_prompt_block(active)
    return {
        "ok": True,
        "brain": active,
        "template_id": payload.template_id,
        "character": char.to_dict(),
        "rendered_prompt": rendered,
    }


@router.get("/character/templates")
def list_character_templates(container: AppContainer = Depends(get_container)) -> dict:
    return {"templates": container.characters.list_templates()}


class ReindexPayload(BaseModel):
    force: bool = False
    brain: str | None = None


@router.get("/search")
def search_brain(
    query: str,
    limit: int = 10,
    path_filter: str = "",
    brain: str | None = None,
    container: AppContainer = Depends(get_container),
) -> dict:
    active = brain or container.settings.load_public()["active_brain"]
    hits = container.hybrid_index.search(active, query, limit=limit, path_filter=path_filter)
    if not hits:
        # Index on demand if brain has never been indexed
        container.hybrid_index.index_brain(container.vaults, active)
        hits = container.hybrid_index.search(active, query, limit=limit, path_filter=path_filter)
    return {"brain": active, "query": query, "hits": hits, "total": len(hits)}


@router.post("/reindex")
def reindex_brain(payload: ReindexPayload | None = None, container: AppContainer = Depends(get_container)) -> dict:
    p = payload or ReindexPayload()
    active = p.brain or container.settings.load_public()["active_brain"]
    result = container.hybrid_index.index_brain(container.vaults, active, force=p.force)
    return {"ok": True, "brain": active, **result}


class SyncTriggerPayload(BaseModel):
    brain: str | None = None
    force: bool = False


@router.get("/sync/status")
def get_sync_status(container: AppContainer = Depends(get_container)) -> dict:
    return container.watcher.status()


@router.post("/sync/trigger")
async def trigger_sync(
    payload: SyncTriggerPayload | None = None,
    container: AppContainer = Depends(get_container),
) -> dict:
    p = payload or SyncTriggerPayload()
    return await container.watcher.trigger_full_sync(brain=p.brain, force=p.force)


class GitRemotePayload(BaseModel):
    remote_url: str = Field(default="")
    brain: str | None = None


class GitSyncPayload(BaseModel):
    action: str = Field(default="all")  # "pull", "push", "all"
    brain: str | None = None


@router.get("/git/remote")
def get_git_remote(brain: str | None = None, container: AppContainer = Depends(get_container)) -> dict:
    active = brain or container.settings.load_public()["active_brain"]
    status = container.vaults.get_git_remote_status(active)
    return {"brain": active, **status}


@router.post("/git/remote")
def configure_git_remote(payload: GitRemotePayload, container: AppContainer = Depends(get_container)) -> dict:
    active = payload.brain or container.settings.load_public()["active_brain"]
    status = container.vaults.configure_git_remote(active, payload.remote_url)
    return {"ok": True, "brain": active, **status}


@router.post("/git/sync")
def sync_git_remote(payload: GitSyncPayload | None = None, container: AppContainer = Depends(get_container)) -> dict:
    p = payload or GitSyncPayload()
    active = p.brain or container.settings.load_public()["active_brain"]
    result = container.vaults.sync_git_remote(active, action=p.action)
    return {"brain": active, **result}
