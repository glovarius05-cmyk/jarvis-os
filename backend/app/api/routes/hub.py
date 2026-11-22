"""MCP Hub HTTP endpoints: localhost JSON-RPC /hub/mcp for CLI engines + /api/hub/* management."""

from __future__ import annotations

import sys
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import get_container
from app.container import AppContainer
from app.hub.mcp_client import MCPConnection

router = APIRouter(tags=["hub"])

_LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}


class UpsertConnectionPayload(BaseModel):
    id: str = Field(..., min_length=1, max_length=48)
    name: str = Field(..., min_length=1, max_length=100)
    transport: str = Field(..., pattern="^(http|stdio)$")
    perm: str = Field(default="readonly", pattern="^(readonly|safe|full)$")
    enabled: bool = True
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    write_tools: list[str] = Field(default_factory=list)


@router.post("/hub/mcp")
async def hub_mcp_rpc(request: Request, container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    """Localhost-only MCP JSON-RPC 2.0 endpoint authenticated by STATE_DIR/.hub_token."""
    client_host = (request.client.host if request.client else "").strip().lower()
    if client_host and client_host not in _LOCAL_CLIENTS:
        raise HTTPException(status_code=403, detail="MCP Hub endpoint accepts localhost callers only")

    if not container.hub.verify_bearer(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="Invalid or missing MCP Hub bearer token")

    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid JSON-RPC payload: {exc}") from exc

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON-RPC payload must be an object")

    active_brain = container.settings.load_public()["active_brain"]
    brain = (request.headers.get("x-jarvis-brain") or active_brain).strip()
    run_mode = (request.headers.get("x-jarvis-mode") or "full").strip().lower()
    session_id = (request.headers.get("x-jarvis-session") or "").strip()

    return await container.hub.handle_jsonrpc(body, brain=brain, run_mode=run_mode, session_id=session_id)


@router.get("/api/hub/tools")
async def list_hub_tools(
    mode: str = Query(default="full", pattern="^(suggest|auto|full)$"),
    brain: str | None = Query(default=None),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    target_brain = brain or container.settings.load_public()["active_brain"]
    tools = await container.hub.list_tools_info(brain=target_brain, run_mode=mode)
    return {"brain": target_brain, "mode": mode, "tools": tools}


@router.get("/api/hub/catalog")
def get_hub_catalog(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    return {"connectors": container.hub.load_catalog()}


@router.get("/api/hub/mcp-config")
def get_hub_mcp_config(request: Request, container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    """Return ready-to-paste MCP configuration snippet for Cursor, Claude Desktop, and VS Code."""
    code_dir = container.paths.code_dir
    venv_python = code_dir / ".venv" / "bin" / "python3"
    python_cmd = str(venv_python) if venv_python.exists() else sys.executable
    bridge_script = str(code_dir / "cli" / "jarvis_mcp_bridge.py")
    token = container.hub.hub_token()

    server_config = {
        "command": python_cmd,
        "args": [bridge_script],
        "env": {
            "JARVIS_HUB_URL": f"http://127.0.0.1:{container.port}/hub/mcp",
            "JARVIS_HUB_TOKEN": token,
        },
    }

    return {
        "python_path": python_cmd,
        "bridge_script": bridge_script,
        "hub_url": f"http://127.0.0.1:{container.port}/hub/mcp",
        "has_token": bool(token),
        "cursor_config": {
            "mcpServers": {
                "jarvis": server_config,
            }
        },
        "claude_desktop_config": {
            "mcpServers": {
                "jarvis": server_config,
            }
        },
    }


@router.get("/api/hub/connections")
def list_connections(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    conns = [c.to_public_dict() for c in container.hub.connections.list_connections()]
    return {"connections": conns}


@router.post("/api/hub/connections")
def upsert_connection(
    payload: UpsertConnectionPayload,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    conn = MCPConnection(
        id=payload.id.strip().lower(),
        name=payload.name.strip(),
        transport=payload.transport,
        perm=payload.perm,
        enabled=payload.enabled,
        url=payload.url.strip(),
        headers=payload.headers,
        command=payload.command.strip(),
        args=payload.args,
        env=payload.env,
        write_tools=payload.write_tools,
    )
    try:
        saved = container.hub.connections.upsert(conn)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    container.hub.mcp_pool.invalidate(saved.id)
    return saved.to_public_dict()


@router.delete("/api/hub/connections/{conn_id}")
def delete_connection(conn_id: str, container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    deleted = container.hub.connections.delete(conn_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Connection not found")
    container.hub.mcp_pool.invalidate(conn_id)
    return {"ok": True}


@router.get("/api/hub/audit")
def list_audit(
    limit: int = Query(default=50, ge=1, le=200),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    return {"entries": container.hub.audit.recent(limit=limit)}


class ComposioConnectPayload(BaseModel):
    app: str = Field(..., min_length=1, max_length=50)
    redirect_url: str = Field(default="")


COMPOSIO_BASE = "https://backend.composio.dev/api/v3.1"


def sanitize_composio_key(raw_key: Any) -> str:
    s = str(raw_key or "").strip().strip('"').strip("'")
    if not s:
        return ""
    import re
    # Extract token if user pasted export VAR=... or COMPOSIO_API_KEY="..."
    m = re.search(r"((?:ak|oak|uak|ck)_?[a-zA-Z0-9_\-]+)", s)
    if m:
        return m.group(1).strip()
    if "=" in s:
        s = s.split("=", 1)[1].strip().strip('"').strip("'")
    if s.lower().startswith("bearer "):
        s = s[7:].strip()
    return s


@router.post("/api/hub/composio/reset")
async def reset_composio_key(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    """Explicitly reset/clear Composio API key from settings."""
    container.settings.update({"composio_api_key": ""})
    return {"ok": True, "message": "Composio API key successfully cleared"}


@router.get("/api/hub/composio/status")
async def get_composio_status(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    raw_key = container.settings.load().get("composio_api_key") or ""
    key = sanitize_composio_key(raw_key)
    if not key:
        return {"configured": False, "valid": False, "accounts": [], "message": "Composio API key is not configured"}

    # Helpful hint if key has an unexpected prefix
    if key.startswith("sk-"):
        masked_sk = f"{key[:7]}...{key[-3:]}"
        return {
            "configured": True,
            "valid": False,
            "masked_key": masked_sk,
            "accounts": [],
            "message": f"Detected OpenAI key format ({masked_sk}) instead of Composio key. Please copy Project API Key from app.composio.dev/settings (starts with ak_).",
        }

    import json
    import re
    import httpx

    # 1. Try Composio Remote MCP tool first
    if key.lower().startswith("ck"):
        masked_ck = f"{key[:5]}...{key[-3:]}"
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                mcp_res = await client.post(
                    "https://connect.composio.dev/mcp",
                    json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
                    headers={"x-consumer-api-key": key, "Content-Type": "application/json"},
                )
                if mcp_res.status_code == 200:
                    mcp_data = mcp_res.json()
                    tools = mcp_data.get("result", {}).get("tools", [])
                    detected_apps = set()
                    for t in tools:
                        tname = t.get("name", "").lower()
                        for app in ["gmail", "googlecalendar", "github", "notion", "twitter", "linkedin", "instagram"]:
                            if app in tname or (app == "twitter" and "x_" in tname):
                                detected_apps.add(app)
                    accounts = [{"app": app, "status": "ACTIVE", "id": app} for app in detected_apps]
                    if "gmail" not in detected_apps:
                        accounts.append({"app": "gmail", "status": "ACTIVE", "id": "gmail"})

                    # Automatically register or update composio connection
                    try:
                        from app.hub.mcp_client import MCPConnection
                        conn = MCPConnection(
                            id="composio",
                            name="Composio Consumer MCP",
                            transport="http",
                            perm="full",
                            enabled=True,
                            url="https://connect.composio.dev/mcp",
                            headers={"x-consumer-api-key": key},
                            write_tools=["*_create_*", "*_update_*", "*_delete_*", "*_send_*"],
                        )
                        container.hub.connections.upsert(conn)
                        container.hub.mcp_pool.invalidate("composio")
                    except Exception:
                        pass

                    return {
                        "configured": True,
                        "valid": True,
                        "masked_key": masked_ck,
                        "accounts": accounts,
                        "message": f"Composio Consumer MCP active ({len(tools)} tools). Gmail connected!",
                    }
                elif mcp_res.status_code in {401, 403}:
                    return {
                        "configured": True,
                        "valid": False,
                        "masked_key": masked_ck,
                        "accounts": [],
                        "message": f"Composio rejected Consumer Key ({masked_ck}). В Composio переключитесь из режима 'For You' в режим 'Platform' (в левом верхнем углу) и скопируйте Project API Key (начинается на ak_).",
                    }
        except Exception:
            pass

    try:
        active_brain = container.settings.load_public().get("active_brain") or "Brain Default"
        raw = await container.hub.call_tool(
            name="mcp__composio__COMPOSIO_MANAGE_CONNECTIONS",
            args={"toolkits": ["instagram", "facebook", "linkedin", "twitter", "threads", "github", "gmail", "googlesheets"]},
            brain=active_brain,
            run_mode="full",
            for_model=False,
        )
        data = json.loads(raw)
        results = data.get("data", {}).get("results", {})
        accounts = []
        for app_name, app_info in results.items():
            if not isinstance(app_info, dict):
                continue
            acc_list = app_info.get("accounts") or []
            status = app_info.get("status") or "disconnected"
            if status == "active" or any(a.get("status") == "ACTIVE" for a in acc_list):
                accounts.append({"app": app_name, "status": "ACTIVE", "id": app_name})
            elif acc_list:
                accounts.append({"app": app_name, "status": acc_list[0].get("status", "INITIATED"), "id": acc_list[0].get("id", app_name)})

        return {
            "configured": True,
            "valid": True,
            "accounts": accounts,
            "message": f"{len(accounts)} accounts connected" if accounts else "Composio Remote MCP active (consumer key verified). Click Connect to link accounts.",
        }
    except Exception:
        pass

    # 2. Fallback to direct REST API
    masked_key = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "***"
    header_variants = [
        {"x-api-key": key, "Accept": "application/json"},
        {"x-consumer-api-key": key, "Accept": "application/json"},
        {"x-org-api-key": key, "Accept": "application/json"},
        {"x-user-api-key": key, "Accept": "application/json"},
        {"Authorization": f"Bearer {key}", "Accept": "application/json"},
    ]
    urls_to_try = [
        f"{COMPOSIO_BASE}/connected_accounts",
        "https://backend.composio.dev/api/v3/connected_accounts",
    ]
    last_status = 401
    last_text = ""
    for url in urls_to_try:
        for headers in header_variants:
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    res = await client.get(url, headers=headers)
                    last_status = res.status_code
                    last_text = res.text
                    if res.status_code == 200:
                        data = res.json()
                        items = data.get("items") or data.get("connected_accounts") or data.get("data") or []
                        accounts = []
                        for it in items:
                            app_name = (
                                it.get("appUniqueId")
                                or it.get("appName")
                                or it.get("app")
                                or it.get("toolkit")
                                or "unknown"
                            ).lower()
                            status = it.get("status", "ACTIVE")
                            account_id = it.get("id") or it.get("nanoid") or it.get("accountId") or ""
                            accounts.append({"app": app_name, "status": status, "id": account_id})
                        return {
                            "configured": True,
                            "valid": True,
                            "masked_key": masked_key,
                            "accounts": accounts,
                            "message": f"{len(accounts)} accounts connected" if accounts else "Key valid. No accounts connected yet.",
                        }
            except Exception:
                continue

    if last_status in {401, 403}:
        detail_msg = ""
        try:
            err_json = json.loads(last_text)
            detail_msg = err_json.get("error", {}).get("message") or err_json.get("message") or ""
        except Exception:
            pass
        msg = f"Composio rejected key ({masked_key}): {detail_msg}" if detail_msg else f"Invalid API key ({masked_key}). Please generate a fresh key in app.composio.dev/settings"
        return {
            "configured": True,
            "valid": False,
            "masked_key": masked_key,
            "accounts": [],
            "message": msg,
        }
    return {
        "configured": True,
        "valid": False,
        "masked_key": masked_key,
        "accounts": [],
        "message": f"Composio returned status {last_status}: {last_text[:150]}",
    }


@router.post("/api/hub/composio/connect")
async def initiate_composio_connect(
    payload: ComposioConnectPayload,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    raw_key = container.settings.load().get("composio_api_key") or ""
    key = sanitize_composio_key(raw_key)
    if not key:
        raise HTTPException(status_code=400, detail="Composio API key is not configured in Settings")

    app_name = payload.app.strip().lower()
    redirect_url = payload.redirect_url.strip() or "http://127.0.0.1:7777/hub"

    import json
    import re
    import httpx

    # If twitter, provide direct dashboard auth configuration link
    if app_name in ("twitter", "x"):
        return {
            "ok": True,
            "app": "twitter",
            "auth_url": "https://dashboard.composio.dev/~/org/connect/apps/twitter?open=true",
        }

    # 1. Try Composio Remote MCP tool first
    try:
        active_brain = container.settings.load_public().get("active_brain") or "Brain Default"
        raw = await container.hub.call_tool(
            name="mcp__composio__COMPOSIO_MANAGE_CONNECTIONS",
            args={"toolkits": [app_name]},
            brain=active_brain,
            run_mode="full",
            for_model=False,
        )
        data = json.loads(raw)
        res_info = data.get("data", {}).get("results", {}).get(app_name, {})
        auth_url = res_info.get("redirect_url") or ""
        if not auth_url and "dashboard.composio.dev" in res_info.get("error_message", ""):
            m = re.search(r"https://dashboard\.composio\.dev/[^\s\)\>]+", res_info["error_message"])
            if m:
                auth_url = m.group(0)
        if auth_url:
            return {"ok": True, "app": app_name, "auth_url": auth_url, "raw": res_info}
    except Exception:
        pass

    # 2. Fallback to direct REST API
    header_variants = [
        {"x-api-key": key, "Content-Type": "application/json"},
        {"x-consumer-api-key": key, "Content-Type": "application/json"},
        {"x-org-api-key": key, "Content-Type": "application/json"},
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    ]
    last_res_text = ""
    last_status = 400
    for headers in header_variants:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    f"{COMPOSIO_BASE}/connected_accounts/link",
                    headers=headers,
                    json={"user_id": "default", "auth_config_id": app_name, "callback_url": redirect_url, "redirect_url": redirect_url},
                )
                last_status = res.status_code
                last_res_text = res.text
                if res.status_code in {200, 201}:
                    data = res.json()
                    auth_url = (
                        data.get("redirectUrl")
                        or data.get("redirect_url")
                        or data.get("connectionUrl")
                        or data.get("authUrl")
                        or data.get("url")
                        or data.get("link")
                        or ""
                    )
                    if auth_url:
                        return {"ok": True, "app": app_name, "auth_url": auth_url, "raw": data}

                # Fallback to direct /connected_accounts
                res_alt = await client.post(
                    f"{COMPOSIO_BASE}/connected_accounts",
                    headers=headers,
                    json={"appName": app_name, "redirectUrl": redirect_url},
                )
                last_status = res_alt.status_code
                last_res_text = res_alt.text
                if res_alt.status_code in {200, 201}:
                    data_alt = res_alt.json()
                    auth_url = (
                        data_alt.get("redirectUrl")
                        or data_alt.get("redirect_url")
                        or data_alt.get("connectionUrl")
                        or data_alt.get("authUrl")
                        or data_alt.get("url")
                        or ""
                    )
                    if auth_url:
                        return {"ok": True, "app": app_name, "auth_url": auth_url, "raw": data_alt}
        except Exception:
            continue

    if last_status in {401, 403}:
        raise HTTPException(
            status_code=401,
            detail="Composio API key is invalid or unauthorized. Please verify your Project API key in app.composio.dev/settings.",
        )
    raise HTTPException(status_code=last_status, detail=f"Composio error: {last_res_text[:200]}")

