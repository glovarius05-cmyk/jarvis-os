"""First-run onboarding wizard routes."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_container
from app.container import AppContainer

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


class OnboardingPayload(BaseModel):
    provider: str = Field(default="openrouter")
    model: str = Field(default="")
    api_key: str = Field(default="")
    character_template: str = Field(default="default")
    admin_user: str = Field(default="")
    admin_password: str = Field(default="")


@router.get("/status")
async def get_onboarding_status(container: AppContainer = Depends(get_container)) -> dict:
    public_settings = container.settings.load_public()
    completed = bool(public_settings.get("onboarding_completed", False))

    # Detect local Ollama
    has_ollama = False
    local_models: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get("http://127.0.0.1:11434/api/tags")
            if resp.status_code == 200:
                has_ollama = True
                data = resp.json()
                local_models = [m["name"] for m in data.get("models", []) if "name" in m]
    except Exception:  # noqa: BLE001
        has_ollama = False

    # Check configured keys
    has_keys = bool(
        public_settings.get("openrouter_api_key")
        or public_settings.get("gemini_api_key")
        or public_settings.get("perplexity_api_key")
        or public_settings.get("openai_api_key")
    )

    templates = container.characters.list_templates()
    active_brain = public_settings.get("active_brain", "Brain Default")

    return {
        "completed": completed,
        "has_keys": has_keys,
        "has_ollama": has_ollama,
        "local_models": local_models,
        "templates": templates,
        "active_brain": active_brain,
        "needs_admin": container.auth._force_login and not container.auth.has_admin(),
    }


@router.post("/complete")
def complete_onboarding(payload: OnboardingPayload, container: AppContainer = Depends(get_container)) -> dict:
    active_brain = container.settings.load_public().get("active_brain", "Brain Default")

    updates: dict = {"onboarding_completed": True}

    # 1. Admin user if supplied
    if payload.admin_user and payload.admin_password and not container.auth.has_admin():
        container.auth.setup_admin(payload.admin_user, payload.admin_password)

    # 2. Store API key if provided
    key_field_map = {
        "openrouter": "openrouter_api_key",
        "gemini": "gemini_api_key",
        "perplexity": "perplexity_api_key",
        "openai": "openai_api_key",
    }
    if payload.api_key and payload.provider in key_field_map:
        updates[key_field_map[payload.provider]] = payload.api_key

    # 3. Model preference
    if payload.provider:
        model_name = payload.model
        if not model_name:
            if payload.provider == "local-llama":
                model_name = "llama3.2:latest"
            elif payload.provider == "gemini":
                model_name = "gemini-3-flash-preview"
            elif payload.provider == "openai":
                model_name = "gpt-4o"
            else:
                model_name = "meta-llama/llama-3.3-70b-instruct:free"
        updates["model"] = {
            "provider": payload.provider,
            "name": model_name,
        }

    container.settings.update(updates)

    # 4. Apply character template
    if payload.character_template:
        try:
            container.characters.apply_template(active_brain, payload.character_template)
        except ValueError:
            pass

    return {
        "ok": True,
        "message": "Welcome to Jarvis OS! Setup is complete.",
        "active_brain": active_brain,
    }
