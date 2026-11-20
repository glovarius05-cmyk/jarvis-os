"""Health, version, and system status routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app import APP_VERSION
from app.api.deps import get_container
from app.container import AppContainer

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health(container: AppContainer = Depends(get_container)) -> dict:
    return {
        "status": "ok",
        "version": APP_VERSION,
        "fts_enabled": container.sessions.fts_enabled,
    }


@router.get("/version")
def version() -> dict:
    return {"version": APP_VERSION}


@router.get("/system/status")
def system_status(container: AppContainer = Depends(get_container)) -> dict:
    settings = container.settings.load_public()
    brains = container.vaults.list_brains()
    return {
        "version": APP_VERSION,
        "active_brain": settings["active_brain"],
        "brains_count": len(brains),
        "fts_enabled": container.sessions.fts_enabled,
        "model": settings["model"],
        "local_llama": settings["local_llama"],
        "cli_providers": settings["cli_providers"],
        "secrets_configured": settings["secrets_configured"],
    }
