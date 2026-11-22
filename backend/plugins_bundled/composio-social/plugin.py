"""Bundled plugin: composio-social — unified social media management via Composio.

Provides:
- composio_status (readonly): inspect API key status and list connected social accounts.
- composio_connect (safe): initiate OAuth connection flow for Twitter, LinkedIn, Instagram, etc.
- composio_feed (readonly): fetch recent posts, mentions, and analytics from connected networks.
- composio_post (full): publish posts, threads, or updates with strict platform constraints.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

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


def _get_api_key(ctx) -> str:
    settings = ctx.services.get("settings")
    if settings is not None:
        cfg = settings.load()
        key = str(cfg.get("composio_api_key") or "").strip()
        if key:
            return key
    return ""


def register(ctx) -> None:
    async def composio_status(_args: dict, _brain: str) -> str:
        api_key = _get_api_key(ctx)
        if not api_key:
            return (
                "Composio API key is NOT configured. "
                "Please configure 'composio_api_key' in Settings or Connections to use social tools."
            )

        # 1. Try Composio Remote MCP tool first
        hub = ctx.services.get("hub")
        if hub is not None:
            try:
                import re
                raw = await hub.call_tool(
                    name="mcp__composio__COMPOSIO_MANAGE_CONNECTIONS",
                    args={"toolkits": ["instagram", "facebook", "linkedin", "twitter", "threads", "github", "gmail", "googlesheets"]},
                    brain=_brain,
                    run_mode="full",
                    for_model=False,
                )
                data = json.loads(raw)
                results = data.get("data", {}).get("results", {})
                connected = []
                for app_name, app_info in results.items():
                    if not isinstance(app_info, dict):
                        continue
                    status = app_info.get("status") or "disconnected"
                    accs = app_info.get("accounts") or []
                    if status == "active" or any(a.get("status") == "ACTIVE" for a in accs):
                        connected.append(f"- {app_name.upper()} (status: ACTIVE)")
                    elif accs:
                        connected.append(f"- {app_name.upper()} (status: {accs[0].get('status', 'PENDING')})")
                if connected:
                    return "Active Composio connections:\n" + "\n".join(connected)
                return (
                    "Composio Remote MCP is active (valid consumer key). No social accounts are connected yet.\n"
                    "Use composio_connect(app='instagram' | 'facebook' | 'linkedin' | 'twitter') to link an account."
                )
            except Exception:
                pass

        # 2. Check connected accounts via Composio REST API fallback
        headers = {"x-api-key": api_key, "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(f"{COMPOSIO_API_BASE}/connected_accounts", headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    items = data.get("items") or data.get("connected_accounts") or data.get("data") or []
                    if not items:
                        return (
                            "Composio API key is valid, but no social accounts are connected yet. "
                            "Use composio_connect(app='twitter' | 'linkedin' | 'instagram') to link an account."
                        )
                    connected = []
                    for it in items:
                        raw_app = (
                            it.get("appUniqueId")
                            or it.get("appName")
                            or it.get("app_name")
                            or it.get("app")
                            or it.get("toolkit")
                            or "unknown"
                        )
                        if isinstance(raw_app, dict):
                            raw_app = raw_app.get("slug") or raw_app.get("name") or "unknown"
                        app_name = str(raw_app).strip()
                        status = it.get("status", "ACTIVE")
                        account_id = it.get("id") or it.get("nanoid") or it.get("accountId") or "id"
                        connected.append(f"- {app_name.upper()} (status: {status}, id: {account_id})")
                    return "Active Composio connections:\n" + "\n".join(connected)
                elif res.status_code in {401, 403}:
                    return f"ERROR: Composio authentication failed ({res.status_code}). Please verify your composio_api_key."
                else:
                    return f"Composio returned status {res.status_code}: {res.text[:200]}"
        except Exception as exc:
            return f"ERROR: Failed to contact Composio API: {exc}"

    async def composio_connect(args: dict, _brain: str) -> str:
        app = str(args.get("app") or args.get("platform") or "").strip().lower()
        if not app:
            return "ERROR: 'app' parameter is required (e.g. twitter, linkedin, instagram, facebook, youtube, threads, reddit)."

        api_key = _get_api_key(ctx)
        if not api_key:
            return "ERROR: Composio API key is not configured. Add 'composio_api_key' in Settings."

        # Special case for Twitter / X setup
        if app in ("twitter", "x"):
            return (
                "Twitter / X Connection:\n"
                "Composio requires custom developer credentials for Twitter on standard accounts.\n"
                "Please configure Twitter in Composio dashboard here:\n"
                "https://dashboard.composio.dev/~/org/connect/apps/twitter?open=true\n"
                "Once completed, run composio_status to confirm the connection."
            )

        # 1. Try Composio Remote MCP tool first
        hub = ctx.services.get("hub")
        if hub is not None:
            try:
                import re
                raw = await hub.call_tool(
                    name="mcp__composio__COMPOSIO_MANAGE_CONNECTIONS",
                    args={"toolkits": [app]},
                    brain=_brain,
                    run_mode="full",
                    for_model=False,
                )
                data = json.loads(raw)
                res_info = data.get("data", {}).get("results", {}).get(app, {})
                auth_url = res_info.get("redirect_url") or ""
                if not auth_url and "dashboard.composio.dev" in res_info.get("error_message", ""):
                    m = re.search(r"https://dashboard\.composio\.dev/[^\s\)\>]+", res_info["error_message"])
                    if m:
                        auth_url = m.group(0)
                if auth_url:
                    return (
                        f"OAuth connection initiated for {app.upper()}.\n"
                        f"Open this URL in your browser to authorize access:\n{auth_url}\n"
                        f"After authorization, run composio_status to confirm the connection."
                    )
            except Exception:
                pass

        # 2. Fallback to direct REST API
        redirect_url = str(args.get("redirect_url") or "http://127.0.0.1:7777/hub").strip()
        headers = {"x-api-key": api_key, "Content-Type": "application/json"}
        payload = {
            "appName": app,
            "redirectUrl": redirect_url,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(f"{COMPOSIO_API_BASE}/connected_accounts/link", headers=headers, json={"user_id": "default", "auth_config_id": app, "redirect_url": redirect_url})
                if res.status_code in {200, 201}:
                    data = res.json()
                    auth_url = (
                        data.get("redirectUrl")
                        or data.get("connectionUrl")
                        or data.get("authUrl")
                        or data.get("url")
                        or data.get("link")
                    )
                    if auth_url:
                        return (
                            f"OAuth connection initiated for {app.upper()}.\n"
                            f"Open this URL in your browser to authorize access:\n{auth_url}\n"
                            f"After authorization, run composio_status to confirm the connection."
                        )
                    return f"Initiated connection: {json.dumps(data, ensure_ascii=False)}"
                # Fallback to direct /connected_accounts
                res_alt = await client.post(f"{COMPOSIO_API_BASE}/connected_accounts", headers=headers, json=payload)
                if res_alt.status_code in {200, 201}:
                    data_alt = res_alt.json()
                    auth_url = (
                        data_alt.get("redirectUrl")
                        or data_alt.get("connectionUrl")
                        or data_alt.get("authUrl")
                        or data_alt.get("url")
                    )
                    if auth_url:
                        return (
                            f"OAuth connection initiated for {app.upper()}.\n"
                            f"Open this URL in your browser to authorize access:\n{auth_url}\n"
                            f"After authorization, run composio_status to confirm the connection."
                        )
                    return f"Initiated connection: {json.dumps(data_alt, ensure_ascii=False)}"
                return f"ERROR from Composio ({res.status_code}): {res.text[:300]}"
        except Exception as exc:
            return f"ERROR: Failed to initiate connection for {app}: {exc}"

    async def composio_feed(args: dict, _brain: str) -> str:
        platform = str(args.get("platform") or "").strip().lower()
        if not platform:
            return "ERROR: 'platform' parameter is required (e.g. twitter, linkedin, instagram)."

        api_key = _get_api_key(ctx)
        if not api_key:
            return "ERROR: Composio API key is not configured."

        limit = int(args.get("limit") or 10)
        query = str(args.get("query") or "").strip()

        # Execute read action via Composio Action execution endpoint
        action_name = "TWITTER_USER_TIMELINE" if platform in {"twitter", "x"} else f"{platform.upper()}_GET_FEED"
        headers = {"x-api-key": api_key, "Content-Type": "application/json"}
        exec_payload: dict[str, Any] = {"arguments": {"limit": limit}}
        if query:
            exec_payload["arguments"]["query"] = query

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(
                    f"{COMPOSIO_API_BASE}/actions/{action_name}/execute",
                    headers=headers,
                    json=exec_payload,
                )
                if res.status_code in {200, 201}:
                    data = res.json()
                    resp_data = data.get("response_data") or data.get("data") or data
                    text_repr = json.dumps(resp_data, ensure_ascii=False, indent=2)
                    # If very large, shape with ToolResultShaper
                    hub = ctx.services.get("hub")
                    if hub is not None and len(text_repr) > 4000:
                        preview, _ = hub.shaper.shape_result(text_repr, tool_name=f"composio_feed_{platform}")
                        return preview
                    return f"Feed for {platform.upper()}:\n{text_repr}"
                return f"Composio returned ({res.status_code}): {res.text[:300]}"
        except Exception as exc:
            return f"ERROR: Failed to fetch feed for {platform}: {exc}"

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

        action_name = constraint["action"]
        headers = {"x-api-key": api_key, "Content-Type": "application/json"}
        post_args: dict[str, Any] = {"text": content}
        media = args.get("media_urls")
        if media and isinstance(media, list):
            post_args["media"] = media

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.post(
                    f"{COMPOSIO_API_BASE}/actions/{action_name}/execute",
                    headers=headers,
                    json={"arguments": post_args},
                )
                if res.status_code in {200, 201}:
                    data = res.json()
                    resp_data = data.get("response_data") or data.get("data") or data
                    return f"Successfully posted to {constraint['name']}!\nResult: {json.dumps(resp_data, ensure_ascii=False)}"
                return f"Failed to post to {constraint['name']} ({res.status_code}): {res.text[:300]}"
        except Exception as exc:
            return f"ERROR: Post execution failed on {platform}: {exc}"

    ctx.register_tool(
        name="composio_status",
        description="Check Composio API key configuration and list all connected social media accounts.",
        handler=composio_status,
        min_mode="readonly",
        schema={"type": "object", "properties": {}},
    )

    ctx.register_tool(
        name="composio_connect",
        description="Initiate an OAuth authorization link to connect a social media account via Composio.",
        handler=composio_connect,
        min_mode="safe",
        schema={
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": "Platform to connect: twitter, linkedin, instagram, facebook, youtube, threads, reddit",
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
        name="composio_feed",
        description="Fetch recent posts, timeline, mentions, or search feed from a connected social network.",
        handler=composio_feed,
        min_mode="readonly",
        schema={
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "description": "Social network platform: twitter, linkedin, instagram, threads, facebook",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of posts to fetch (default: 10)",
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
