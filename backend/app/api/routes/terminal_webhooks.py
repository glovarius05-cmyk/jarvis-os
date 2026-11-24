"""Owner Code Terminal, Incoming Webhooks (POST /hooks/{id}), and CLI Channel routes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import get_container
from app.container import AppContainer
from app.runtime.terminal import run_fenced_command

router = APIRouter(tags=["terminal-webhooks-cli"])


class TerminalExecPayload(BaseModel):
    command: str = Field(..., min_length=1, max_length=2000)
    cwd: str = ""
    timeout_s: float = Field(default=30.0, ge=1.0, le=180.0)


class CreateWebhookPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    action: str = Field(default="kanban", pattern="^(kanban|chat)$")
    prompt_template: str = "Webhook event received:\n{{payload}}"
    owner_chat: str = ""
    mode: str = Field(default="auto", pattern="^(suggest|auto|full)$")


class CliChatPayload(BaseModel):
    prompt: str = Field(..., min_length=1)
    peer_id: str = Field(default="local", max_length=64)
    mode: str = Field(default="full", pattern="^(suggest|auto|full)$")


# ---- Owner Terminal ----

@router.post("/api/terminal/exec")
async def terminal_exec(
    payload: TerminalExecPayload,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    brain = container.settings.load_public()["active_brain"]
    target_cwd = Path(payload.cwd).expanduser().resolve() if payload.cwd.strip() else container.vaults.brain_path(brain)
    sandbox_cfg = container.settings.load().get("sandbox", {}) if hasattr(container.settings, "load") else {}
    try:
        res = await run_fenced_command(
            payload.command,
            cwd=target_cwd,
            timeout_s=payload.timeout_s,
            extra_secrets=[container.hub.hub_token()],
            sandbox_mode=str(sandbox_cfg.get("mode") or "local").lower(),
            docker_image=str(sandbox_cfg.get("docker_image") or "python:3.12-slim"),
            docker_network=str(sandbox_cfg.get("network") or "none"),
            memory_limit=str(sandbox_cfg.get("memory_limit") or "512m"),
            cpus=str(sandbox_cfg.get("cpus") or "1.0"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return res.to_dict()


# ---- CLI Channel ----

@router.post("/api/chat/cli")
async def cli_chat(
    payload: CliChatPayload,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    return await container.turn_shell.handle_message(
        channel="cli",
        peer_id=payload.peer_id,
        text=payload.prompt,
        run_mode=payload.mode,
    )


# ---- Webhooks management + trigger ----

@router.get("/api/webhooks")
def list_webhooks(container: AppContainer = Depends(get_container)) -> dict:
    return {"webhooks": container.webhooks.list_hooks()}


@router.post("/api/webhooks")
def create_webhook(
    payload: CreateWebhookPayload,
    container: AppContainer = Depends(get_container),
) -> dict:
    brain = container.settings.load_public()["active_brain"]
    try:
        return container.webhooks.create_hook(
            name=payload.name,
            brain=brain,
            action=payload.action,
            prompt_template=payload.prompt_template,
            owner_chat=payload.owner_chat,
            mode=payload.mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/webhooks/{hook_id}")
def delete_webhook(hook_id: str, container: AppContainer = Depends(get_container)) -> dict:
    if not container.webhooks.delete_hook(hook_id):
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"ok": True}


@router.post("/hooks/{hook_id}")
async def trigger_webhook(
    hook_id: str,
    request: Request,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    token = (request.headers.get("x-webhook-token") or "").strip()
    if not token:
        auth_h = request.headers.get("authorization") or ""
        if auth_h.startswith("Bearer "):
            token = auth_h[7:].strip()

    hook = container.webhooks.verify_and_touch(hook_id, token)
    if hook is None:
        raise HTTPException(status_code=401, detail="Invalid webhook token or unknown webhook")

    try:
        raw_body = await request.body()
        body_text = raw_body.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body_text)
            payload_str = json.dumps(parsed, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            payload_str = body_text
    except Exception:  # noqa: BLE001
        payload_str = ""

    prompt = str(hook["prompt_template"]).replace("{{payload}}", payload_str[:8000])
    brain = str(hook["brain"])

    if hook["action"] == "kanban":
        task = container.kanban.create_task(
            brain=brain,
            title=f"Webhook: {hook['name']}",
            description=prompt,
            mode=str(hook["mode"]),
            owner_chat=str(hook["owner_chat"]),
        )
        return {"ok": True, "action": "kanban", "task_id": task.id}

    res = await container.turn_shell.handle_message(
        channel="webhook",
        peer_id=hook_id,
        text=prompt,
        run_mode=str(hook["mode"]),
    )
    await container.inbox.deliver(
        brain=brain,
        source="system",
        title=f"Webhook: {hook['name']}",
        body=str(res.get("reply") or ""),
        owner_chat=str(hook["owner_chat"]),
    )
    return {"ok": True, "action": "chat", "reply": res.get("reply", "")}
