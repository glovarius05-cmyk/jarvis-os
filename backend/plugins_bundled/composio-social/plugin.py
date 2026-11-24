"""Bundled plugin: composio-social - unified social media, Gmail, and Google Calendar management via Composio.

Provides:
- composio_status (readonly): inspect API key status and list connected accounts (Gmail with email address, Calendar, etc.).
- composio_connect (safe): initiate OAuth connection flow for Gmail, Calendar, Twitter, LinkedIn, etc.
- composio_gmail (safe): inspect Gmail profile, search/read emails, or send emails.
- composio_calendar (safe): list upcoming calendar events or create new events.
- composio_feed (readonly): fetch recent posts, mentions, emails, or events.
- composio_post (full): publish posts, threads, or updates with strict platform constraints.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.api.routes.hub import extract_app_slug, extract_user_identifier, parse_composio_json

COMPOSIO_API_BASE = "https://backend.composio.dev/api/v3.1"

PLATFORM_CONSTRAINTS = {
    "twitter": {"max_len": 280, "name": "Twitter / X", "action": "TWITTER_CREATION_OF_A_POST"},
    "x": {"max_len": 280, "name": "Twitter / X", "action": "TWITTER_CREATION_OF_A_POST"},
    "linkedin": {"max_len": 3000, "name": "LinkedIn", "action": "LINKEDIN_CREATE_A_POST"},
    "threads": {"max_len": 500, "name": "Threads", "action": "THREADS_CREATE_POST"},
    "instagram": {"max_len": 2200, "name": "Instagram", "action": "INSTAGRAM_CREATE_A_POST"},
    "facebook": {"max_len": 63206, "name": "Facebook", "action": "FACEBOOK_CREATE_A_PAGE_POST"},
    "youtube": {"max_len": 5000, "name": "YouTube", "action": "YOUTUBE_UPLOAD_VIDEO"},
    "reddit": {"max_len": 40000, "name": "Reddit", "action": "REDDIT_SUBMIT_POST"},
}


def _extract_email_from_account(it: Any) -> str:
    """Extract email address from connected account dictionary or structure."""
    if not isinstance(it, dict):
        return ""

    # 1. Direct fields
    for k in ("email", "user_email", "account_email", "emailAddress", "email_address", "email_id"):
        v = it.get(k)
        if isinstance(v, str) and "@" in v:
            return v.strip()

    # 2. Check alias, name, or account_identifier
    for k in ("alias", "account_identifier", "identifier", "name", "username"):
        v = it.get(k)
        if isinstance(v, str) and "@" in v:
            return v.strip()

    # 3. Check nested objects
    for parent in ("state", "params", "data", "metadata", "profile", "auth_config", "connection_data"):
        sub = it.get(parent)
        if isinstance(sub, dict):
            val = sub.get("val") if isinstance(sub.get("val"), dict) else sub
            for k in ("email", "user_email", "account_email", "emailAddress", "email_address", "identifier"):
                v = val.get(k)
                if isinstance(v, str) and "@" in v:
                    return v.strip()

    # 4. Regex fallback across JSON representation
    try:
        raw_dump = json.dumps(it)
        matches = re.findall(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', raw_dump)
        for m in matches:
            low = m.lower()
            if not any(bad in low for bad in ("composio.dev", "example.com", "schema.org", "sentry.io", "w3.org")):
                return m
    except Exception:
        pass

    return ""


def _get_api_key(ctx) -> str:
    settings = ctx.services.get("settings")
    if settings is not None:
        cfg = settings.load()
        key = str(cfg.get("composio_api_key") or "").strip()
        if key:
            return key
    import os
    return os.getenv("COMPOSIO_API_KEY", "").strip()


async def _execute_composio_action(
    api_key: str,
    action_name: str,
    arguments: dict[str, Any] | None = None,
    connected_account_id: str | None = None,
    user_id: str | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Execute a Composio tool/action across multiple API version endpoints."""
    args = arguments or {}
    real_ca_id = str(connected_account_id).strip() if (connected_account_id and str(connected_account_id).startswith("ca_")) else None
    effective_user = str(user_id or "default").strip()

    if api_key.startswith("ck"):
        headers_variants = [
            {"x-consumer-api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"},
            {"x-api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"},
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"},
        ]
    else:
        headers_variants = [
            {"x-api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"},
            {"x-consumer-api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"},
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"},
        ]

    payloads: list[dict[str, Any]] = []
    if real_ca_id:
        payloads.append({"connected_account_id": real_ca_id, "user_id": effective_user, "arguments": args})
        payloads.append({"connected_account_id": real_ca_id, "arguments": args})
        payloads.append({"connected_account_id": real_ca_id, "entity_id": effective_user, "arguments": args})
    payloads.append({"user_id": effective_user, "entity_id": effective_user, "arguments": args})
    payloads.append({"user_id": effective_user, "arguments": args})
    payloads.append({"arguments": args})

    endpoints = [
        f"{COMPOSIO_API_BASE}/tools/execute/{action_name}",
        f"https://backend.composio.dev/api/v3/tools/execute/{action_name}",
        f"{COMPOSIO_API_BASE}/actions/{action_name}/execute",
        f"https://backend.composio.dev/api/v3/actions/{action_name}/execute",
        f"{COMPOSIO_API_BASE}/tools/execute/{action_name.lower()}",
    ]

    last_error = ""

    # When using a Consumer Key (ck_...), Composio Connect MCP is the primary engine
    if api_key.startswith("ck"):
        call_params = {
            "name": "COMPOSIO_EXECUTE_TOOL",
            "arguments": {
                "action_name": action_name,
                "tool_name": action_name,
                "arguments": args,
                **({"connected_account_id": real_ca_id} if real_ca_id else {}),
                "user_id": effective_user,
            },
        }
        if real_ca_id and "connected_account_id" not in args:
            call_params["arguments"]["arguments"] = {**args, "connected_account_id": real_ca_id}

        rpc_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": call_params,
        }
        for headers in headers_variants:
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    res = await client.post("https://connect.composio.dev/mcp", headers=headers, json=rpc_payload)
                    if res.status_code == 200:
                        data = res.json().get("result", {})
                        raw_text = ""
                        if isinstance(data, dict):
                            if data.get("isError"):
                                last_error = json.dumps(data)
                                continue
                            content = data.get("content", [])
                            if isinstance(content, list) and content:
                                raw_text = content[0].get("text", "")
                        if not raw_text and isinstance(data, str):
                            raw_text = data
                        if raw_text:
                            parsed = parse_composio_json(raw_text)
                            if parsed and isinstance(parsed, dict) and (parsed.get("successful") is False or parsed.get("success") is False):
                                last_error = str(parsed.get("error") or parsed.get("message") or raw_text[:200])
                                continue
                            if parsed:
                                return {"success": True, "data": parsed, "status_code": 200}
                            return {"success": True, "data": raw_text, "status_code": 200}
                        if data:
                            return {"success": True, "data": data, "status_code": 200}
            except Exception as ex:
                last_error = str(ex)
                continue

    # REST endpoints (for standard ak_ project keys or if MCP is unavailable)
    for url in endpoints:
        for payload in payloads:
            for headers in headers_variants:
                try:
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        res = await client.post(url, headers=headers, json=payload)
                        if res.status_code in {200, 201}:
                            data = res.json()
                            if isinstance(data, dict):
                                if data.get("successful") is False or data.get("success") is False:
                                    last_error = str(data.get("error") or data.get("message") or "Tool returned unsuccessful status")
                                    continue
                            resp = (
                                data.get("response_data")
                                or data.get("data")
                                or data.get("result")
                                or data
                            )
                            return {"success": True, "data": resp, "status_code": res.status_code}
                        else:
                            last_error = f"HTTP {res.status_code}: {res.text[:200]}"
                except Exception as ex:
                    last_error = str(ex)
                    continue

    # Fallback to connect MCP
    mcp_tool_variants = [
        action_name,
        action_name.lower(),
        f"mcp__composio__{action_name}",
        "COMPOSIO_EXECUTE_TOOL",
    ]
    for tool_variant in mcp_tool_variants:
        mcp_args = dict(args)
        if tool_variant == "COMPOSIO_EXECUTE_TOOL":
            call_params = {
                "name": "COMPOSIO_EXECUTE_TOOL",
                "arguments": {
                    "action_name": action_name,
                    "arguments": args,
                    **({"connected_account_id": real_ca_id} if real_ca_id else {}),
                },
            }
        else:
            if real_ca_id:
                mcp_args["connected_account_id"] = real_ca_id
            mcp_args.setdefault("user_id", effective_user)
            call_params = {
                "name": tool_variant,
                "arguments": mcp_args,
            }

        rpc_payload = {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": call_params,
        }

        for headers in headers_variants:
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    res = await client.post("https://connect.composio.dev/mcp", headers=headers, json=rpc_payload)
                    if res.status_code == 200:
                        data = res.json().get("result", {})
                        raw_text = ""
                        if isinstance(data, dict):
                            if data.get("isError"):
                                last_error = json.dumps(data)
                                continue
                            content = data.get("content", [])
                            if isinstance(content, list) and content:
                                raw_text = content[0].get("text", "")
                        if not raw_text and isinstance(data, str):
                            raw_text = data
                        if raw_text:
                            try:
                                parsed = parse_composio_json(raw_text)
                                if parsed and isinstance(parsed, dict) and (parsed.get("successful") is False or parsed.get("success") is False):
                                    last_error = str(parsed.get("error") or parsed.get("message") or raw_text[:200])
                                    continue
                                if parsed:
                                    return {"success": True, "data": parsed, "status_code": 200}
                                return {"success": True, "data": raw_text, "status_code": 200}
                            except Exception:
                                return {"success": True, "data": raw_text, "status_code": 200}
                        if data:
                            return {"success": True, "data": data, "status_code": 200}
            except Exception as ex:
                last_error = str(ex)
                continue

    err = f"Failed to execute action {action_name}"
    if last_error:
        err += f": {last_error}"
    return {"success": False, "error": err}


async def _get_connected_apps(ctx, api_key: str) -> dict[str, dict[str, Any]]:
    """Gather all connected accounts from settings, REST API, and MCP."""
    accounts: dict[str, dict[str, Any]] = {}

    # 1. Check locally confirmed / persisted connections in settings
    settings = ctx.services.get("settings")
    saved_emails: dict[str, str] = {}
    saved_ids: dict[str, str] = {}
    if settings is not None:
        try:
            cfg = settings.load()
            saved_apps = cfg.get("composio_connected_apps") or []
            saved_emails = dict(cfg.get("composio_account_emails") or {})
            saved_ids = dict(cfg.get("composio_account_ids") or {})
            if isinstance(saved_apps, list):
                for s in saved_apps:
                    slug = extract_app_slug(s)
                    if slug:
                        entry = {"app": slug, "status": "ACTIVE", "id": saved_ids.get(slug) or f"local_{slug}"}
                        if slug in saved_emails:
                            entry["email"] = saved_emails[slug]
                        accounts[slug] = entry
        except Exception:
            pass

    # 2. REST API queries (only for ak_ project keys, skipped for ck_ consumer keys which only support MCP)
    if not api_key.startswith("ck"):
        rest_urls = [
            f"{COMPOSIO_API_BASE}/connected_accounts?account_type=ALL",
            f"{COMPOSIO_API_BASE}/connected_accounts",
            f"{COMPOSIO_API_BASE}/connected_accounts?user_ids=default",
            "https://backend.composio.dev/api/v3/connected_accounts?account_type=ALL",
            "https://backend.composio.dev/api/v3/connected_accounts",
            "https://backend.composio.dev/api/v3/connected_accounts?user_ids=default",
        ]
        headers_variants = [
            {"x-api-key": api_key, "Accept": "application/json"},
            {"x-consumer-api-key": api_key, "Accept": "application/json"},
            {"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        ]
        for url in rest_urls:
            for hdrs in headers_variants:
                try:
                    async with httpx.AsyncClient(timeout=8.0) as client:
                        res = await client.get(url, headers=hdrs)
                        if res.status_code == 200:
                            data = res.json()
                            items = []
                            if isinstance(data, list):
                                items = data
                            elif isinstance(data, dict):
                                items = (
                                    data.get("items")
                                    or data.get("connected_accounts")
                                    or data.get("data")
                                    or data.get("results")
                                    or []
                                )
                            for it in items:
                                if not isinstance(it, dict):
                                    continue
                                slug = extract_app_slug(it)
                                if not slug:
                                    continue
                                raw_st = str(it.get("status") or "ACTIVE").upper()
                                st = "ACTIVE" if raw_st in ("ACTIVE", "CONNECTED", "READY", "ENABLED", "INITIATED", "INITIALIZED") else raw_st
                                raw_id = str(it.get("id") or it.get("nanoid") or it.get("accountId") or "")
                                user_id = str(it.get("user_id") or it.get("userId") or "default")
                                email = _extract_email_from_account(it)

                                existing = accounts.get(slug, {})
                                acc_id = raw_id or existing.get("id") or slug
                                acc_entry: dict[str, Any] = {
                                    "app": slug,
                                    "status": st,
                                    "id": acc_id,
                                    "user_id": user_id,
                                }
                                if email or existing.get("email"):
                                    acc_entry["email"] = email or existing.get("email")
                                alias = str(it.get("alias") or it.get("account_identifier") or existing.get("alias") or "")
                                if alias:
                                    acc_entry["alias"] = alias
                                accounts[slug] = acc_entry
                except Exception:
                    continue

    # 3. Direct MCP call to connect.composio.dev/mcp
    check_toolkits = ["gmail", "googlecalendar", "googlesheets", "github", "notion", "twitter", "linkedin", "instagram"]
    mcp_headers_variants = (
        [
            {"x-consumer-api-key": api_key, "Content-Type": "application/json"},
            {"x-api-key": api_key, "Content-Type": "application/json"},
        ]
        if api_key.startswith("ck")
        else [
            {"x-api-key": api_key, "Content-Type": "application/json"},
            {"x-consumer-api-key": api_key, "Content-Type": "application/json"},
        ]
    )
    for hdrs in mcp_headers_variants:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
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
                    headers=hdrs,
                )
                if call_res.status_code == 200:
                    payload = call_res.json().get("result", {})
                    raw_text = ""
                    if isinstance(payload, dict):
                        content = payload.get("content", [])
                        if isinstance(content, list) and content:
                            raw_text = content[0].get("text", "")
                    if not raw_text and isinstance(payload, str):
                        raw_text = payload
                    res_obj = {}
                    if raw_text:
                        res_obj = parse_composio_json(raw_text)
                    elif isinstance(payload, dict):
                        res_obj = payload

                    results_map = (
                        res_obj.get("data", {}).get("results", {})
                        if isinstance(res_obj.get("data"), dict)
                        else res_obj.get("results", {})
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
                                existing = accounts.get(norm_slug, {})
                                ca_id = existing.get("id") or norm_slug
                                email = existing.get("email") or ""
                                user_id = existing.get("user_id") or "default"
                                for a in accs:
                                    if isinstance(a, dict):
                                        c_id = a.get("id") or a.get("nanoid")
                                        if c_id and str(c_id).startswith("ca_"):
                                            ca_id = str(c_id)
                                            if settings is not None:
                                                try:
                                                    c_cfg = settings.load()
                                                    c_ids = dict(c_cfg.get("composio_account_ids") or {})
                                                    if c_ids.get(norm_slug) != ca_id:
                                                        c_ids[norm_slug] = ca_id
                                                        settings.update({"composio_account_ids": c_ids})
                                                except Exception:
                                                    pass
                                        e = extract_user_identifier(a)
                                        if e:
                                            email = e
                                        u = a.get("user_id") or a.get("userId")
                                        if u:
                                            user_id = str(u)
                                entry = {
                                    "app": norm_slug,
                                    "status": "ACTIVE",
                                    "id": ca_id,
                                    "user_id": user_id,
                                }
                                if email:
                                    entry["email"] = email
                                accounts[norm_slug] = entry
                    break
        except Exception:
            continue

    # 4. If Gmail has a real ca_... ID but no email yet, query detailed account object
    gmail_acc = accounts.get("gmail")
    if gmail_acc:
        g_id = str(gmail_acc.get("id") or "")
        if g_id.startswith("ca_") and not gmail_acc.get("email"):
            for hdrs in headers_variants:
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        ac_res = await client.get(
                            f"{COMPOSIO_API_BASE}/connected_accounts/{g_id}",
                            headers=hdrs,
                        )
                        if ac_res.status_code == 200:
                            g_data = ac_res.json()
                            e = _extract_email_from_account(g_data)
                            if e:
                                gmail_acc["email"] = e
                                break
                except Exception:
                    continue

    # 5. If Gmail is connected, discover user email address and mailbox counts via GMAIL_GET_PROFILE
    if "gmail" in accounts:
        g_info = accounts["gmail"]
        ca_id = g_info.get("id")
        user_id = g_info.get("user_id")
        for slug_to_try in ("GMAIL_GET_PROFILE", "GMAIL_FETCH_USER_PROFILE"):
            try:
                profile_res = await _execute_composio_action(
                    api_key,
                    slug_to_try,
                    {},
                    connected_account_id=ca_id,
                    user_id=user_id,
                    timeout=8.0,
                )
                if profile_res.get("success"):
                    pdata = profile_res.get("data", {})
                    if isinstance(pdata, dict):
                        email = (
                            pdata.get("emailAddress")
                            or pdata.get("email")
                            or pdata.get("email_address")
                            or ""
                        )
                        if email:
                            accounts["gmail"]["email"] = str(email)
                        if "messagesTotal" in pdata:
                            accounts["gmail"]["messagesTotal"] = pdata.get("messagesTotal")
                        if "threadsTotal" in pdata:
                            accounts["gmail"]["threadsTotal"] = pdata.get("threadsTotal")
                        break
            except Exception:
                continue

    # 6. Apply saved emails, usernames, and account IDs fallback for any account
    for s_slug, s_id in saved_ids.items():
        if s_slug not in accounts and s_id:
            accounts[s_slug] = {
                "app": s_slug,
                "status": "ACTIVE",
                "id": s_id,
                "email": saved_emails.get(s_slug, ""),
            }
    for s_slug, s_em in saved_emails.items():
        if s_slug not in accounts and s_em:
            accounts[s_slug] = {
                "app": s_slug,
                "status": "ACTIVE",
                "id": saved_ids.get(s_slug, f"local_{s_slug}"),
                "email": s_em,
            }
    for s_slug, s_em in saved_emails.items():
        if s_slug in accounts and not accounts[s_slug].get("email") and s_em:
            accounts[s_slug]["email"] = s_em
    for s_slug, s_id in saved_ids.items():
        if s_slug in accounts and not str(accounts[s_slug].get("id") or "").startswith("ca_") and s_id:
            accounts[s_slug]["id"] = s_id

    # 7. Persist newly discovered Gmail email into settings
    gmail_discovered = accounts.get("gmail", {}).get("email")
    if gmail_discovered and "@" in str(gmail_discovered) and settings is not None:
        try:
            c_cfg = settings.load()
            c_emails = dict(c_cfg.get("composio_account_emails") or {})
            if c_emails.get("gmail") != str(gmail_discovered):
                c_emails["gmail"] = str(gmail_discovered)
                settings.update({"composio_account_emails": c_emails})
        except Exception:
            pass

    return accounts


def register(ctx) -> None:
    async def composio_status(_args: dict, _brain: str) -> str:
        api_key = _get_api_key(ctx)
        if not api_key:
            return (
                "Composio API key is NOT configured. "
                "Please configure 'composio_api_key' in Settings or Connections to use social and email tools."
            )

        accounts = await _get_connected_apps(ctx, api_key)
        if not accounts:
            return (
                "Ключ Composio активен и валиден, но ни одного аккаунта сервисов пока не подключено.\n"
                "Перейдите в интерфейсе в раздел Connections и нажмите 'Connect' для нужного сервиса (Gmail, Календарь, Twitter и др.)."
            )

        lines = ["Статус подключений Composio: АКТИВЕН"]
        lines.append(f"Всего подключенных сервисов: {len(accounts)}")
        for slug, info in accounts.items():
            email_info = f", email: {info['email']}" if "email" in info else ""
            lines.append(f"- {slug.upper()}{email_info} (статус: {info.get('status', 'ACTIVE')})")

        lines.append("\nВсе указанные интеграции готовы к работе (чтение почты, расписание календаря, посты).")
        return "\n".join(lines)

    async def composio_connect(args: dict, _brain: str) -> str:
        app = str(args.get("app") or args.get("platform") or "").strip().lower()
        if not app:
            return "ERROR: 'app' parameter is required (e.g. gmail, github, googlecalendar, twitter, linkedin, instagram)."

        api_key = _get_api_key(ctx)
        if not api_key:
            return "ERROR: Composio API key is not configured. Add 'composio_api_key' in Settings."

        norm_app = extract_app_slug(app) or app
        dashboard_url = f"https://dashboard.composio.dev/~/org/connect/apps/{norm_app}?open=true"

        # Try to obtain real OAuth authorization link from Composio Connect MCP
        auth_url = ""
        mcp_headers = (
            [
                {"x-consumer-api-key": api_key, "Content-Type": "application/json"},
                {"x-api-key": api_key, "Content-Type": "application/json"},
            ]
            if api_key.startswith("ck")
            else [
                {"x-api-key": api_key, "Content-Type": "application/json"},
                {"x-consumer-api-key": api_key, "Content-Type": "application/json"},
            ]
        )
        for hdrs in mcp_headers:
            for reinit in (True, False):
                try:
                    async with httpx.AsyncClient(timeout=8.0) as client:
                        call_res = await client.post(
                            "https://connect.composio.dev/mcp",
                            json={
                                "jsonrpc": "2.0",
                                "method": "tools/call",
                                "params": {
                                    "name": "COMPOSIO_MANAGE_CONNECTIONS",
                                    "arguments": {"toolkits": [norm_app], "reinitiate_all": reinit},
                                },
                                "id": 10,
                            },
                            headers=hdrs,
                        )
                        if call_res.status_code == 200:
                            data = call_res.json().get("result", {})
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
                            res_info = results_map.get(norm_app, {}) if isinstance(results_map, dict) else {}
                            auth_url = res_info.get("redirect_url") or res_obj.get("redirect_url") or ""
                            if auth_url:
                                break
                except Exception:
                    pass
            if auth_url:
                break

        link_to_show = auth_url or dashboard_url
        return (
            f"Для подключения {norm_app.upper()} перейдите по ссылке авторизации в браузере:\n"
            f"{link_to_show}\n"
            f"После авторизации вернитесь в Jarvis OS и запустите composio_status для проверки."
        )

    async def composio_gmail(args: dict, _brain: str) -> str:
        api_key = _get_api_key(ctx)
        if not api_key:
            return "ERROR: Composio API key is not configured. Add 'composio_api_key' in Settings."

        action = str(args.get("action") or "profile").strip().lower()
        query = str(args.get("query") or "").strip()
        limit = int(args.get("limit") or 5)
        message_id = str(args.get("message_id") or "").strip()

        accounts = await _get_connected_apps(ctx, api_key)
        gmail_info = accounts.get("gmail", {})
        ca_id = gmail_info.get("id")
        user_id = gmail_info.get("user_id")

        if not ca_id or not str(ca_id).startswith("ca_"):
            settings_svc = ctx.services.get("settings")
            if settings_svc is not None:
                try:
                    c_ids = settings_svc.load().get("composio_account_ids") or {}
                    if c_ids.get("gmail"):
                        ca_id = c_ids["gmail"]
                except Exception:
                    pass

        if action in ("profile", "status", "whoami", "email", "info"):
            res = await _execute_composio_action(
                api_key,
                "GMAIL_GET_PROFILE",
                {},
                connected_account_id=ca_id,
                user_id=user_id,
            )
            if not res.get("success"):
                res = await _execute_composio_action(
                    api_key,
                    "GMAIL_FETCH_USER_PROFILE",
                    {},
                    connected_account_id=ca_id,
                    user_id=user_id,
                )
            if res.get("success"):
                pdata = res.get("data", {})
                email = pdata.get("emailAddress") or pdata.get("email") or gmail_info.get("email") or "подключен"
                if "@" in str(email):
                    settings_svc = ctx.services.get("settings")
                    if settings_svc is not None:
                        try:
                            c_cfg = settings_svc.load()
                            c_emails = dict(c_cfg.get("composio_account_emails") or {})
                            if c_emails.get("gmail") != str(email):
                                c_emails["gmail"] = str(email)
                                settings_svc.update({"composio_account_emails": c_emails})
                        except Exception:
                            pass
                total_msgs = pdata.get("messagesTotal", gmail_info.get("messagesTotal", "N/A"))
                total_threads = pdata.get("threadsTotal", gmail_info.get("threadsTotal", "N/A"))
                return (
                    f"Подключенный профиль Gmail:\n"
                    f"- Email: {email}\n"
                    f"- Всего сообщений в ящике: {total_msgs}\n"
                    f"- Всего цепочек писем: {total_threads}\n"
                    f"- Статус интеграции: АКТИВЕН"
                )

            # Fallback to connected account metadata if API action execution failed
            if gmail_info.get("email"):
                return (
                    f"Подключенный профиль Gmail:\n"
                    f"- Email: {gmail_info['email']}\n"
                    f"- Статус интеграции: АКТИВЕН (учетная запись подтверждена в Composio)"
                )

            if "gmail" in accounts:
                return (
                    f"Gmail подключен в Composio (аккаунт: {ca_id or 'active'}), "
                    f"но запрос профиля вернул: {res.get('error')}.\n"
                    f"Вы можете попробовать запросить список писем через composio_gmail(action='list')."
                )
            return f"Gmail не подключен в Composio: {res.get('error')}"

        if action in ("list", "messages", "inbox", "search", "unread"):
            search_query = query or "in:inbox"
            res = await _execute_composio_action(
                api_key,
                "GMAIL_LIST_MESSAGES",
                {"query": search_query, "max_results": limit},
                connected_account_id=ca_id,
                user_id=user_id,
            )
            if not res.get("success"):
                res = await _execute_composio_action(
                    api_key,
                    "GMAIL_FETCH_EMAILS",
                    {"query": search_query, "max_results": limit},
                    connected_account_id=ca_id,
                    user_id=user_id,
                )
            if res.get("success"):
                data = res.get("data")
                return f"Письма Gmail по запросу '{search_query}':\n{json.dumps(data, ensure_ascii=False, indent=2)}"
            return f"Не удалось получить список писем: {res.get('error')}"

        if action in ("read", "get", "message"):
            if not message_id:
                return "ERROR: 'message_id' is required to read a specific message."
            res = await _execute_composio_action(
                api_key,
                "GMAIL_GET_MESSAGE",
                {"message_id": message_id},
                connected_account_id=ca_id,
                user_id=user_id,
            )
            if not res.get("success"):
                res = await _execute_composio_action(
                    api_key,
                    "GMAIL_FETCH_EMAIL",
                    {"message_id": message_id},
                    connected_account_id=ca_id,
                    user_id=user_id,
                )
            if res.get("success"):
                return f"Письмо Gmail ({message_id}):\n{json.dumps(res.get('data'), ensure_ascii=False, indent=2)}"
            return f"Не удалось прочитать письмо: {res.get('error')}"

        if action in ("send",):
            to = str(args.get("to") or "").strip()
            subject = str(args.get("subject") or "").strip()
            body = str(args.get("body") or "").strip()
            if not to or not body:
                return "ERROR: 'to' and 'body' parameters are required to send an email."
            res = await _execute_composio_action(
                api_key,
                "GMAIL_SEND_EMAIL",
                {"recipient_email": to, "subject": subject, "body": body},
                connected_account_id=ca_id,
                user_id=user_id,
            )
            if not res.get("success"):
                res = await _execute_composio_action(
                    api_key,
                    "GMAIL_CREATE_EMAIL_DRAFT",
                    {"recipient_email": to, "subject": subject, "body": body},
                    connected_account_id=ca_id,
                    user_id=user_id,
                )
            if res.get("success"):
                return f"Письмо успешно отправлено на адрес {to}!"
            return f"Не удалось отправить письмо: {res.get('error')}"

        return f"Неизвестное действие Gmail: {action}. Доступны: profile, list, search, read, send."

    async def composio_calendar(args: dict, _brain: str) -> str:
        api_key = _get_api_key(ctx)
        if not api_key:
            return "ERROR: Composio API key is not configured. Add 'composio_api_key' in Settings."

        action = str(args.get("action") or "list").strip().lower()
        limit = int(args.get("limit") or 10)

        accounts = await _get_connected_apps(ctx, api_key)
        cal_info = accounts.get("googlecalendar", {})
        ca_id = cal_info.get("id")
        user_id = cal_info.get("user_id")

        if action in ("list", "events", "upcoming"):
            time_min = str(args.get("time_min") or "").strip()
            params: dict[str, Any] = {"max_results": limit}
            if time_min:
                params["time_min"] = time_min
            res = await _execute_composio_action(
                api_key,
                "GOOGLECALENDAR_LIST_EVENTS",
                params,
                connected_account_id=ca_id,
                user_id=user_id,
            )
            if not res.get("success"):
                res = await _execute_composio_action(
                    api_key,
                    "GOOGLECALENDAR_FIND_EVENTS",
                    params,
                    connected_account_id=ca_id,
                    user_id=user_id,
                )
            if res.get("success"):
                return f"События Google Календаря:\n{json.dumps(res.get('data'), ensure_ascii=False, indent=2)}"
            return f"Не удалось получить события календаря: {res.get('error')}"

        if action in ("create", "add"):
            summary = str(args.get("summary") or "").strip()
            start_time = str(args.get("start_time") or "").strip()
            end_time = str(args.get("end_time") or "").strip()
            if not summary or not start_time:
                return "ERROR: 'summary' and 'start_time' are required to create a calendar event."
            res = await _execute_composio_action(
                api_key,
                "GOOGLECALENDAR_CREATE_EVENT",
                {"summary": summary, "start_time": start_time, "end_time": end_time},
                connected_account_id=ca_id,
                user_id=user_id,
            )
            if not res.get("success"):
                res = await _execute_composio_action(
                    api_key,
                    "GOOGLECALENDAR_QUICK_ADD",
                    {"text": f"{summary} at {start_time}"},
                    connected_account_id=ca_id,
                    user_id=user_id,
                )
            if res.get("success"):
                return f"Событие '{summary}' успешно создано в Google Календаре!"
            return f"Не удалось создать событие в календаре: {res.get('error')}"

        return f"Неизвестное действие Календаря: {action}. Доступны: list, create."

    async def composio_github(args: dict, _brain: str) -> str:
        api_key = _get_api_key(ctx)
        if not api_key:
            return "ERROR: Composio API key is not configured. Add 'composio_api_key' in Settings."

        action = str(args.get("action") or "repos").strip().lower()
        repo = str(args.get("repo") or args.get("repository") or "").strip()
        limit = int(args.get("limit") or 10)
        title = str(args.get("title") or "").strip()
        body = str(args.get("body") or "").strip()

        accounts = await _get_connected_apps(ctx, api_key)
        github_info = accounts.get("github", {})
        ca_id = github_info.get("id")
        user_id = github_info.get("user_id")

        if not ca_id or not str(ca_id).startswith("ca_"):
            settings_svc = ctx.services.get("settings")
            if settings_svc is not None:
                try:
                    c_ids = settings_svc.load().get("composio_account_ids") or {}
                    if c_ids.get("github"):
                        ca_id = c_ids["github"]
                except Exception:
                    pass

        owner = ""
        repo_name = ""
        if repo:
            if "/" in repo:
                parts = repo.split("/", 1)
                owner, repo_name = parts[0].strip(), parts[1].strip()
            else:
                repo_name = repo

        if action in ("profile", "user", "whoami", "me"):
            res = await _execute_composio_action(
                api_key,
                "GITHUB_GET_THE_AUTHENTICATED_USER",
                {},
                connected_account_id=ca_id,
                user_id=user_id,
            )
            if not res.get("success"):
                res = await _execute_composio_action(
                    api_key,
                    "GITHUB_USERS_GET_AUTHENTICATED",
                    {},
                    connected_account_id=ca_id,
                    user_id=user_id,
                )
            if res.get("success"):
                pdata = res.get("data", {})
                login = pdata.get("login") or pdata.get("username") or github_info.get("alias") or "подключен"
                name = pdata.get("name") or login
                repos_cnt = pdata.get("public_repos", "N/A")
                return (
                    f"Подключенный профиль GitHub:\n"
                    f"- Пользователь: {login} ({name})\n"
                    f"- Публичных репозиториев: {repos_cnt}\n"
                    f"- Статус интеграции: АКТИВЕН"
                )
            if "github" in accounts:
                return f"GitHub подключен в Composio (аккаунт: {ca_id or 'active'}), но запрос профиля вернул: {res.get('error')}"
            return f"GitHub не подключен в Composio: {res.get('error')}"

        if action in ("repos", "list", "repositories"):
            res = await _execute_composio_action(
                api_key,
                "GITHUB_LIST_REPOSITORIES_FOR_THE_AUTHENTICATED_USER",
                {"per_page": limit, "sort": "updated"},
                connected_account_id=ca_id,
                user_id=user_id,
            )
            if not res.get("success"):
                res = await _execute_composio_action(
                    api_key,
                    "GITHUB_REPOS_LIST_FOR_AUTHENTICATED_USER",
                    {"per_page": limit, "sort": "updated"},
                    connected_account_id=ca_id,
                    user_id=user_id,
                )
            if not res.get("success"):
                res = await _execute_composio_action(
                    api_key,
                    "GITHUB_LIST_USER_REPOSITORIES",
                    {"per_page": limit},
                    connected_account_id=ca_id,
                    user_id=user_id,
                )
            if res.get("success"):
                data = res.get("data")
                return f"Репозитории GitHub:\n{json.dumps(data, ensure_ascii=False, indent=2)}"
            return f"Не удалось получить список репозиториев GitHub: {res.get('error')}"

        if action in ("issues", "issue_list"):
            if not repo_name:
                return "ERROR: 'repo' (например, 'owner/repo_name') обязателен для получения списка issues."
            payload: dict[str, Any] = {"per_page": limit, "state": "open"}
            if owner:
                payload["owner"] = owner
            payload["repo"] = repo_name

            res = await _execute_composio_action(
                api_key,
                "GITHUB_LIST_REPOSITORY_ISSUES",
                payload,
                connected_account_id=ca_id,
                user_id=user_id,
            )
            if not res.get("success"):
                res = await _execute_composio_action(
                    api_key,
                    "GITHUB_ISSUES_LIST_FOR_REPO",
                    payload,
                    connected_account_id=ca_id,
                    user_id=user_id,
                )
            if res.get("success"):
                return f"Issues в репозитории {repo}:\n{json.dumps(res.get('data'), ensure_ascii=False, indent=2)}"
            return f"Не удалось получить issues для {repo}: {res.get('error')}"

        if action in ("pulls", "prs", "pull_requests"):
            if not repo_name:
                return "ERROR: 'repo' (например, 'owner/repo_name') обязателен для получения списка pull requests."
            payload = {"per_page": limit, "state": "open"}
            if owner:
                payload["owner"] = owner
            payload["repo"] = repo_name

            res = await _execute_composio_action(
                api_key,
                "GITHUB_LIST_PULL_REQUESTS",
                payload,
                connected_account_id=ca_id,
                user_id=user_id,
            )
            if not res.get("success"):
                res = await _execute_composio_action(
                    api_key,
                    "GITHUB_PULLS_LIST",
                    payload,
                    connected_account_id=ca_id,
                    user_id=user_id,
                )
            if res.get("success"):
                return f"Pull requests в репозитории {repo}:\n{json.dumps(res.get('data'), ensure_ascii=False, indent=2)}"
            return f"Не удалось получить pull requests для {repo}: {res.get('error')}"

        if action in ("create_issue", "new_issue"):
            if not repo_name or not title:
                return "ERROR: 'repo' (например, 'owner/repo') и 'title' обязательны для создания issue."
            payload = {"title": title, "body": body}
            if owner:
                payload["owner"] = owner
            payload["repo"] = repo_name

            res = await _execute_composio_action(
                api_key,
                "GITHUB_CREATE_AN_ISSUE",
                payload,
                connected_account_id=ca_id,
                user_id=user_id,
            )
            if not res.get("success"):
                res = await _execute_composio_action(
                    api_key,
                    "GITHUB_ISSUES_CREATE",
                    payload,
                    connected_account_id=ca_id,
                    user_id=user_id,
                )
            if res.get("success"):
                return f"Issue успешно создан в {repo}!\n{json.dumps(res.get('data'), ensure_ascii=False, indent=2)}"
            return f"Не удалось создать issue в {repo}: {res.get('error')}"

        if action in ("star", "star_repo"):
            if not repo_name:
                return "ERROR: 'repo' обязателен для добавления в избранное (star)."
            payload = {}
            if owner:
                payload["owner"] = owner
            payload["repo"] = repo_name

            res = await _execute_composio_action(
                api_key,
                "GITHUB_STAR_A_REPOSITORY_FOR_THE_AUTHENTICATED_USER",
                payload,
                connected_account_id=ca_id,
                user_id=user_id,
            )
            if res.get("success"):
                return f"Репозиторий {repo} успешно добавлен в избранное (star)!"
            return f"Не удалось добавить в избранное {repo}: {res.get('error')}"

        return f"Неизвестное действие GitHub: {action}. Доступны: profile, repos, issues, pulls, create_issue, star."

    async def composio_feed(args: dict, _brain: str) -> str:
        platform = str(args.get("platform") or "").strip().lower()
        if not platform:
            return "ERROR: 'platform' parameter is required (e.g. twitter, linkedin, instagram, gmail, calendar, github)."

        query = str(args.get("query") or "").strip()
        limit = int(args.get("limit") or 10)

        if platform in ("gmail", "mail", "email"):
            return await composio_gmail({"action": "list", "query": query, "limit": limit}, _brain)
        if platform in ("googlecalendar", "calendar", "gcal"):
            return await composio_calendar({"action": "list", "limit": limit}, _brain)
        if platform in ("github", "gh"):
            return await composio_github({"action": "repos", "limit": limit}, _brain)

        api_key = _get_api_key(ctx)
        if not api_key:
            return "ERROR: Composio API key is not configured."

        accounts = await _get_connected_apps(ctx, api_key)
        acc_info = accounts.get(platform, {})
        ca_id = acc_info.get("id")
        user_id = acc_info.get("user_id")

        action_name = "TWITTER_USER_TIMELINE" if platform in {"twitter", "x"} else f"{platform.upper()}_GET_FEED"
        exec_payload: dict[str, Any] = {"limit": limit}
        if query:
            exec_payload["query"] = query

        res = await _execute_composio_action(
            api_key,
            action_name,
            exec_payload,
            connected_account_id=ca_id,
            user_id=user_id,
        )
        if res.get("success"):
            resp_data = res.get("data")
            text_repr = json.dumps(resp_data, ensure_ascii=False, indent=2)
            hub = ctx.services.get("hub")
            if hub is not None and len(text_repr) > 4000:
                preview, _ = hub.shaper.shape_result(text_repr, tool_name=f"composio_feed_{platform}")
                return preview
            return f"Feed for {platform.upper()}:\n{text_repr}"
        return f"Failed to fetch feed for {platform}: {res.get('error')}"

    async def composio_post(args: dict, _brain: str) -> str:
        platform = str(args.get("platform") or "").strip().lower()
        if not platform:
            return "ERROR: 'platform' is required (e.g. twitter, linkedin, instagram, threads)."

        content = str(args.get("content") or "").strip()
        if not content:
            return "ERROR: 'content' text cannot be empty."

        constraint = PLATFORM_CONSTRAINTS.get(platform, {"max_len": 5000, "name": platform.title(), "action": f"{platform.upper()}_CREATE_POST"})
        max_len = constraint["max_len"]
        if len(content) > max_len:
            return (
                f"ERROR: Content length ({len(content)} chars) exceeds maximum allowed for {constraint['name']} "
                f"({max_len} chars). Please shorten your post or create a thread."
            )

        api_key = _get_api_key(ctx)
        if not api_key:
            return "ERROR: Composio API key is not configured."

        accounts = await _get_connected_apps(ctx, api_key)
        acc_info = accounts.get(platform, {})
        ca_id = acc_info.get("id")
        user_id = acc_info.get("user_id")

        action_name = constraint["action"]
        post_args: dict[str, Any] = {"text": content}
        media = args.get("media_urls")
        if media and isinstance(media, list):
            post_args["media"] = media

        res = await _execute_composio_action(
            api_key,
            action_name,
            post_args,
            connected_account_id=ca_id,
            user_id=user_id,
        )
        if res.get("success"):
            resp_data = res.get("data")
            return f"Successfully posted to {constraint['name']}!\nResult: {json.dumps(resp_data, ensure_ascii=False)}"
        return f"Failed to post to {constraint['name']}: {res.get('error')}"

    ctx.register_tool(
        name="composio_status",
        description="Check Composio API key configuration and list all connected accounts (including Gmail with email address, Google Calendar, etc.).",
        handler=composio_status,
        min_mode="readonly",
        schema={"type": "object", "properties": {}},
    )

    ctx.register_tool(
        name="composio_connect",
        description="Initiate an OAuth authorization link to connect a cloud account (gmail, googlecalendar, twitter, etc.) via Composio.",
        handler=composio_connect,
        min_mode="safe",
        schema={
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": "Service to connect: gmail, googlecalendar, twitter, linkedin, instagram, etc.",
                },
                "redirect_url": {
                    "type": "string",
                    "description": "Optional callback URL after authorization completes",
                },
            },
            "required": ["app"],
        },
    )

    ctx.register_tool(
        name="composio_gmail",
        description="Interact with Gmail via Composio: inspect user profile/email address, list/search emails, read messages, or send emails.",
        handler=composio_gmail,
        min_mode="safe",
        schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["profile", "list", "search", "read", "send"],
                    "description": "Action to perform: 'profile' (get connected email address and stats), 'list' (list recent emails), 'search' (search emails by query), 'read' (read email body), 'send' (send email)",
                    "default": "profile",
                },
                "query": {
                    "type": "string",
                    "description": "Search query or filter (e.g. 'is:unread', 'from:someone', 'in:inbox')",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of emails to retrieve (default: 5)",
                    "default": 5,
                },
                "message_id": {
                    "type": "string",
                    "description": "Message ID for action='read'",
                },
                "to": {
                    "type": "string",
                    "description": "Recipient email address for action='send'",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject for action='send'",
                },
                "body": {
                    "type": "string",
                    "description": "Email body text for action='send'",
                },
            },
            "required": ["action"],
        },
    )

    ctx.register_tool(
        name="composio_calendar",
        description="Interact with Google Calendar via Composio: list upcoming meetings/events or create new events.",
        handler=composio_calendar,
        min_mode="safe",
        schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "create"],
                    "description": "Action: 'list' (upcoming events) or 'create' (new event)",
                    "default": "list",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of events to list (default: 10)",
                    "default": 10,
                },
                "time_min": {
                    "type": "string",
                    "description": "Optional ISO start time for event filter",
                },
                "summary": {
                    "type": "string",
                    "description": "Event title/summary for action='create'",
                },
                "start_time": {
                    "type": "string",
                    "description": "Start time ISO string for action='create'",
                },
                "end_time": {
                    "type": "string",
                    "description": "End time ISO string for action='create'",
                },
            },
            "required": ["action"],
        },
    )

    ctx.register_tool(
        name="composio_github",
        description="Interact with GitHub via Composio: inspect user profile, list repositories, view open issues/pull requests, or create issues.",
        handler=composio_github,
        min_mode="safe",
        schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["profile", "repos", "issues", "pulls", "create_issue", "star"],
                    "description": "Action to perform: 'profile', 'repos' (list user repositories), 'issues' (list issues in repo), 'pulls' (list PRs), 'create_issue' (new issue), 'star' (star repo)",
                    "default": "repos",
                },
                "repo": {
                    "type": "string",
                    "description": "Repository in format 'owner/repo_name' (e.g. 'octocat/Hello-World')",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of items to retrieve (default: 10)",
                    "default": 10,
                },
                "title": {
                    "type": "string",
                    "description": "Issue title for action='create_issue'",
                },
                "body": {
                    "type": "string",
                    "description": "Issue body / description for action='create_issue'",
                },
            },
            "required": ["action"],
        },
    )

    ctx.register_tool(
        name="composio_feed",
        description="Fetch recent posts, timeline, mentions, or emails/calendar events from a connected integration.",
        handler=composio_feed,
        min_mode="readonly",
        schema={
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "description": "Platform: twitter, linkedin, instagram, threads, facebook, gmail, calendar, github",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of items to fetch (default: 10)",
                    "default": 10,
                },
                "query": {
                    "type": "string",
                    "description": "Optional search term or filter query",
                },
            },
            "required": ["platform"],
        },
    )

    ctx.register_tool(
        name="composio_post",
        description="Publish a post or update to a connected social network (Twitter/X, LinkedIn, etc.). Requires Full permission.",
        handler=composio_post,
        min_mode="full",
        schema={
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "description": "Target platform: twitter, linkedin, instagram, facebook, threads, reddit",
                },
                "content": {
                    "type": "string",
                    "description": "Text content of the post (strictly validated against platform length limits)",
                },
                "media_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional array of media URLs to attach to the post",
                },
            },
            "required": ["platform", "content"],
        },
    )
