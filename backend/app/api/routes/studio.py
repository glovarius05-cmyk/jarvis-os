"""Studio (Skills, Plugins, Agents, Workflows) and Second Brain Knowledge Graph / File Editor routes."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.deps import get_container
from app.brain.graph import build_knowledge_graph
from app.brain.vault import VaultPathError, safe_resolve
from app.container import AppContainer

router = APIRouter(tags=["studio"])


class TogglePayload(BaseModel):
    enabled: bool


class SaveAgentPayload(BaseModel):
    slug: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=100)
    role: str = ""
    prompt: str = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=lambda: ["General"])
    skills: list[str] = Field(default_factory=list)
    model: str = ""
    model_provider: str = ""


class SaveWorkflowPayload(BaseModel):
    slug: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    group: str = "General"
    status: str = "on"
    steps: list[dict[str, Any]] = Field(..., min_length=1)


class RunWorkflowPayload(BaseModel):
    input_text: str = Field(..., min_length=1)
    mode: str = Field(default="auto", pattern="^(suggest|auto|full)$")


class WriteFilePayload(BaseModel):
    path: str = Field(..., min_length=1)
    content: str


def _active_brain(container: AppContainer, brain: str | None) -> str:
    return brain or container.settings.load_public()["active_brain"]


# ---- Skills ----

@router.get("/api/studio/skills")
def list_skills(brain: str | None = Query(default=None), container: AppContainer = Depends(get_container)) -> dict:
    b = _active_brain(container, brain)
    skills = container.skills.list_skills(b)
    return {
        "brain": b,
        "skills": [
            {
                "slug": s.slug,
                "name": s.name,
                "description": s.description,
                "group": s.group,
                "enabled": s.enabled,
                "is_system": s.is_system,
                "path": s.path,
            }
            for s in skills
        ],
    }


@router.post("/api/studio/skills/{slug}/toggle")
def toggle_skill(
    slug: str,
    payload: TogglePayload,
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    b = _active_brain(container, brain)
    updated = container.skills.toggle_skill(b, slug, payload.enabled)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Skill '{slug}' not found")
    return {"slug": updated.slug, "enabled": updated.enabled}


# ---- Plugins ----

@router.get("/api/studio/plugins")
def list_plugins(brain: str | None = Query(default=None), container: AppContainer = Depends(get_container)) -> dict:
    b = _active_brain(container, brain)
    recs = container.plugins.load_all(b)
    return {"brain": b, "plugins": [r.to_public_dict() for r in recs]}


@router.post("/api/studio/plugins/{slug}/toggle")
def toggle_plugin(
    slug: str,
    payload: TogglePayload,
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    b = _active_brain(container, brain)
    rec = container.plugins.toggle_plugin(slug, payload.enabled, brain=b)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{slug}' not found")
    return rec.to_public_dict()


# ---- Agents & Workflows ----

@router.get("/api/studio/agents")
def list_agents(brain: str | None = Query(default=None), container: AppContainer = Depends(get_container)) -> dict:
    b = _active_brain(container, brain)
    return {"brain": b, "agents": [a.to_dict() for a in container.agents.list_agents(b)]}


@router.post("/api/studio/agents")
def save_agent(
    payload: SaveAgentPayload,
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    b = _active_brain(container, brain)
    try:
        saved = container.agents.save_agent(
            b,
            slug=payload.slug,
            name=payload.name,
            role=payload.role,
            prompt=payload.prompt,
            tags=payload.tags,
            skills=payload.skills,
            model=payload.model,
            model_provider=payload.model_provider,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return saved.to_dict()


@router.get("/api/studio/workflows")
def list_workflows(brain: str | None = Query(default=None), container: AppContainer = Depends(get_container)) -> dict:
    b = _active_brain(container, brain)
    return {"brain": b, "workflows": [w.to_dict() for w in container.agents.list_workflows(b)]}


@router.post("/api/studio/workflows")
def save_workflow(
    payload: SaveWorkflowPayload,
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    b = _active_brain(container, brain)
    try:
        saved = container.agents.save_workflow(
            b,
            slug=payload.slug,
            name=payload.name,
            description=payload.description,
            group=payload.group,
            status=payload.status,
            steps=payload.steps,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return saved.to_dict()


@router.post("/api/studio/workflows/{slug}/run")
async def run_workflow(
    slug: str,
    payload: RunWorkflowPayload,
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    b = _active_brain(container, brain)
    try:
        record = await container.workflows.run(
            brain=b,
            workflow_slug=slug,
            input_text=payload.input_text,
            run_mode=payload.mode,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return record.to_dict()


@router.get("/api/studio/workflows/runs")
def list_workflow_runs(
    brain: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    container: AppContainer = Depends(get_container),
) -> dict:
    b = _active_brain(container, brain)
    runs = container.workflows.list_runs(brain=b, limit=limit)
    return {"runs": [r.to_dict() for r in runs]}


# ---- Knowledge Graph & Vault File Editor ----

@router.get("/api/brain/graph")
def get_brain_graph(brain: str | None = Query(default=None), container: AppContainer = Depends(get_container)) -> dict:
    b = _active_brain(container, brain)
    root = container.vaults.brain_path(b)
    return {"brain": b, **build_knowledge_graph(root)}


@router.get("/api/brain/file")
def read_brain_file(
    path: str = Query(..., min_length=1),
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    b = _active_brain(container, brain)
    try:
        target = safe_resolve(container.vaults.brain_path(b), path)
    except (ValueError, VaultPathError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return {"brain": b, "path": path, "content": target.read_text(encoding="utf-8", errors="replace")}


@router.put("/api/brain/file")
def write_brain_file(
    payload: WriteFilePayload,
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    b = _active_brain(container, brain)
    try:
        container.vaults.write_text(b, payload.path, payload.content)
    except (ValueError, VaultPathError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "brain": b, "path": payload.path}


@router.get("/api/brain/raw")
def get_brain_raw_file(
    path: str = Query(..., min_length=1),
    brain: str | None = Query(default=None),
    download: bool = Query(default=False),
    container: AppContainer = Depends(get_container),
) -> FileResponse:
    b = _active_brain(container, brain)
    try:
        target = safe_resolve(container.vaults.brain_path(b), path)
    except (ValueError, VaultPathError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    filename = target.name
    return FileResponse(target, filename=filename if download else None)


@router.get("/api/brain/download-zip")
def download_brain_zip(
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
):
    import io
    import re
    import zipfile
    from fastapi.responses import Response

    b = _active_brain(container, brain)
    root = container.vaults.brain_path(b)
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="Brain directory not found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in root.rglob("*"):
            if file.is_file():
                rel = file.relative_to(root)
                if any(part.startswith(".git") for part in rel.parts):
                    continue
                zf.write(file, arcname=str(rel))

    buf.seek(0)
    zip_bytes = buf.getvalue()
    safe_name = re.sub(r"[^\w\-]", "_", b)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="jarvis_brain_{safe_name}.zip"',
            "Content-Length": str(len(zip_bytes)),
        },
    )


@router.get("/api/storage/files")
def list_cloud_files(
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    """List downloadable files and artifacts from VPS cloud storage."""
    b = container.settings.load_public().get("active_brain") or "Brain Default"
    brain_root = container.vaults.brain_path(b)
    items = []
    if brain_root.is_dir():
        for f in sorted(brain_root.rglob("*")):
            if f.is_file():
                rel = f.relative_to(brain_root)
                if any(p.startswith(".git") for p in rel.parts):
                    continue
                rel_str = str(rel)
                items.append({
                    "name": f.name,
                    "path": rel_str,
                    "size": f.stat().st_size,
                    "folder": str(rel.parent) if str(rel.parent) != "." else "",
                    "download_url": f"/api/brain/raw?path={quote(rel_str, safe='/')}&download=true",
                })
    return {"brain": b, "files": items, "count": len(items)}
