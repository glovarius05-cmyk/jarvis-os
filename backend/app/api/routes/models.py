"""Provider discovery and local Llama probing endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_container
from app.container import AppContainer

router = APIRouter(prefix="/api/models", tags=["models"])


class TestLocalPayload(BaseModel):
    base_url: str = "http://127.0.0.1:11434/v1"


@router.get("/providers")
async def list_providers(container: AppContainer = Depends(get_container)) -> dict:
    providers = await container.engine_router.list_providers_status()
    settings = container.settings.load_public()
    return {
        "providers": providers,
        "model": settings["model"],
        "local_llama": settings["local_llama"],
        "secrets_configured": settings["secrets_configured"],
    }


@router.post("/test-local")
async def test_local_llama(
    payload: TestLocalPayload,
    container: AppContainer = Depends(get_container),
) -> dict:
    ok, models, error = await container.engine_router.discover_local_models(payload.base_url)
    return {
        "ok": ok,
        "base_url": payload.base_url,
        "models": models,
        "error": error,
    }
