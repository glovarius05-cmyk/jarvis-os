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


def extract_app_slug(item: Any) -> str:
    if not isinstance(item, dict):
        s = str(item or "").strip().lower()
    else:
        tk = item.get("toolkit")
        tk_slug = tk.get("slug") if isinstance(tk, dict) else (str(tk) if tk else "")
        ac = item.get("auth_config")
        ac_slug = ac.get("toolkit_slug") if isinstance(ac, dict) else ""
        raw = (
            tk_slug
            or item.get("appUniqueId")
            or item.get("appName")
            or item.get("app")
            or ac_slug
            or item.get("slug")
            or ""
        )
        s = str(raw).strip().lower()

    s_clean = s.replace("-", "").replace("_", "").replace(" ", "")
    if s_clean in ("googlecalendar", "calendar", "gcal"):
        return "googlecalendar"
    if s_clean in ("googlesheets", "sheets", "gsheets"):
        return "googlesheets"
    if s_clean in ("gmail", "googlemail"):
        return "gmail"
    if s_clean in ("twitter", "x"):
        return "twitter"
    return s_clean or s


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

    masked_key = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "***"
    accounts_by_app: dict[str, dict[str, Any]] = {}

    # 1. Try connect.composio.dev/mcp with both x-api-key and x-consumer-api-key
    mcp_headers_to_try = []
    if key.lower().startswith("ck"):
        mcp_headers_to_try.append({"x-consumer-api-key": key, "Content-Type": "application/json"})
        mcp_headers_to_try.append({"x-api-key": key, "Content-Type": "application/json"})
    else:
        mcp_headers_to_try.append({"x-api-key": key, "Content-Type": "application/json"})
        mcp_headers_to_try.append({"x-consumer-api-key": key, "Content-Type": "application/json"})

    mcp_succeeded = False
    mcp_tool_count = 0
    active_mcp_headers = None

    for m_hdrs in mcp_headers_to_try:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                mcp_res = await client.post(
                    "https://connect.composio.dev/mcp",
                    json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
                    headers=m_hdrs,
                )
                if mcp_res.status_code == 200:
                    mcp_succeeded = True
                    active_mcp_headers = m_hdrs
                    mcp_data = mcp_res.json()
                    tools = mcp_data.get("result", {}).get("tools", [])
                    mcp_tool_count = len(tools)
                    for t in tools:
                        tname = str(t.get("name") or "").lower().replace("-", "").replace("_", "")
                        if "gmail" in tname or "googlemail" in tname:
                            accounts_by_app["gmail"] = {"app": "gmail", "status": "ACTIVE", "id": "mcp_gmail"}
                        if "calendar" in tname:
                            accounts_by_app["googlecalendar"] = {"app": "googlecalendar", "status": "ACTIVE", "id": "mcp_googlecalendar"}
                        if "sheet" in tname:
                            accounts_by_app["googlesheets"] = {"app": "googlesheets", "status": "ACTIVE", "id": "mcp_googlesheets"}
                        if "github" in tname:
                            accounts_by_app["github"] = {"app": "github", "status": "ACTIVE", "id": "mcp_github"}
                        if "notion" in tname:
                            accounts_by_app["notion"] = {"app": "notion", "status": "ACTIVE", "id": "mcp_notion"}
                        if "twitter" in tname or "tweet" in tname or tname.startswith("x"):
                            accounts_by_app["twitter"] = {"app": "twitter", "status": "ACTIVE", "id": "mcp_twitter"}
                        if "linkedin" in tname:
                            accounts_by_app["linkedin"] = {"app": "linkedin", "status": "ACTIVE", "id": "mcp_linkedin"}
                        if "instagram" in tname:
                            accounts_by_app["instagram"] = {"app": "instagram", "status": "ACTIVE", "id": "mcp_instagram"}
                    break
        except Exception:
            continue

    if mcp_succeeded and active_mcp_headers:
        # Register or update Composio MCP connection in Jarvis
        try:
            from app.hub.mcp_client import MCPConnection
            conn = MCPConnection(
                id="composio",
                name="Composio MCP",
                transport="http",
                perm="full",
                enabled=True,
                url="https://connect.composio.dev/mcp",
                headers=active_mcp_headers,
                write_tools=["*_create_*", "*_update_*", "*_delete_*", "*_send_*"],
            )
            container.hub.connections.upsert(conn)
            container.hub.mcp_pool.invalidate("composio")
        except Exception:
            pass

    # 2. Try Composio toolkits call if registered
    try:
        active_brain = container.settings.load_public().get("active_brain") or "Brain Default"
        raw = await container.hub.call_tool(
            name="mcp__composio__COMPOSIO_MANAGE_CONNECTIONS",
            args={"toolkits": ["gmail", "googlecalendar", "googlesheets", "github", "notion", "twitter", "linkedin", "instagram"]},
            brain=active_brain,
            run_mode="full",
            for_model=False,
        )
        data = json.loads(raw)
        results = data.get("data", {}).get("results", {})
        for app_raw, app_info in results.items():
            if not isinstance(app_info, dict):
                continue
            normalized_app = extract_app_slug({"slug": app_raw})
            acc_list = app_info.get("accounts") or []
            status = str(app_info.get("status") or "disconnected").upper()
            if status == "ACTIVE" or any(str(a.get("status", "")).upper() == "ACTIVE" for a in acc_list):
                accounts_by_app[normalized_app] = {"app": normalized_app, "status": "ACTIVE", "id": normalized_app}
            elif acc_list:
                accounts_by_app[normalized_app] = {"app": normalized_app, "status": str(acc_list[0].get("status", "INITIATED")).upper(), "id": str(acc_list[0].get("id", normalized_app))}
    except Exception:
        pass

    # 3. Direct REST API to backend.composio.dev for connected_accounts
    header_variants = [
        {"x-api-key": key, "Accept": "application/json"},
        {"x-consumer-api-key": key, "Accept": "application/json"},
        {"x-org-api-key": key, "Accept": "application/json"},
        {"x-user-api-key": key, "Accept": "application/json"},
        {"Authorization": f"Bearer {key}", "Accept": "application/json"},
    ]
    urls_to_try = [
        f"{COMPOSIO_BASE}/connected_accounts",
        f"{COMPOSIO_BASE}/connected_accounts?account_type=ALL",
        "https://backend.composio.dev/api/v3/connected_accounts",
        "https://backend.composio.dev/api/v3/connected_accounts?account_type=ALL",
    ]
    rest_succeeded = False
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
                        rest_succeeded = True
                        data = res.json()
                        items = data.get("items") or data.get("connected_accounts") or data.get("data") or []
                        for it in items:
                            if not isinstance(it, dict):
                                continue
                            app_name = extract_app_slug(it)
                            if not app_name:
                                continue
                            status = str(it.get("status") or "ACTIVE").upper()
                            account_id = str(it.get("id") or it.get("nanoid") or it.get("accountId") or "")
                            accounts_by_app[app_name] = {"app": app_name, "status": status, "id": account_id}
                        break
            except Exception:
                continue
        if rest_succeeded:
            break

    if mcp_succeeded or rest_succeeded:
        accounts = list(accounts_by_app.values())
        msg = f"{len(accounts)} accounts connected" if accounts else (
            f"Composio MCP active ({mcp_tool_count} tools). Click Connect to link accounts."
            if mcp_succeeded else "Key valid. No accounts connected yet."
        )
        return {
            "configured": True,
            "valid": True,
            "masked_key": masked_key,
            "accounts": accounts,
            "message": msg,
        }

    # If neither MCP nor REST succeeded
    if last_status in {401, 403}:
        detail_msg = ""
        try:
            err_json = json.loads(last_text)
            detail_msg = err_json.get("error", {}).get("message") or err_json.get("message") or ""
        except Exception:
            pass
        msg = f"Composio rejected key ({masked_key}): {detail_msg}" if detail_msg else f"Invalid API key ({masked_key}). Please verify your key in app.composio.dev/settings"
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

    raw_app = payload.app.strip().lower()
    toolkit_slug = extract_app_slug(raw_app) or raw_app
    redirect_url = payload.redirect_url.strip() or "http://127.0.0.1:7777/hub"

    import json
    import re
    import httpx

    # Direct fallback URL in Composio Dashboard for this app
    dashboard_url = f"https://dashboard.composio.dev/~/org/connect/apps/{toolkit_slug}?open=true"

    if toolkit_slug in ("twitter", "x"):
        return {"ok": True, "app": "twitter", "auth_url": dashboard_url}

    # 1. Try Composio MCP connection tool if available
    try:
        active_brain = container.settings.load_public().get("active_brain") or "Brain Default"
        raw = await container.hub.call_tool(
            name="mcp__composio__COMPOSIO_MANAGE_CONNECTIONS",
            args={"toolkits": [toolkit_slug]},
            brain=active_brain,
            run_mode="full",
            for_model=False,
        )
        data = json.loads(raw)
        res_info = data.get("data", {}).get("results", {}).get(toolkit_slug, {})
        auth_url = res_info.get("redirect_url") or ""
        if not auth_url and "dashboard.composio.dev" in res_info.get("error_message", ""):
            m = re.search(r"https://dashboard\.composio\.dev/[^\s\)\>]+", res_info["error_message"])
            if m:
                auth_url = m.group(0)
        if auth_url:
            return {"ok": True, "app": toolkit_slug, "auth_url": auth_url, "raw": res_info}
    except Exception:
        pass

    # 2. Try REST API: Query auth_configs for this toolkit, then create link
    header_variants = [
        {"x-api-key": key, "Content-Type": "application/json"},
        {"x-consumer-api-key": key, "Content-Type": "application/json"},
        {"x-org-api-key": key, "Content-Type": "application/json"},
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    ]

    for headers in header_variants:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # A. Fetch auth_configs for toolkit
                cfg_res = await client.get(
                    f"{COMPOSIO_BASE}/auth_configs?toolkit_slug={toolkit_slug}",
                    headers=headers,
                )
                if cfg_res.status_code == 200:
                    cfg_items = cfg_res.json().get("items") or []
                    auth_config_id = None
                    for ci in cfg_items:
                        if ci.get("status") in ("ENABLED", "ACTIVE") or ci.get("is_composio_managed"):
                            auth_config_id = ci.get("id")
                            break
                    if not auth_config_id and cfg_items:
                        auth_config_id = cfg_items[0].get("id")

                    if auth_config_id:
                        # B. Create connect link
                        link_res = await client.post(
                            f"{COMPOSIO_BASE}/connected_accounts/link",
                            headers=headers,
                            json={
                                "user_id": "default",
                                "auth_config_id": auth_config_id,
                                "callback_url": redirect_url,
                            },
                        )
                        if link_res.status_code in (200, 201):
                            link_data = link_res.json()
                            auth_url = (
                                link_data.get("redirect_url")
                                or link_data.get("redirectUrl")
                                or link_data.get("connectionUrl")
                                or link_data.get("authUrl")
                                or link_data.get("url")
                            )
                            if auth_url:
                                return {"ok": True, "app": toolkit_slug, "auth_url": auth_url, "raw": link_data}

                # C. Try direct link with auth_config_id = toolkit_slug (some versions accept slug)
                link_direct = await client.post(
                    f"{COMPOSIO_BASE}/connected_accounts/link",
                    headers=headers,
                    json={
                        "user_id": "default",
                        "auth_config_id": toolkit_slug,
                        "callback_url": redirect_url,
                    },
                )
                if link_direct.status_code in (200, 201):
                    link_data = link_direct.json()
                    auth_url = (
                        link_data.get("redirect_url")
                        or link_data.get("redirectUrl")
                        or link_data.get("connectionUrl")
                        or link_data.get("authUrl")
                        or link_data.get("url")
                    )
                    if auth_url:
                        return {"ok": True, "app": toolkit_slug, "auth_url": auth_url, "raw": link_data}
        except Exception:
            continue

    # 3. Graceful fallback: return the official Composio connect page for the app
    # This ensures clicking "Connect" always opens the app's authorization page in Composio!
    return {
        "ok": True,
        "app": toolkit_slug,
        "auth_url": dashboard_url,
        "message": f"Redirecting to Composio to connect {toolkit_slug}",
    }

