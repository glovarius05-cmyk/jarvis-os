"""Settings endpoints (never returns plaintext secrets)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_container
from app.container import AppContainer

router = APIRouter(prefix="/api/settings", tags=["settings"])

ALLOWED_TOP_KEYS = {
    "active_brain",
    "ui_lang",
    "timezone",
    "model",
    "local_llama",
    "cli_providers",
    "telegram",
    "voice",
    "openrouter_api_key",
    "gemini_api_key",
    "openai_api_key",
    "anthropic_api_key",
    "groq_api_key",
    "telegram_bot_token",
    "composio_api_key",
    "composio_connected_apps",
    "composio_account_emails",
    "composio_account_ids",
    "perplexity_api_key",
    "deepseek_api_key",
    "moonshot_api_key",
    "dashscope_api_key",
    "onboarding_completed",
}


class SettingsPatch(BaseModel):
    updates: dict[str, Any]


@router.get("")
def get_settings(container: AppContainer = Depends(get_container)) -> dict:
    return container.settings.load_public()


@router.patch("")
def patch_settings(payload: SettingsPatch, container: AppContainer = Depends(get_container)) -> dict:
    unknown = set(payload.updates.keys()) - ALLOWED_TOP_KEYS
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown setting keys: {sorted(unknown)}")
    if "active_brain" in payload.updates:
        try:
            container.vaults.ensure_brain(str(payload.updates["active_brain"]))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return container.settings.update(payload.updates)
