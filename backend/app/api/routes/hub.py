"""MCP Hub HTTP endpoints: localhost JSON-RPC /hub/mcp for CLI engines + /api/hub/* management."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sys
import time
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
import httpx
from pydantic import BaseModel, Field

from app.api.deps import get_container
from app.container import AppContainer
from app.hub.mcp_client import MCPConnection

router = APIRouter(tags=["hub"])

_LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}
_AGENSI_OAUTH_PENDING: dict[str, dict[str, Any]] = {}


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
    api_key: str = ""
    header_name: str = "Authorization"
    env_var: str = ""


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
    headers = dict(payload.headers)
    env = dict(payload.env)
    if payload.api_key:
        if payload.transport == "http":
            hdr = payload.header_name or "Authorization"
            if hdr.lower() == "authorization" and not payload.api_key.lower().startswith("bearer "):
                headers[hdr] = f"Bearer {payload.api_key}"
            else:
                headers[hdr] = payload.api_key
        elif payload.transport == "stdio":
            e_var = payload.env_var or f"{payload.id.upper().replace('-', '_')}_API_KEY"
            env[e_var] = payload.api_key

    conn = MCPConnection(
        id=payload.id.strip().lower(),
        name=payload.name.strip(),
        transport=payload.transport,
        perm=payload.perm,
        enabled=payload.enabled,
        url=payload.url.strip(),
        headers=headers,
        command=payload.command.strip(),
        args=payload.args,
        env=env,
        write_tools=payload.write_tools,
    )
    try:
        saved = container.hub.connections.upsert(conn)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    container.hub.mcp_pool.invalidate(saved.id)
    return saved.to_public_dict()


@router.post("/api/hub/connections/test")
async def test_connection_endpoint(
    payload: UpsertConnectionPayload,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    headers = dict(payload.headers)
    env = dict(payload.env)
    if payload.api_key:
        if payload.transport == "http":
            hdr = payload.header_name or "Authorization"
            if hdr.lower() == "authorization" and not payload.api_key.lower().startswith("bearer "):
                headers[hdr] = f"Bearer {payload.api_key}"
            else:
                headers[hdr] = payload.api_key
        elif payload.transport == "stdio":
            e_var = payload.env_var or f"{payload.id.upper().replace('-', '_')}_API_KEY"
            env[e_var] = payload.api_key

    conn = MCPConnection(
        id=payload.id.strip().lower(),
        name=payload.name.strip(),
        transport=payload.transport,
        perm=payload.perm,
        enabled=payload.enabled,
        url=payload.url.strip(),
        headers=headers,
        command=payload.command.strip(),
        args=payload.args,
        env=env,
        write_tools=payload.write_tools,
    )
    try:
        tools = await container.hub.mcp_pool.test_connection(conn)
        return {
            "success": True,
            "count": len(tools),
            "tools": [
                {"name": t.get("name"), "description": t.get("description", "")}
                for t in tools
            ],
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


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
        tk_slug = ""
        if isinstance(tk, dict):
            tk_slug = str(tk.get("slug") or tk.get("toolkit_slug") or tk.get("name") or tk.get("id") or "")
        elif tk:
            tk_slug = str(tk)

        ac = item.get("auth_config") or item.get("authConfig")
        ac_slug = ""
        if isinstance(ac, dict):
            ac_tk = ac.get("toolkit")
            ac_tk_slug = ac_tk.get("slug") if isinstance(ac_tk, dict) else ""
            ac_slug = str(ac.get("toolkit_slug") or ac.get("slug") or ac_tk_slug or "")
        elif ac:
            ac_slug = str(ac)

        raw = (
            tk_slug
            or ac_slug
            or item.get("app_name")
            or item.get("appName")
            or item.get("app")
            or item.get("toolkit_slug")
            or item.get("toolkitSlug")
            or item.get("app_unique_id")
            or item.get("appUniqueId")
            or item.get("slug")
            or item.get("name")
            or item.get("integration_id")
            or item.get("integrationId")
            or ""
        )
        s = str(raw).strip().lower()

    s_clean = s.replace("-", "").replace("_", "").replace(" ", "")
    if any(k in s_clean for k in ("calendar", "gcal", "googlecalendar")):
        return "googlecalendar"
    if any(k in s_clean for k in ("sheet", "gsheet", "googlesheet")):
        return "googlesheets"
    if any(k in s_clean for k in ("gmail", "googlemail")):
        return "gmail"
    if any(k in s_clean for k in ("twitter", "tweet")) or s_clean == "x":
        return "twitter"
    if "github" in s_clean:
        return "github"
    if "notion" in s_clean:
        return "notion"
    if "linkedin" in s_clean:
        return "linkedin"
    if "instagram" in s_clean:
        return "instagram"
    if "facebook" in s_clean:
        return "facebook"
    if "youtube" in s_clean:
        return "youtube"
    if "reddit" in s_clean:
        return "reddit"
    if "slack" in s_clean:
        return "slack"
    return s_clean or s


def extract_email_from_obj(it: Any) -> str:
    """Extract email address from connected account dictionary or structure."""
    if not isinstance(it, dict):
        return ""
    for k in ("email", "user_email", "account_email", "emailAddress", "email_address", "email_id", "label", "user_uuid", "login"):
        v = it.get(k)
        if isinstance(v, str) and "@" in v:
            return v.strip()
    for k in ("alias", "account_identifier", "identifier", "name", "username"):
        v = it.get(k)
        if isinstance(v, str) and "@" in v:
            return v.strip()
    for parent in ("state", "params", "data", "metadata", "profile", "auth_config", "connection_data", "custom_fields"):
        sub = it.get(parent)
        if isinstance(sub, dict):
            val = sub.get("val") if isinstance(sub.get("val"), dict) else sub
            for k in ("email", "user_email", "account_email", "emailAddress", "email_address", "identifier", "label", "user"):
                v = val.get(k)
                if isinstance(v, str) and "@" in v:
                    return v.strip()
                if isinstance(v, dict) and isinstance(v.get("email"), str) and "@" in str(v.get("email")):
                    return str(v["email"]).strip()
    try:
        raw_dump = json.dumps(it)
        import re
        matches = re.findall(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', raw_dump)
        for m in matches:
            low = m.lower()
            if not any(bad in low for bad in ("composio.dev", "example.com", "schema.org", "sentry.io", "w3.org")):
                return m
    except Exception:
        pass
    return ""


def parse_composio_json(raw: str) -> dict[str, Any]:
    """Robustly parse Composio response, stripping markdown code blocks if present."""
    if not raw or not isinstance(raw, str):
        return {}
    cleaned = raw.strip()
    import re
    if "```" in cleaned:
        m = re.search(r'```(?:json)?\s*(\{.*\}|\[.*\])\s*```', cleaned, re.DOTALL)
        if m:
            cleaned = m.group(1).strip()
    try:
        res = json.loads(cleaned)
        if isinstance(res, dict):
            return res
        if isinstance(res, list):
            return {"items": res}
    except Exception:
        m = re.search(r'(\{.*\}|\[.*\])', cleaned, re.DOTALL)
        if m:
            try:
                res = json.loads(m.group(1))
                if isinstance(res, dict):
                    return res
                if isinstance(res, list):
                    return {"items": res}
            except Exception:
                pass
    return {}


def parse_mcp_rpc_response(resp: Any) -> dict[str, Any]:
    """Decode JSON-RPC response from either plain JSON or SSE (`data: ...`) HTTP MCP response."""
    try:
        text = (getattr(resp, "text", "") or "").strip()
        if "data: " in text:
            for line in text.splitlines():
                if line.startswith("data: "):
                    candidate = line[6:].strip()
                    if candidate.startswith("{"):
                        data = json.loads(candidate)
                        if isinstance(data, dict):
                            return data
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def extract_user_identifier(it: Any) -> str:
    """Extract username, email, or account alias from account dictionary."""
    em = extract_email_from_obj(it)
    if em:
        return em
    if not isinstance(it, dict):
        return ""
    for k in ("login", "username", "alias", "account_identifier", "identifier", "name"):
        v = it.get(k)
        if isinstance(v, str) and v.strip() and not any(bad in v.lower() for bad in ("composio", "default", "none")):
            return v.strip()
    for parent in ("state", "params", "data", "metadata", "profile", "auth_config"):
        sub = it.get(parent)
        if isinstance(sub, dict):
            for k in ("login", "username", "alias", "identifier", "name"):
                v = sub.get(k)
                if isinstance(v, str) and v.strip() and not any(bad in v.lower() for bad in ("composio", "default", "none")):
                    return v.strip()
    return ""


def _persist_found_email(container: AppContainer, app_slug: str, email: str) -> None:
    """Helper to store discovered account email or username in settings."""
    clean = (email or "").strip()
    if not clean:
        return
    if app_slug in ("gmail", "googlemail") and "@" not in clean:
        return
    try:
        cfg = container.settings.load()
        cur_emails = dict(cfg.get("composio_account_emails") or {})
        if cur_emails.get(app_slug) != clean:
            cur_emails[app_slug] = clean
            container.settings.update({"composio_account_emails": cur_emails})
    except Exception:
        pass


def _persist_found_account_id(container: AppContainer, app_slug: str, account_id: str) -> None:
    """Helper to store discovered Composio connected account ID (ca_...) in settings."""
    clean = (account_id or "").strip()
    if not clean or not clean.startswith("ca_"):
        return
    try:
        cfg = container.settings.load()
        cur_ids = dict(cfg.get("composio_account_ids") or {})
        if cur_ids.get(app_slug) != clean:
            cur_ids[app_slug] = clean
            container.settings.update({"composio_account_ids": cur_ids})
    except Exception:
        pass


class ComposioSetEmailPayload(BaseModel):
    app: str = Field(..., min_length=1, max_length=50)
    email: str = Field(default="", max_length=200)
    account_id: str = Field(default="", max_length=200)


@router.post("/api/hub/composio/email")
async def set_composio_account_email(
    payload: ComposioSetEmailPayload,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    """Persist user-specified email and/or account ID for a connected Composio app."""
    app_slug = extract_app_slug(payload.app)
    cfg = container.settings.load()
    existing_emails = dict(cfg.get("composio_account_emails") or {})
    clean_email = payload.email.strip()
    if clean_email:
        existing_emails[app_slug] = clean_email
    else:
        existing_emails.pop(app_slug, None)

    existing_ids = dict(cfg.get("composio_account_ids") or {})
    clean_id = payload.account_id.strip()
    if clean_id:
        existing_ids[app_slug] = clean_id
    elif payload.account_id != "":
        existing_ids.pop(app_slug, None)

    updates: dict[str, Any] = {"composio_account_emails": existing_emails}
    if clean_id or payload.account_id != "":
        updates["composio_account_ids"] = existing_ids
    container.settings.update(updates)

    return {
        "ok": True,
        "app": app_slug,
        "email": clean_email,
        "account_id": existing_ids.get(app_slug, ""),
        "composio_account_emails": existing_emails,
        "composio_account_ids": existing_ids,
    }


class ComposioConfirmPayload(BaseModel):
    app: str = Field(..., min_length=1, max_length=50)
    connected: bool = Field(default=True)


@router.post("/api/hub/composio/confirm")
async def confirm_composio_app(
    payload: ComposioConfirmPayload,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    """Persist manual verification or confirmation that an app is connected in Composio."""
    app_slug = extract_app_slug(payload.app)
    cfg = container.settings.load()
    existing = list(cfg.get("composio_connected_apps") or [])
    if payload.connected:
        if app_slug not in existing:
            existing.append(app_slug)
    else:
        existing = [a for a in existing if a != app_slug]
    container.settings.update({"composio_connected_apps": existing})
    return {"ok": True, "connected_apps": existing}


@router.post("/api/hub/composio/reset")
async def reset_composio_key(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    """Explicitly reset/clear Composio API key from settings."""
    container.settings.update({"composio_api_key": "", "composio_connected_apps": []})
    return {"ok": True, "message": "Composio API key successfully cleared"}


@router.get("/api/hub/composio/status")
async def get_composio_status(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    raw_key = container.settings.load().get("composio_api_key") or os.getenv("COMPOSIO_API_KEY", "")
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
    mcp_succeeded = False
    mcp_tool_count = 0
    active_mcp_headers = None
    rest_succeeded = False
    last_status = 401
    last_text = ""

    # 1. Direct MCP check on connect.composio.dev/mcp
    mcp_headers_to_try = []
    if key.lower().startswith("ck"):
        mcp_headers_to_try.append({"x-consumer-api-key": key, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
        mcp_headers_to_try.append({"x-api-key": key, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    else:
        mcp_headers_to_try.append({"x-api-key": key, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
        mcp_headers_to_try.append({"x-consumer-api-key": key, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})

    check_toolkits = [
        "gmail",
        "googlecalendar",
        "googlesheets",
        "github",
        "notion",
        "twitter",
        "linkedin",
        "instagram",
        "facebook",
        "threads",
        "youtube",
        "reddit",
    ]

    for m_hdrs in mcp_headers_to_try:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # A. Health check via tools/list
                list_res = await client.post(
                    "https://connect.composio.dev/mcp",
                    json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
                    headers=m_hdrs,
                )
                if list_res.status_code == 200:
                    list_rpc = parse_mcp_rpc_response(list_res)
                    if list_rpc:
                        mcp_succeeded = True
                        active_mcp_headers = m_hdrs
                        tools_list = list_rpc.get("result", {}).get("tools", [])
                        mcp_tool_count = len(tools_list)

                    # B. Check exact connection status for toolkits via COMPOSIO_MANAGE_CONNECTIONS
                    call_res = await client.post(
                        "https://connect.composio.dev/mcp",
                        json={
                            "jsonrpc": "2.0",
                            "method": "tools/call",
                            "params": {
                                "name": "COMPOSIO_MANAGE_CONNECTIONS",
                                "arguments": {"toolkits": check_toolkits, "reinitiate_all": False},
                            },
                            "id": 2,
                        },
                        headers=m_hdrs,
                    )
                    if call_res.status_code == 200:
                        call_payload = parse_mcp_rpc_response(call_res).get("result", {})
                        raw_json_str = ""
                        if isinstance(call_payload, dict):
                            content = call_payload.get("content", [])
                            if isinstance(content, list) and content:
                                raw_json_str = content[0].get("text", "")
                        if not raw_json_str and isinstance(call_payload, str):
                            raw_json_str = call_payload

                        res_obj = {}
                        if raw_json_str:
                            res_obj = parse_composio_json(raw_json_str)
                        elif isinstance(call_payload, dict):
                            res_obj = call_payload

                        data_part = res_obj.get("data", {}) if isinstance(res_obj, dict) else {}
                        results_map = (
                            data_part.get("results")
                            or res_obj.get("results")
                            or (data_part if isinstance(data_part, dict) else {})
                        )
                        if isinstance(results_map, dict):
                            for tk, tk_info in results_map.items():
                                if not isinstance(tk_info, dict):
                                    continue
                                norm_slug = extract_app_slug(tk)
                                st = str(tk_info.get("status") or "").lower()
                                accs = tk_info.get("accounts") or []
                                is_active = (
                                    st in ("active", "connected", "ready", "enabled", "initiated", "initialized")
                                    or any(
                                        str(a.get("status") or "").upper() in ("ACTIVE", "CONNECTED", "READY", "ENABLED", "INITIATED")
                                        for a in accs
                                        if isinstance(a, dict)
                                    )
                                    or (bool(accs) and st != "disconnected")
                                )
                                if is_active:
                                    ca_id = norm_slug
                                    email = ""
                                    for a in accs:
                                        if isinstance(a, dict):
                                            c_id = a.get("id") or a.get("nanoid")
                                            if c_id and str(c_id).startswith("ca_"):
                                                ca_id = str(c_id)
                                            e = extract_user_identifier(a)
                                            if e:
                                                email = e
                                    entry: dict[str, Any] = {"app": norm_slug, "status": "ACTIVE", "id": ca_id}
                                    if email:
                                        entry["email"] = email
                                    accounts_by_app[norm_slug] = entry
                                    if ca_id and str(ca_id).startswith("ca_"):
                                        _persist_found_account_id(container, norm_slug, str(ca_id))
                                    if email:
                                        _persist_found_email(container, norm_slug, email)
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

    # 2. REST API queries to backend.composio.dev (only if MCP did not already discover active accounts)
    if not (mcp_succeeded and accounts_by_app):
        rest_urls = [
            f"{COMPOSIO_BASE}/connected_accounts?account_type=ALL",
            "https://backend.composio.dev/api/v3.1/connected_accounts",
        ]
        if key.startswith("ck"):
            rest_headers = [{"x-consumer-api-key": key, "Accept": "application/json"}]
        else:
            rest_headers = [
                {"x-api-key": key, "Accept": "application/json"},
                {"Authorization": f"Bearer {key}", "Accept": "application/json"},
            ]

        for r_url in rest_urls:
            for r_hdrs in rest_headers:
                try:
                    async with httpx.AsyncClient(timeout=4.0) as client:
                        r_res = await client.get(r_url, headers=r_hdrs)
                        last_status = r_res.status_code
                        last_text = r_res.text
                        if r_res.status_code == 200:
                            rest_succeeded = True
                            r_data = r_res.json()
                            raw_items = []
                            if isinstance(r_data, list):
                                raw_items = r_data
                            elif isinstance(r_data, dict):
                                raw_items = (
                                    r_data.get("items")
                                    or r_data.get("connected_accounts")
                                    or r_data.get("data")
                                    or r_data.get("results")
                                    or []
                                )
                            for item in raw_items:
                                if not isinstance(item, dict):
                                    continue
                                app_slug = extract_app_slug(item)
                                if not app_slug:
                                    continue
                                raw_st = str(item.get("status") or "ACTIVE").upper()
                                st = "ACTIVE" if raw_st in ("ACTIVE", "CONNECTED", "READY", "ENABLED", "INITIATED", "INITIALIZED") else raw_st
                                raw_id = str(item.get("id") or item.get("nanoid") or item.get("accountId") or "")
                                email = extract_email_from_obj(item)
                                alias = str(item.get("alias") or item.get("account_identifier") or "")
                                existing = accounts_by_app.get(app_slug, {})
                                acc_id = raw_id or existing.get("id") or app_slug
                                acc_entry: dict[str, Any] = {"app": app_slug, "status": st, "id": acc_id}
                                if email or existing.get("email"):
                                    acc_entry["email"] = email or existing.get("email")
                                if alias or existing.get("alias"):
                                    acc_entry["alias"] = alias or existing.get("alias")
                                accounts_by_app[app_slug] = acc_entry
                                if raw_id and raw_id.startswith("ca_"):
                                    _persist_found_account_id(container, app_slug, raw_id)
                            break
                except Exception:
                    continue
            if rest_succeeded:
                break

    # 3. Merge locally persisted / user confirmed connections, saved emails & account IDs
    saved_emails: dict[str, str] = {}
    saved_ids: dict[str, str] = {}
    try:
        cfg = container.settings.load()
        saved_apps = cfg.get("composio_connected_apps") or []
        if isinstance(saved_apps, list):
            for s_app in saved_apps:
                s_slug = extract_app_slug(str(s_app))
                if s_slug and s_slug not in accounts_by_app:
                    accounts_by_app[s_slug] = {"app": s_slug, "status": "ACTIVE", "id": f"local_{s_slug}"}

        saved_emails = dict(cfg.get("composio_account_emails") or {})
        saved_ids = dict(cfg.get("composio_account_ids") or {})

        for s_slug, s_id in saved_ids.items():
            if s_slug not in accounts_by_app and s_id:
                accounts_by_app[s_slug] = {
                    "app": s_slug,
                    "status": "ACTIVE",
                    "id": s_id,
                    "email": saved_emails.get(s_slug, ""),
                }
        for s_slug, s_em in saved_emails.items():
            if s_slug not in accounts_by_app and s_em:
                accounts_by_app[s_slug] = {
                    "app": s_slug,
                    "status": "ACTIVE",
                    "id": saved_ids.get(s_slug, f"local_{s_slug}"),
                    "email": s_em,
                }

        for app_slug, acc_entry in accounts_by_app.items():
            if not acc_entry.get("email") and app_slug in saved_emails:
                acc_entry["email"] = saved_emails[app_slug]
            if not str(acc_entry.get("id") or "").startswith("ca_") and app_slug in saved_ids:
                acc_entry["id"] = saved_ids[app_slug]
    except Exception:
        pass

    # 4. Attempt deep account details & profile discovery for accounts missing an email
    for app_slug, acc_entry in list(accounts_by_app.items()):
        if not acc_entry.get("email"):
            ca_id = str(acc_entry.get("id") or "")
            if ca_id.startswith("ca_"):
                for r_hdrs in rest_headers[:2]:
                    try:
                        async with httpx.AsyncClient(timeout=4.0) as client:
                            ca_res = await client.get(
                                f"https://backend.composio.dev/api/v3.1/connected_accounts/{ca_id}",
                                headers=r_hdrs,
                            )
                            if ca_res.status_code == 200:
                                ext = extract_email_from_obj(ca_res.json())
                                if ext:
                                    acc_entry["email"] = ext
                                    _persist_found_email(container, app_slug, ext)
                                    break
                    except Exception:
                        pass

            # Deep Gmail profile discovery via GMAIL_GET_PROFILE tool if still missing
            if app_slug == "gmail" and not acc_entry.get("email"):
                for m_hdrs in mcp_headers_to_try:
                    try:
                        async with httpx.AsyncClient(timeout=5.0) as client:
                            p_res = await client.post(
                                "https://connect.composio.dev/mcp",
                                json={
                                    "jsonrpc": "2.0",
                                    "method": "tools/call",
                                    "params": {
                                        "name": "GMAIL_GET_PROFILE",
                                        "arguments": {},
                                    },
                                    "id": 98,
                                },
                                headers=m_hdrs,
                            )
                            if p_res.status_code == 200:
                                p_data = p_res.json().get("result", {})
                                p_text = ""
                                if isinstance(p_data, dict):
                                    c_arr = p_data.get("content", [])
                                    if isinstance(c_arr, list) and c_arr:
                                        p_text = c_arr[0].get("text", "")
                                if not p_text and isinstance(p_data, str):
                                    p_text = p_data
                                if p_text:
                                    try:
                                        parsed = json.loads(p_text)
                                        em = (
                                            parsed.get("emailAddress")
                                            or parsed.get("email")
                                            or extract_email_from_obj(parsed)
                                        )
                                        if em and "@" in str(em):
                                            acc_entry["email"] = str(em).strip()
                                            _persist_found_email(container, "gmail", str(em).strip())
                                            break
                                    except Exception:
                                        pass
                    except Exception:
                        pass

    if mcp_succeeded or rest_succeeded or accounts_by_app:
        accounts = list(accounts_by_app.values())
        msg = f"{len(accounts)} accounts connected" if accounts else (
            f"Composio active ({mcp_tool_count} MCP tools). Click Connect to link accounts."
            if mcp_succeeded else "Key valid. No accounts connected yet."
        )
        return {
            "configured": True,
            "valid": True,
            "masked_key": masked_key,
            "accounts": accounts,
            "composio_account_emails": saved_emails,
            "composio_account_ids": saved_ids,
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
    raw_key = container.settings.load().get("composio_api_key") or os.getenv("COMPOSIO_API_KEY", "")
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

    # 1. Try Composio MCP connection tool directly on connect.composio.dev/mcp or via hub
    mcp_headers_to_try = []
    if key.lower().startswith("ck"):
        mcp_headers_to_try.append({"x-consumer-api-key": key, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
        mcp_headers_to_try.append({"x-api-key": key, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    else:
        mcp_headers_to_try.append({"x-api-key": key, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
        mcp_headers_to_try.append({"x-consumer-api-key": key, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})

    for m_hdrs in mcp_headers_to_try:
        for reinit in (True, False):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    call_res = await client.post(
                        "https://connect.composio.dev/mcp",
                        json={
                            "jsonrpc": "2.0",
                            "method": "tools/call",
                            "params": {
                                "name": "COMPOSIO_MANAGE_CONNECTIONS",
                                "arguments": {"toolkits": [toolkit_slug], "reinitiate_all": reinit},
                            },
                            "id": 10,
                        },
                        headers=m_hdrs,
                    )
                    if call_res.status_code == 200:
                        data = parse_mcp_rpc_response(call_res).get("result", {})
                        raw_str = ""
                        if isinstance(data, dict):
                            content = data.get("content", [])
                            if isinstance(content, list) and content:
                                raw_str = content[0].get("text", "")
                        if not raw_str and isinstance(data, str):
                            raw_str = data
                        res_obj = parse_composio_json(raw_str) if raw_str else (data if isinstance(data, dict) else {})
                        data_part = res_obj.get("data", {}) if isinstance(res_obj, dict) else {}
                        results_map = (
                            data_part.get("results")
                            or res_obj.get("results")
                            or (data_part if isinstance(data_part, dict) else {})
                        )
                        res_info = results_map.get(toolkit_slug, {}) if isinstance(results_map, dict) else {}
                        auth_url = res_info.get("redirect_url") or res_obj.get("redirect_url") or ""
                        if not auth_url and "dashboard.composio.dev" in str(res_info.get("error_message", "")):
                            m = re.search(r"https://dashboard\.composio\.dev/[^\s\)\>]+", res_info["error_message"])
                            if m:
                                auth_url = m.group(0)
                        if auth_url:
                            return {"ok": True, "app": toolkit_slug, "auth_url": auth_url, "raw": res_info}
            except Exception:
                pass

    try:
        active_brain = container.settings.load_public().get("active_brain") or "Brain Default"
        raw = await container.hub.call_tool(
            name="mcp__composio__COMPOSIO_MANAGE_CONNECTIONS",
            args={"toolkits": [toolkit_slug], "reinitiate_all": True},
            brain=active_brain,
            run_mode="full",
            for_model=False,
        )
        data = parse_composio_json(raw)
        data_part = data.get("data", {}) if isinstance(data, dict) else {}
        results_map = data_part.get("results") or data.get("results") or {}
        res_info = results_map.get(toolkit_slug, {}) if isinstance(results_map, dict) else {}
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


class ExchangeAgensiCodePayload(BaseModel):
    code_or_url: str = Field(..., description="Authorization code or callback URL from browser")
    state: str | None = None


@router.post("/api/hub/agensi/connect")
async def initiate_agensi_connect(
    request: Request,
    redirect_url: str | None = None,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    # Clean up expired states (> 15 minutes)
    now = time.time()
    for st, data in list(_AGENSI_OAUTH_PENDING.items()):
        if now - data.get("created_at", 0) > 900:
            _AGENSI_OAUTH_PENDING.pop(st, None)

    base = str(request.base_url).rstrip("/")
    parsed = urlparse(base)
    is_https = parsed.scheme == "https"
    is_localhost = (parsed.hostname or "").lower() in ("localhost", "127.0.0.1")

    # Agensi Dynamic Client Registration strictly enforces RFC 6749:
    # HTTP is only permitted on loopback (localhost / 127.0.0.1).
    # All remote hostnames/IPs require HTTPS.
    valid_redirect_uris = [
        "http://localhost:7777/api/hub/agensi/callback",
        "http://127.0.0.1:7777/api/hub/agensi/callback",
        "urn:ietf:wg:oauth:2.0:oob",
    ]
    if is_https:
        https_cb = f"{base}/api/hub/agensi/callback"
        if https_cb not in valid_redirect_uris:
            valid_redirect_uris.insert(0, https_cb)

    if redirect_url:
        cb_url = redirect_url
    elif is_https:
        cb_url = f"{base}/api/hub/agensi/callback"
    elif is_localhost:
        cb_url = f"http://{parsed.netloc}/api/hub/agensi/callback"
    else:
        # Remote IP access over plain HTTP (e.g. http://202.71.13.37:7777).
        # We use http://localhost:7777/api/hub/agensi/callback so Agensi accepts registration.
        # Works out-of-the-box via SSH port forward or manual code/URL exchange.
        cb_url = "http://localhost:7777/api/hub/agensi/callback"

    # Register with Agensi via Dynamic Client Registration (RFC 7591)
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            reg_resp = await client.post(
                "https://mcp.agensi.io/oauth/register",
                headers={"Content-Type": "application/json"},
                json={
                    "client_name": "Jarvis OS",
                    "redirect_uris": valid_redirect_uris,
                },
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Failed to reach Agensi registration server: {exc}") from exc

        if reg_resp.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f"Failed to register OAuth client with Agensi: {reg_resp.text[:200]}")
        reg_data = reg_resp.json()
        client_id = reg_data.get("client_id")
        if not client_id:
            raise HTTPException(status_code=502, detail="No client_id returned by Agensi OAuth registration")

    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    state = secrets.token_hex(16)

    _AGENSI_OAUTH_PENDING[state] = {
        "client_id": client_id,
        "verifier": verifier,
        "redirect_uri": cb_url,
        "created_at": now,
    }

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": cb_url,
        "scope": "read install",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    auth_url = f"https://mcp.agensi.io/oauth/authorize?{urlencode(params)}"
    return {"ok": True, "auth_url": auth_url, "callback_url": cb_url}


@router.get("/api/hub/agensi/callback")
async def agensi_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    container: AppContainer = Depends(get_container),
) -> Response:
    from fastapi.responses import HTMLResponse

    if error:
        return HTMLResponse(
            status_code=400,
            content=f"""<!DOCTYPE html><html><body style="background:#09090b;color:#f4f4f5;font-family:sans-serif;padding:40px;text-align:center;">
            <h2 style="color:#f43f5e;">Ошибка авторизации Agensi</h2><p>{error}</p>
            <p><a style="color:#f59e0b;" href="/#tab=hub">Вернуться в MCP Hub</a></p></body></html>""",
        )

    if not code:
        return HTMLResponse(
            status_code=400,
            content="""<!DOCTYPE html><html><body style="background:#09090b;color:#f4f4f5;font-family:sans-serif;padding:40px;text-align:center;">
            <h2 style="color:#f43f5e;">Отсутствует код авторизации</h2>
            <p><a style="color:#f59e0b;" href="/#tab=hub">Вернуться в MCP Hub</a></p></body></html>""",
        )

    data = None
    if state and state in _AGENSI_OAUTH_PENDING:
        data = _AGENSI_OAUTH_PENDING.pop(state)
    elif _AGENSI_OAUTH_PENDING:
        latest_key = max(_AGENSI_OAUTH_PENDING.keys(), key=lambda k: _AGENSI_OAUTH_PENDING[k].get("created_at", 0))
        data = _AGENSI_OAUTH_PENDING.pop(latest_key)

    if not data:
        return HTMLResponse(
            status_code=400,
            content="""<!DOCTYPE html><html><body style="background:#09090b;color:#f4f4f5;font-family:sans-serif;padding:40px;text-align:center;">
            <h2 style="color:#f43f5e;">Сессия авторизации истекла</h2>
            <p>Пожалуйста, нажмите кнопку «Sign in via Agensi OAuth» заново в MCP Hub.</p>
            <p><a style="color:#f59e0b;" href="/#tab=hub">Вернуться в MCP Hub</a></p></body></html>""",
        )

    client_id = data["client_id"]
    verifier = data["verifier"]
    redirect_uri = data["redirect_uri"]

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            token_resp = await client.post(
                "https://mcp.agensi.io/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                },
            )
        except Exception as exc:
            return HTMLResponse(
                status_code=502,
                content=f"""<!DOCTYPE html><html><body style="background:#09090b;color:#f4f4f5;font-family:sans-serif;padding:40px;text-align:center;">
                <h2 style="color:#f43f5e;">Ошибка связи с Agensi</h2><p>{exc}</p>
                <p><a style="color:#f59e0b;" href="/#tab=hub">Вернуться в MCP Hub</a></p></body></html>""",
            )

        if token_resp.status_code != 200:
            return HTMLResponse(
                status_code=400,
                content=f"""<!DOCTYPE html><html><body style="background:#09090b;color:#f4f4f5;font-family:sans-serif;padding:40px;text-align:center;">
                <h2 style="color:#f43f5e;">Agensi отклонил запрос ({token_resp.status_code})</h2>
                <p>{token_resp.text[:300]}</p>
                <p><a style="color:#f59e0b;" href="/#tab=hub">Вернуться в MCP Hub</a></p></body></html>""",
            )

        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return HTMLResponse(
                status_code=502,
                content="""<!DOCTYPE html><html><body style="background:#09090b;color:#f4f4f5;font-family:sans-serif;padding:40px;text-align:center;">
                <h2 style="color:#f43f5e;">Сервер Agensi не вернул access_token</h2>
                <p><a style="color:#f59e0b;" href="/#tab=hub">Вернуться в MCP Hub</a></p></body></html>""",
            )

    # Upsert connection with bearer token
    conn = MCPConnection(
        id="agensi",
        name="Agensi Skills Marketplace",
        transport="http",
        perm="safe",
        enabled=True,
        url="https://mcp.agensi.io/mcp",
        headers={"Authorization": f"Bearer {access_token}"},
        write_tools=["upload_*", "set_skill_*", "submit_*", "update_*", "buy_*"],
    )
    container.hub.connections.upsert(conn)
    container.hub.mcp_pool.invalidate("agensi")

    return HTMLResponse(
        content="""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Agensi успешно подключен!</title>
  <meta http-equiv="refresh" content="1; url=/#connected=agensi">
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #09090b;
      color: #f4f4f5;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
      text-align: center;
    }
    .box {
      background: #18181b;
      border: 1px solid #27272a;
      border-radius: 12px;
      padding: 36px;
      max-width: 450px;
    }
    h2 { color: #10b981; margin: 0 0 12px; }
    p { color: #a1a1aa; font-size: 14px; margin: 0 0 16px; }
    a { color: #f59e0b; text-decoration: none; font-weight: 500; font-size: 13px; }
  </style>
</head>
<body>
  <div class="box">
    <h2>✅ Agensi успешно авторизован!</h2>
    <p>Все инструменты маркетплейса активированы. Перенаправляем в Jarvis OS...</p>
    <a href="/#connected=agensi">Нажмите сюда, если переход не произошел автоматически →</a>
  </div>
  <script>
    if (window.opener) {
      try { window.opener.postMessage({ type: "agensi_connected" }, "*"); } catch(e) {}
    }
    setTimeout(() => {
      if (window.opener) {
        try { window.close(); } catch(e) {}
      }
      window.location.href = "/#connected=agensi";
    }, 1500);
  </script>
</body>
</html>"""
    )


@router.post("/api/hub/agensi/exchange")
async def exchange_agensi_code(
    payload: ExchangeAgensiCodePayload,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    raw = payload.code_or_url.strip()
    code = ""
    state = (payload.state or "").strip()

    if "code=" in raw:
        try:
            parsed_q = parse_qs(urlparse(raw).query or raw)
            code = (parsed_q.get("code") or [""])[0]
            if not state:
                state = (parsed_q.get("state") or [""])[0]
        except Exception:
            code = raw
    else:
        code = raw

    if not code:
        raise HTTPException(status_code=400, detail="Не найден код авторизации в переданной строке")

    data = None
    if state and state in _AGENSI_OAUTH_PENDING:
        data = _AGENSI_OAUTH_PENDING.pop(state)
    elif _AGENSI_OAUTH_PENDING:
        latest_key = max(_AGENSI_OAUTH_PENDING.keys(), key=lambda k: _AGENSI_OAUTH_PENDING[k].get("created_at", 0))
        data = _AGENSI_OAUTH_PENDING.pop(latest_key)

    if not data:
        raise HTTPException(status_code=400, detail="Сессия авторизации истекла или не найдена. Нажмите 'Sign in via Agensi OAuth' заново.")

    client_id = data["client_id"]
    verifier = data["verifier"]
    redirect_uri = data["redirect_uri"]

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            token_resp = await client.post(
                "https://mcp.agensi.io/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                },
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Ошибка связи с сервером токенов Agensi: {exc}") from exc

        if token_resp.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Agensi отклонил код авторизации ({token_resp.status_code}): {token_resp.text[:200]}")

        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=502, detail="Сервер Agensi не вернул access_token")

    conn = MCPConnection(
        id="agensi",
        name="Agensi Skills Marketplace",
        transport="http",
        perm="safe",
        enabled=True,
        url="https://mcp.agensi.io/mcp",
        headers={"Authorization": f"Bearer {access_token}"},
        write_tools=["upload_*", "set_skill_*", "submit_*", "update_*", "buy_*"],
    )
    container.hub.connections.upsert(conn)
    container.hub.mcp_pool.invalidate("agensi")
    tools = await container.hub.mcp_pool.test_connection(conn)
    return {
        "ok": True,
        "message": f"Agensi успешно авторизован! Доступно инструментов: {len(tools)}",
        "tools_count": len(tools),
        "access_token_preview": f"{access_token[:6]}...{access_token[-4:]}" if len(access_token) > 10 else "***",
    }


@router.post("/api/hub/agensi/reset")
async def reset_agensi(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    conn = container.hub.connections.get("agensi")
    if conn:
        conn.headers = {}
        container.hub.connections.upsert(conn)
        container.hub.mcp_pool.invalidate("agensi")
    return {"ok": True, "message": "Авторизация Agensi сброшена (включен публичный каталог инструментов)"}


@router.get("/api/hub/agensi/status")
async def get_agensi_status(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    conn = container.hub.connections.get("agensi")
    if not conn or not conn.enabled:
        return {"connected": False, "authenticated": False, "tools_count": 0}
    has_auth = bool(conn.headers.get("Authorization") or conn.headers.get("authorization"))
    try:
        tools = await container.hub.mcp_pool.test_connection(conn)
        return {
            "connected": True,
            "authenticated": has_auth,
            "tools_count": len(tools),
            "url": conn.url,
        }
    except Exception as exc:
        err_msg = str(exc)
        return {
            "connected": True,
            "authenticated": False if "401" in err_msg or "токен" in err_msg else has_auth,
            "error": err_msg,
            "tools_count": 0,
        }


# ---- Interactive Approval Gate Routes ----


class ResolveApprovalPayload(BaseModel):
    decision: str = Field(..., pattern="^(approve|deny)$", description="'approve' or 'deny'")


@router.get("/api/hub/approvals/pending")
async def list_pending_approvals(
    session_id: str | None = None,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    """List pending tool executions awaiting human confirmation."""
    pending = container.approvals.list_pending(session_id=session_id)
    return {"ok": True, "pending": pending}


@router.post("/api/hub/approvals/{approval_id}/resolve")
async def resolve_approval(
    approval_id: str,
    payload: ResolveApprovalPayload,
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    """Approve or deny a paused tool execution."""
    resolved = await container.approvals.resolve(
        approval_id,
        approved=(payload.decision == "approve"),
    )
    if resolved is None:
        raise HTTPException(status_code=404, detail="Approval request not found, expired, or already resolved")
    return {"ok": True, "approval": resolved}


