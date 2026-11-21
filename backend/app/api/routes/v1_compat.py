"""OpenAI-compatible /v1 API for Cursor, Continue, LibreChat, and external LLM clients.

Exposes standard endpoints:
- GET /v1/models
- POST /v1/chat/completions (streaming SSE and non-streaming)

Allows any external tool to interact with Jarvis OS, utilizing its active Second Brain,
full tool orchestration, hybrid search, and multi-model routing.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from uuid import uuid4
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app import APP_VERSION
from app.api.deps import get_container
from app.engines.base import EngineEvent, EngineRequest
from app.security.auth import SESSION_COOKIE_NAME

router = APIRouter(prefix="/v1", tags=["OpenAI Compatible API"])

PERSONA_MODELS = [
    {"id": "jarvis-os", "name": "Jarvis OS (Universal)", "persona": "default"},
    {"id": "jarvis-architect", "name": "Chief Architect & Security Auditor", "persona": "architect"},
    {"id": "jarvis-researcher", "name": "Deep Research Analyst", "persona": "researcher"},
    {"id": "jarvis-writer", "name": "Content & Strategy Director", "persona": "writer"},
    {"id": "jarvis-executive", "name": "Executive Chief of Staff", "persona": "executive"},
]


def _check_v1_auth(request: Request, ctr: Any) -> None:
    """Verify Bearer token, x-api-key, or session cookie if login is required."""
    if not ctr.auth.login_required():
        return

    auth_header = request.headers.get("authorization", "")
    token = ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    elif "x-api-key" in request.headers:
        token = request.headers["x-api-key"].strip()

    cookie_token = request.cookies.get(SESSION_COOKIE_NAME, "")

    if token:
        if hmac.compare_digest(token, ctr.hub.hub_token()):
            return
        if ctr.auth.validate_session(token):
            return
        if ctr.auth.verify_admin_password(token):
            return

    if cookie_token and ctr.auth.validate_session(cookie_token):
        return

    raise HTTPException(
        status_code=401,
        detail="Unauthorized: provide Bearer token (Hub Token or Admin Password) or active session cookie.",
    )


@router.get("/models")
async def list_v1_models(request: Request) -> dict[str, Any]:
    """Return available models in standard OpenAI format."""
    ctr = get_container(request)
    _check_v1_auth(request, ctr)

    now = int(time.time())
    data = []

    for pm in PERSONA_MODELS:
        data.append(
            {
                "id": pm["id"],
                "object": "model",
                "created": now,
                "owned_by": "jarvis-os",
                "permission": [],
                "root": pm["id"],
                "parent": None,
            }
        )

    # Also list configured provider targets
    settings = ctr.settings.load()
    openrouter_models = [
        "openrouter/auto",
        "anthropic/claude-3.5-sonnet",
        "openai/gpt-4o",
        "google/gemini-2.0-flash",
    ]
    for om in openrouter_models:
        data.append(
            {
                "id": om,
                "object": "model",
                "created": now,
                "owned_by": "openrouter",
                "permission": [],
                "root": om,
                "parent": None,
            }
        )

    return {"object": "list", "data": data}


@router.post("/chat/completions")
async def create_v1_chat_completion(request: Request) -> Any:
    """Execute chat completion with full Jarvis agent capabilities in OpenAI format."""
    ctr = get_container(request)
    _check_v1_auth(request, ctr)

    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}") from exc

    raw_model = str(body.get("model") or "jarvis-os").strip()
    messages = body.get("messages") or []
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="'messages' must be a non-empty list.")

    stream = bool(body.get("stream", False))
    brain = request.headers.get("x-brain") or request.query_params.get("brain") or "Brain Default"

    # Resolve target persona if requested
    matched_persona = None
    for pm in PERSONA_MODELS:
        if raw_model.lower() in {pm["id"].lower(), pm["persona"].lower()}:
            matched_persona = pm["persona"]
            break

    # Determine provider and model override
    provider_override = ""
    model_override = ""
    if "/" in raw_model and not matched_persona:
        if raw_model.startswith("gemini-"):
            provider_override = "gemini-api"
            model_override = raw_model
        else:
            provider_override = "openrouter"
            model_override = raw_model

    resolved_provider, resolved_model = ctr.engine_router.resolve_target(
        override_provider=provider_override,
        override_model=model_override,
        role="main",
    )

    system_prompt = ctr.prompt_builder.build(
        brain=brain,
        provider=resolved_provider,
        model=resolved_model,
        channel="v1-api",
    )

    # If client passed custom system messages, append them to instructions
    user_system_prompts = [
        str(m.get("content") or "") for m in messages if m.get("role") == "system" and m.get("content")
    ]
    if user_system_prompts:
        system_prompt += "\n\n# Client Custom Instructions:\n" + "\n".join(user_system_prompts)

    conversation_messages = [
        {"role": m.get("role", "user"), "content": str(m.get("content") or "")}
        for m in messages
        if m.get("role") in {"user", "assistant", "tool"}
    ]

    tools = await ctr.hub.discover_schemas(brain=brain, run_mode="full") if ctr.hub else []
    tool_executor = ctr.hub.make_executor(brain=brain, run_mode="full") if ctr.hub else None

    engine = ctr.engine_router.build_engine(
        resolved_provider,
        brain=brain,
        run_mode="full",
        model=resolved_model,
    )

    completion_id = f"chatcmpl-{uuid4().hex[:12]}"
    created_ts = int(time.time())

    cancel_event = asyncio.Event()
    req = EngineRequest(
        session_id=f"v1-{uuid4().hex[:8]}",
        brain=brain,
        system_prompt=system_prompt,
        messages=conversation_messages,
        provider=resolved_provider,
        model=resolved_model,
        tools=tools,
        tool_executor=tool_executor,
        cancel_event=cancel_event,
    )

    if stream:
        async def event_generator():
            # Initial role chunk
            first_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": raw_model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(first_chunk, ensure_ascii=False)}\n\n"

            async for ev in engine.stream(req):
                if ev.type == "text" and ev.content:
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created_ts,
                        "model": raw_model,
                        "choices": [{"index": 0, "delta": {"content": ev.content}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                elif ev.type == "error":
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created_ts,
                        "model": raw_model,
                        "choices": [
                            {"index": 0, "delta": {"content": f"\n\n[ERROR: {ev.content}]"}, "finish_reason": "error"}
                        ],
                    }
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                    break

            # Finish chunk
            final_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": raw_model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    # Non-streaming mode
    accumulated_content = []
    final_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    async for ev in engine.stream(req):
        if ev.type == "text" and ev.content:
            accumulated_content.append(ev.content)
        elif ev.type == "final":
            if ev.content:
                accumulated_content = [ev.content]
            if ev.usage:
                final_usage["prompt_tokens"] = int(ev.usage.get("input_tokens") or 0)
                final_usage["completion_tokens"] = int(ev.usage.get("output_tokens") or 0)
                final_usage["total_tokens"] = final_usage["prompt_tokens"] + final_usage["completion_tokens"]

    return JSONResponse(
        {
            "id": completion_id,
            "object": "chat.completion",
            "created": created_ts,
            "model": raw_model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "".join(accumulated_content),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": final_usage,
        }
    )
