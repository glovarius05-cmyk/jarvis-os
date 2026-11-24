"""Built-in Vault, Web (SSRF-guarded), Query, and Connections tools exposed through the MCP Hub.

Every engine (OpenRouter, Local Llama, Gemini API, Codex CLI, Antigravity CLI) gets
these exact same tools under the exact same permission matrix.
"""

from __future__ import annotations

import ipaddress
import json
from pathlib import Path
import re
import socket
from dataclasses import dataclass
from html import unescape
from typing import Any, Awaitable, Callable
from urllib.parse import quote_plus, urlparse

import httpx

from app.brain.vault import VaultManager, VaultPathError, safe_resolve
from app.hub.permissions import PermLevel
from app.hub.shaper import ToolResultStore, query_stored_result

ToolHandler = Callable[[dict[str, Any], str], Awaitable[str]]  # (args, brain) -> str


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    required_perm: PermLevel
    source: str
    handler: ToolHandler
    connection_perm: PermLevel = PermLevel.FULL
    shape_output: bool = True

    def to_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def to_mcp_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.parameters,
        }


def is_safe_public_url(url: str) -> tuple[bool, str]:
    """SSRF guard: allow only http/https URLs pointing to public, non-private IP addresses."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return False, f"Blocked URL scheme '{parsed.scheme}': only http/https allowed."
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False, "URL has no hostname."
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return False, f"Blocked private/local hostname: {host}"

    # Direct IP literal check
    try:
        ip_obj = ipaddress.ip_address(host)
        if not ip_obj.is_global:
            return False, f"Blocked non-public IP address: {host}"
        return True, ""
    except ValueError:
        pass

    # DNS resolution check (fail closed on private targets)
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        for info in infos:
            ip_str = info[4][0]
            ip_obj = ipaddress.ip_address(ip_str)
            if not ip_obj.is_global:
                return False, f"Blocked hostname resolving to non-public IP ({ip_str}): {host}"
    except OSError:
        # Allow DNS failure to surface normally as network error during fetch, rather than false SSRF
        pass
    return True, ""


_TAG_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_NL_RE = re.compile(r"\n{3,}")


def html_to_plain_text(html: str) -> str:
    cleaned = _TAG_RE.sub(" ", html or "")
    cleaned = re.sub(r"</?(p|div|br|li|h[1-6]|tr)[^>]*>", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = _HTML_TAG_RE.sub("", cleaned)
    cleaned = unescape(cleaned)
    cleaned = _WS_RE.sub(" ", cleaned)
    cleaned = _NL_RE.sub("\n\n", cleaned)
    return cleaned.strip()


def build_builtin_tools(
    *,
    vaults: VaultManager,
    result_store: ToolResultStore,
    list_connections_fn: Callable[[], list[dict[str, Any]]],
    connections_store: Any | None = None,
    mcp_pool: Any | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
    router_getter: Callable[[], Any] | None = None,
    experience_getter: Callable[[], Any] | None = None,
    core_memory_getter: Callable[[], Any] | None = None,
    hybrid_index_getter: Callable[[], Any] | None = None,
    character_getter: Callable[[], Any] | None = None,
    skills_getter: Callable[[], Any] | None = None,
) -> list[ToolDefinition]:
    """Build the core Vault, Web, Query, and Connections tools."""

    async def vault_read(args: dict[str, Any], brain: str) -> str:
        path = str(args.get("path") or "")
        start = max(1, int(args.get("start_line") or 1))
        max_lines = max(1, min(int(args.get("max_lines") or 200), 500))
        try:
            root = vaults.brain_path(brain)
            target = safe_resolve(root, path)
        except (ValueError, VaultPathError) as exc:
            return f"ERROR: {exc}"
        if not target.is_file():
            return f"ERROR: File not found: {path}"
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        chunk = lines[start - 1 : start - 1 + max_lines]
        numbered = "\n".join(f"{start + i}: {line}" for i, line in enumerate(chunk))
        return f"File: {path} (lines {start}-{start + len(chunk) - 1} of {len(lines)})\n{numbered}"

    async def vault_list(args: dict[str, Any], brain: str) -> str:
        rel = str(args.get("path") or "")
        try:
            root = vaults.brain_path(brain)
            target = safe_resolve(root, rel)
        except (ValueError, VaultPathError) as exc:
            return f"ERROR: {exc}"
        if not target.is_dir():
            return f"ERROR: Directory not found: {rel or '.'}"
        entries = []
        for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if item.name == ".git":
                continue
            suffix = "/" if item.is_dir() else f" ({item.stat().st_size} B)"
            entries.append(f"{item.name}{suffix}")
        return "\n".join(entries) if entries else "(empty directory)"

    async def vault_search(args: dict[str, Any], brain: str) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            return "ERROR: query is required."
        max_hits = max(1, min(int(args.get("max_hits") or 30), 100))
        root = vaults.brain_path(brain)
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        hits: list[str] = []
        for p in sorted(root.rglob("*")):
            if not p.is_file() or ".git" in p.parts:
                continue
            if p.suffix.lower() not in {".md", ".txt", ".yaml", ".yml", ".json"}:
                continue
            rel = p.relative_to(root).as_posix()
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for idx, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    hits.append(f"{rel}:{idx}: {line.strip()[:160]}")
                    if len(hits) >= max_hits:
                        return "\n".join(hits)
        return "\n".join(hits) if hits else "No matches found."

    async def vault_write(args: dict[str, Any], brain: str) -> str:
        path = str(args.get("path") or "").strip()
        content = str(args.get("content") or "")
        if not path:
            return "ERROR: path is required."
        try:
            root = vaults.brain_path(brain)
            target = safe_resolve(root, path)
            if ".git" in target.relative_to(root).parts:
                return "ERROR: Refusing to write inside .git directory."
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} chars to {path}"
        except (ValueError, VaultPathError, OSError) as exc:
            return f"ERROR: {exc}"

    async def vault_edit(args: dict[str, Any], brain: str) -> str:
        path = str(args.get("path") or "").strip()
        old_text = str(args.get("old_text") or "")
        new_text = str(args.get("new_text") or "")
        if not path or not old_text:
            return "ERROR: path and old_text are required."
        try:
            root = vaults.brain_path(brain)
            target = safe_resolve(root, path)
            if not target.is_file():
                return f"ERROR: File not found: {path}"
            current = target.read_text(encoding="utf-8")
            if old_text not in current:
                return "ERROR: old_text not found in file."
            updated = current.replace(old_text, new_text, 1)
            target.write_text(updated, encoding="utf-8")
            return f"Updated {path} (replaced 1 occurrence)."
        except (ValueError, VaultPathError, OSError) as exc:
            return f"ERROR: {exc}"

    async def web_fetch(args: dict[str, Any], _brain: str) -> str:
        url = str(args.get("url") or "").strip()
        ok, err = is_safe_public_url(url)
        if not ok:
            return f"ERROR: {err}"
        try:
            async with httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                transport=http_transport,
                headers={"User-Agent": "JarvisOS/0.1 (+self-hosted)"},
            ) as client:
                resp = await client.get(url)
                # Re-check final redirect target against SSRF
                final_ok, final_err = is_safe_public_url(str(resp.url))
                if not final_ok:
                    return f"ERROR: Redirect blocked: {final_err}"
                if resp.status_code >= 400:
                    return f"ERROR: HTTP {resp.status_code} fetching {url}"
                ctype = (resp.headers.get("content-type") or "").lower()
                text = resp.text
                if "html" in ctype:
                    text = html_to_plain_text(text)
                return f"URL: {resp.url}\n\n{text}"
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: web_fetch failed: {exc}"

    async def web_search(args: dict[str, Any], _brain: str) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            return "ERROR: query is required."
        max_results = max(1, min(int(args.get("max_results") or 5), 10))
        search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            async with httpx.AsyncClient(
                timeout=12.0,
                transport=http_transport,
                headers={"User-Agent": "Mozilla/5.0 (compatible; JarvisOS/0.1)"},
            ) as client:
                resp = await client.get(search_url)
                if resp.status_code >= 400:
                    return f"ERROR: Search HTTP {resp.status_code}"
                matches = re.findall(
                    r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                    resp.text,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                results = []
                for href, raw_title in matches[:max_results]:
                    title = html_to_plain_text(raw_title)
                    results.append(f"- {title}: {unescape(href)}")
                return "\n".join(results) if results else "No search results found."
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: web_search failed: {exc}"

    async def jarvis_browse_url(args: dict[str, Any], brain: str) -> str:
        from app.hub.web_reader import fetch_and_extract_webpage, save_webpage_to_vault
        url = str(args.get("url") or "").strip()
        if not url:
            return "ERROR: url is required."
        max_chars = max(500, min(int(args.get("max_chars") or 8000), 40000))
        save_to_vault = bool(args.get("save_to_vault", False))
        vault_path = str(args.get("vault_path") or "").strip()

        try:
            page = await fetch_and_extract_webpage(url, transport=http_transport, timeout=15.0)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: Failed to browse {url}: {exc}"

        saved_msg = ""
        if save_to_vault and brain:
            try:
                rel_path = save_webpage_to_vault(vaults, brain, page, target_path=vault_path)
                saved_msg = f"\n[Saved to Second Brain vault: `{rel_path}`]\n"
            except Exception as exc:  # noqa: BLE001
                saved_msg = f"\n[Vault save warning: {exc}]\n"

        content = page.content_markdown
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n...[Content truncated at {max_chars} chars. Use max_chars or save_to_vault=True to inspect full text]..."

        meta_lines = [f"# {page.title or url}", f"Source: {page.url}"]
        if page.author:
            meta_lines.append(f"Author: {page.author}")
        if page.description:
            meta_lines.append(f"Description: {page.description}")
        if saved_msg:
            meta_lines.append(saved_msg.strip())

        header = "\n".join(meta_lines)
        return f"{header}\n\n---\n\n{content}"

    async def jarvis_browser_task_fn(args: dict[str, Any], _brain: str) -> str:
        from app.hub.browser_agent import run_browser_task

        task = str(args.get("task") or "").strip()
        url = str(args.get("url") or "").strip()
        if not task:
            return "ERROR: task is required."
        max_steps = max(1, min(int(args.get("max_steps") or 10), 30))
        headless = bool(args.get("headless", True))

        cfg: dict[str, Any] = {}
        if router_getter is not None:
            try:
                r = router_getter()
                if hasattr(r, "_settings"):
                    cfg = r._settings.load()
            except Exception:
                cfg = {}

        res = await run_browser_task(
            task=task,
            url=url,
            max_steps=max_steps,
            headless=headless,
            settings=cfg,
        )
        if not res.get("ok"):
            return f"ERROR: Browser task failed ({res.get('backend', 'none')}): {res.get('error', 'unknown error')}"

        backend = res.get("backend", "unknown")
        if backend == "browser_use":
            return f"[Browser Agent ({backend})]\nTask: {task}\nResult:\n{res.get('result', '')}"
        elif backend == "playwright":
            return f"[Browser Agent ({backend})]\nURL: {res.get('url')}\nTitle: {res.get('title')}\nContent:\n{res.get('content', '')}"
        else:
            return f"[Browser Agent (HTTP Fallback)]\nURL: {res.get('url')}\nTitle: {res.get('title')}\n{res.get('note', '')}\n\nContent:\n{res.get('content', '')}"

    async def jarvis_browser_navigate_fn(args: dict[str, Any], _brain: str) -> str:
        from app.hub.browser_agent import run_browser_task

        url = str(args.get("url") or "").strip()
        if not url:
            return "ERROR: url is required."

        res = await run_browser_task(
            task=f"Navigate to {url} and extract page summary",
            url=url,
            headless=True,
        )
        if not res.get("ok"):
            return f"ERROR: Browser navigation failed: {res.get('error', 'unknown error')}"

        content = res.get("content") or res.get("result") or ""
        title = res.get("title", "")
        return f"[Browser Navigation ({res.get('backend')})]\nURL: {url}\nTitle: {title}\n\n{content}"

    async def jarvis_query(args: dict[str, Any], brain: str) -> str:
        ref_id = str(args.get("ref_id") or "").strip()
        if not ref_id:
            return "ERROR: ref_id is required."
        return query_stored_result(
            result_store,
            brain=brain,
            ref_id=ref_id,
            sql=str(args.get("sql") or ""),
            fields=args.get("fields") if isinstance(args.get("fields"), list) else None,
            offset=int(args.get("offset") or 0),
            limit=int(args.get("limit") or 20),
        )

    async def jarvis_connections(args: dict[str, Any], _brain: str) -> str:
        action = str(args.get("action") or "list").strip().lower()
        target_id = str(args.get("conn_id") or args.get("id") or "").strip().lower()

        if action in ("delete", "remove", "disconnect") and target_id:
            if connections_store is None:
                return "ERROR: Connection store is not available."
            deleted = connections_store.delete(target_id)
            if mcp_pool is not None:
                mcp_pool.invalidate(target_id)
            if deleted:
                return f"MCP-соединение '{target_id}' успешно удалено."
            return f"MCP-соединение '{target_id}' не найдено."

        if action == "test" and target_id:
            if connections_store is None or mcp_pool is None:
                return "ERROR: MCP client pool is not available."
            conn = connections_store.get(target_id)
            if not conn:
                return f"ERROR: MCP-соединение '{target_id}' не найдено."
            try:
                raw_tools = await mcp_pool.test_connection(conn)
                return (
                    f"Проверка подключения '{target_id}' успешна!\n"
                    f"- Статус: активен\n"
                    f"- Найдено инструментов: {len(raw_tools)}\n"
                    f"- Доступные команды: {', '.join(t.get('name', '') for t in raw_tools[:8])}"
                )
            except Exception as ex:
                return f"Ошибка при проверке MCP-соединения '{target_id}': {ex}"

        conns = list_connections_fn()
        if not conns:
            return "Внешние MCP-соединения пока не настроены. Используйте 'jarvis_mcp_connect' для подключения."
        lines = [
            f"- {c['id']} ({c['name']}): transport={c['transport']}, perm={c['perm']}, enabled={c['enabled']}"
            for c in conns
        ]
        return "\n".join(lines)

    async def jarvis_mcp_connect(args: dict[str, Any], _brain: str) -> str:
        if connections_store is None:
            return "ERROR: MCP connection store is not available."

        conn_id = str(args.get("id") or "").strip().lower()
        if not conn_id:
            return "ERROR: 'id' parameter is required (e.g. 'brave-search', 'context7', 'github', 'custom-mcp')."

        conn_id = re.sub(r"[^a-z0-9_-]", "-", conn_id)

        name = str(args.get("name") or "").strip() or conn_id.replace("-", " ").title()
        transport = str(args.get("transport") or "").strip().lower()
        url = str(args.get("url") or "").strip()
        api_key = str(args.get("api_key") or args.get("token") or args.get("key") or "").strip()
        header_name = str(args.get("header_name") or "").strip()
        command = str(args.get("command") or "").strip()
        cmd_args = args.get("args") if isinstance(args.get("args"), list) else []
        env_var = str(args.get("env_var") or "").strip()
        perm = str(args.get("perm") or "safe").strip().lower()
        if perm not in ("readonly", "safe", "full"):
            perm = "safe"

        # Catalog preset lookup if preset matches
        code_dir = Path(__file__).resolve().parent.parent.parent
        cat_path = code_dir / "system" / "mcp-catalog.json"
        if cat_path.exists():
            try:
                cdata = json.loads(cat_path.read_text(encoding="utf-8"))
                for cat in cdata.get("connectors", []):
                    cid = str(cat.get("id") or "").lower()
                    if cid == conn_id or conn_id in cid or cid in conn_id:
                        if not transport and cat.get("transport"):
                            transport = cat["transport"]
                        if not url and cat.get("default_url"):
                            url = cat["default_url"]
                        if not command and cat.get("default_command"):
                            command = cat["default_command"]
                        if not cmd_args and cat.get("default_args"):
                            cmd_args = cat["default_args"]
                        if not env_var and cat.get("env_var"):
                            env_var = cat["env_var"]
                        if not header_name and cat.get("header_name"):
                            header_name = cat["header_name"]
                        if name == conn_id.replace("-", " ").title() and cat.get("name"):
                            name = cat["name"]
                        break
            except Exception:
                pass

        if not header_name:
            header_name = "Authorization"
        if not transport:
            transport = "http" if (url or not command) else "stdio"

        headers: dict[str, str] = {}
        env: dict[str, str] = {}

        if transport == "http":
            if not url:
                return f"ERROR: 'url' is required for HTTP MCP server '{conn_id}'."
            if api_key:
                if header_name.lower() == "authorization" and not api_key.lower().startswith("bearer "):
                    headers[header_name] = f"Bearer {api_key}"
                else:
                    headers[header_name] = api_key
        else:
            if not command:
                return f"ERROR: 'command' is required for Stdio MCP server '{conn_id}' (e.g. 'npx')."
            if api_key:
                env_var_name = env_var or f"{conn_id.upper().replace('-', '_')}_API_KEY"
                env[env_var_name] = api_key

        from app.hub.mcp_client import MCPConnection
        conn = MCPConnection(
            id=conn_id,
            name=name,
            transport=transport,
            perm=perm,
            enabled=True,
            url=url,
            headers=headers,
            command=command,
            args=[str(a) for a in cmd_args],
            env=env,
        )

        tools_discovered = []
        if mcp_pool is not None:
            try:
                tools_discovered = await mcp_pool.test_connection(conn)
            except Exception as ex:
                return (
                    f"Не удалось подключиться к MCP-серверу '{name}' ({conn_id}):\n"
                    f"Ошибка верификации: {ex}\n"
                    f"Проверьте URL, API-ключ и параметры соединения."
                )

        try:
            saved = connections_store.upsert(conn)
            if mcp_pool is not None:
                mcp_pool.invalidate(saved.id)
                if not tools_discovered:
                    try:
                        defs = await mcp_pool._discover_one(saved)
                        tools_discovered = [{"name": d.name.split("__")[-1], "description": d.description} for d in defs]
                    except Exception:
                        pass
        except Exception as exc:
            return f"ERROR: Failed to save MCP connection: {exc}"

        tool_lines = []
        for t in tools_discovered[:10]:
            tname = t.get("name") if isinstance(t, dict) else getattr(t, "name", str(t))
            tdesc = t.get("description", "") if isinstance(t, dict) else getattr(t, "description", "")
            tool_lines.append(f"- `mcp__{conn_id}__{tname}`: {tdesc[:80]}")
        if len(tools_discovered) > 10:
            tool_lines.append(f"... и еще {len(tools_discovered) - 10} инструментов.")

        res_msg = [
            f"MCP-сервер '{name}' (ID: `{conn_id}`) успешно подключен и сохранен!",
            f"- Протокол: `{transport}`",
            f"- Уровень доступа: `{perm}`",
            f"- Аутентификация: {'API-ключ сохранен (Fernet-шифрование)' if api_key else 'Без аутентификации'}",
            f"- Обнаружено инструментов: {len(tools_discovered)}",
        ]
        if tool_lines:
            res_msg.append("\nДоступные инструменты:")
            res_msg.extend(tool_lines)
        res_msg.append("\nВсе инструменты готовы к использованию в чате!")
        return "\n".join(res_msg)

    async def jarvis_consensus(args: dict[str, Any], _brain: str) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            return "ERROR: query is required."
        router = router_getter() if router_getter else None
        if not router:
            return "ERROR: Engine router is not available."
        from app.engines.base import EngineRequest
        from app.engines.consensus import ConsensusEngine

        cfg = router.settings.load()
        prim_p, prim_m = router.resolve_target()
        sec_p, sec_m = router.resolve_secondary_target(prim_p, cfg)
        engine = ConsensusEngine(
            primary_provider=prim_p,
            primary_model=prim_m,
            secondary_provider=sec_p,
            secondary_model=sec_m,
            router=router,
            mode="consensus",
        )
        req = EngineRequest(
            session_id="consensus",
            brain=_brain or "Brain Default",
            provider=prim_p,
            model=prim_m,
            messages=[{"role": "user", "content": query}],
            system_prompt="You are a multi-model consensus evaluator. Synthesize the most accurate, balanced, and verified answer.",
        )
        final_answer = ""
        async for ev in engine.stream(req):
            if ev.type == "final":
                final_answer = ev.content
            elif ev.type == "error":
                return f"ERROR in consensus: {ev.content}"
        return final_answer or "Consensus complete without response."

    async def jarvis_debate(args: dict[str, Any], _brain: str) -> str:
        topic = str(args.get("topic") or "").strip()
        if not topic:
            return "ERROR: topic is required."
        router = router_getter() if router_getter else None
        if not router:
            return "ERROR: Engine router is not available."
        from app.engines.base import EngineRequest
        from app.engines.consensus import ConsensusEngine

        cfg = router.settings.load()
        prim_p, prim_m = router.resolve_target()
        sec_p, sec_m = router.resolve_secondary_target(prim_p, cfg)
        engine = ConsensusEngine(
            primary_provider=prim_p,
            primary_model=prim_m,
            secondary_provider=sec_p,
            secondary_model=sec_m,
            router=router,
            mode="debate",
        )
        req = EngineRequest(
            session_id="debate",
            brain=_brain or "Brain Default",
            provider=prim_p,
            model=prim_m,
            messages=[{"role": "user", "content": topic}],
            system_prompt="Conduct an adversarial debate between two independent AI architectures. Model A proposes a thesis, Model B challenges and probes edge cases, and the Arbiter delivers a balanced verdict.",
        )
        final_answer = ""
        async for ev in engine.stream(req):
            if ev.type == "final":
                final_answer = ev.content
            elif ev.type == "error":
                return f"ERROR in debate: {ev.content}"
        return final_answer or "Debate complete without response."

    async def experience_search(args: dict[str, Any], _brain: str) -> str:
        query = str(args.get("query") or args.get("error_text") or "").strip()
        if not query:
            return "ERROR: query or error_text is required."
        store = experience_getter() if experience_getter else None
        if not store:
            return "ERROR: ExperienceStore is not available."
        cmd = str(args.get("failing_command") or "").strip()
        solutions = store.find_solutions(query, failing_cmd=cmd, limit=5)
        if not solutions:
            return "No matching solutions found in Self-Healing Memory."
        out = [f"Found {len(solutions)} verified solution(s) in Self-Healing Memory:\n"]
        for i, s in enumerate(solutions, 1):
            conf = int(s["confidence_score"] * 100)
            out.append(f"### {i}. [{s['error_type'].upper()}] Confidence: {conf}% (used {s['success_count']}x)")
            out.append(f"- Signature: `{s['error_signature']}`")
            if s.get("failing_command"):
                out.append(f"- Command: `{s['failing_command']}`")
            out.append(f"**Solution / Patch:**\n```\n{s['solution_patch']}\n```\n")
        return "\n".join(out)

    async def experience_record(args: dict[str, Any], brain: str) -> str:
        err = str(args.get("error_text") or "").strip()
        patch = str(args.get("solution_patch") or "").strip()
        if not err or not patch:
            return "ERROR: both error_text and solution_patch are required."
        store = experience_getter() if experience_getter else None
        if not store:
            return "ERROR: ExperienceStore is not available."
        cmd = str(args.get("failing_command") or "").strip()
        rec = store.record_fix(err, patch, failing_cmd=cmd, brain=brain or "global")
        conf = int(rec.get("confidence_score", 1.0) * 100)
        return f"Recorded in Self-Healing Memory: ID={rec.get('id')}, Signature='{rec.get('error_signature')}', Confidence={conf}%"

    async def experience_stats(_args: dict[str, Any], _brain: str) -> str:
        store = experience_getter() if experience_getter else None
        if not store:
            return "ERROR: ExperienceStore is not available."
        st = store.stats()
        lines = [
            "Jarvis OS Self-Healing Memory Stats:",
            f"- Total remembered solutions: {st.get('total_solutions', 0)}",
            f"- Average confidence: {int(st.get('average_confidence', 0.0) * 100)}%",
        ]
        by_type = st.get("by_type", {})
        if by_type:
            lines.append("- Categories: " + ", ".join(f"{k}: {v}" for k, v in by_type.items()))
        return "\n".join(lines)

    async def jarvis_remember(args: dict[str, Any], brain: str) -> str:
        cm = core_memory_getter() if core_memory_getter else None
        if cm is None:
            from app.brain.core_memory import CoreMemoryManager
            cm = CoreMemoryManager(vaults)
        fact = str(args.get("fact") or "").strip()
        section = str(args.get("section") or "user").strip()
        return cm.remember(brain, fact, section=section)

    async def jarvis_forget(args: dict[str, Any], brain: str) -> str:
        cm = core_memory_getter() if core_memory_getter else None
        if cm is None:
            from app.brain.core_memory import CoreMemoryManager
            cm = CoreMemoryManager(vaults)
        query = str(args.get("query") or "").strip()
        section = str(args.get("section") or "user").strip()
        return cm.forget(brain, query, section=section)

    async def jarvis_core_memory_view(_args: dict[str, Any], brain: str) -> str:
        cm = core_memory_getter() if core_memory_getter else None
        if cm is None:
            from app.brain.core_memory import CoreMemoryManager
            cm = CoreMemoryManager(vaults)
        return cm.get_raw(brain)

    async def jarvis_search(args: dict[str, Any], brain: str) -> str:
        idx = hybrid_index_getter() if hybrid_index_getter else None
        if idx is None:
            return "ERROR: Hybrid search index is not initialized."
        query = str(args.get("query") or "").strip()
        if not query:
            return "ERROR: query is required."
        max_results = max(1, min(int(args.get("max_results") or 5), 20))
        path_filter = str(args.get("path_filter") or "").strip()

        hits = idx.search(brain, query, limit=max_results, path_filter=path_filter)
        if not hits:
            idx.index_brain(vaults, brain)
            hits = idx.search(brain, query, limit=max_results, path_filter=path_filter)

        if not hits:
            return f"No matching notes or documents found in brain '{brain}' for query: '{query}'"

        out = [f"Found {len(hits)} relevant note snippet(s) in Second Brain '{brain}' (Hybrid BM25 + Vector RRF):\n"]
        for i, h in enumerate(hits, 1):
            out.append(f"### {i}. `{h['path']}` > {h['heading']} (Relevance: {h['score']}%, Match: {h['match_type']})")
            out.append(f"```markdown\n{h['snippet']}\n```\n")
        return "\n".join(out)

    async def jarvis_reindex(args: dict[str, Any], brain: str) -> str:
        idx = hybrid_index_getter() if hybrid_index_getter else None
        if idx is None:
            return "ERROR: Hybrid search index is not initialized."
        force = bool(args.get("force", False))
        res = idx.index_brain(vaults, brain, force=force)
        return f"Second Brain '{brain}' hybrid indexing complete: {res['indexed_files']} files re-indexed ({res['total_chunks']} chunks created/updated)."

    async def jarvis_character_view(args: dict[str, Any], brain: str) -> str:
        b = str(args.get("brain") or brain or "").strip()
        cm = character_getter() if character_getter else None
        if cm is None:
            from app.brain.character import CharacterManager
            cm = CharacterManager(vaults)
        char = cm.get_character(b)
        out = [
            f"# Active Character: {char.name} ({char.avatar})",
            f"- Role: {char.role}",
            f"- Tone: {char.tone}",
        ]
        if char.expertise:
            out.append(f"- Core Expertise: {', '.join(char.expertise)}")
        if char.directives:
            out.append("- Operating Directives:")
            for d in char.directives:
                out.append(f"  * {d}")
        if char.bio:
            out.append(f"\n## Purpose\n{char.bio}")
        return "\n".join(out)

    async def jarvis_character_apply_template(args: dict[str, Any], brain: str) -> str:
        b = str(args.get("brain") or brain or "").strip()
        tpl_id = str(args.get("template_id") or "default").strip()
        cm = character_getter() if character_getter else None
        if cm is None:
            from app.brain.character import CharacterManager
            cm = CharacterManager(vaults)
        try:
            char = cm.apply_template(b, tpl_id)
            return f"Applied character template '{tpl_id}': active persona is now {char.name} ({char.role})."
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: Failed to apply character template '{tpl_id}': {exc}"

    async def jarvis_create_skill(args: dict[str, Any], brain: str) -> str:
        s_name = str(args.get("name") or "").strip()
        s_slug = str(args.get("slug") or "").strip()
        s_desc = str(args.get("description") or "").strip()
        s_inst = str(args.get("instructions") or "").strip()
        s_group = str(args.get("group") or "Custom").strip()

        if not s_name or not s_desc or not s_inst:
            return "ERROR: name, description, and instructions are required."

        sm = skills_getter() if skills_getter else None
        if sm is None:
            from app.brain.skills import SkillStore
            sm = SkillStore(vaults.brains_dir.parent, vaults)

        try:
            info = sm.save_skill(
                brain=brain,
                slug=s_slug or s_name,
                name=s_name,
                description=s_desc,
                instructions=s_inst,
                group=s_group,
            )
            return f"Successfully created skill '{info.name}' (`{info.slug}`) in {info.path}. Description: {info.description}"
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: Failed to save skill: {exc}"

    async def jarvis_profile_user(args: dict[str, Any], brain: str) -> str:
        facts = args.get("facts")
        if not isinstance(facts, list) or not facts:
            return "ERROR: 'facts' must be a non-empty list of {fact, section} objects."
        cm = core_memory_getter() if core_memory_getter else None
        if cm is None:
            from app.brain.core_memory import CoreMemoryManager
            cm = CoreMemoryManager(vaults)
        results = cm.profile_user(brain, facts)
        return "Dialectic user profiling updated:\n" + "\n".join(f"- {r}" for r in results)

    return [
        ToolDefinition(
            name="vault_read",
            description="Read lines from a file inside the active Second Brain vault.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path inside the active brain"},
                    "start_line": {"type": "integer", "default": 1},
                    "max_lines": {"type": "integer", "default": 200},
                },
                "required": ["path"],
            },
            required_perm=PermLevel.READONLY,
            source="builtin:vault",
            handler=vault_read,
        ),
        ToolDefinition(
            name="vault_list",
            description="List files and subdirectories inside the active Second Brain vault.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative directory path (empty for vault root)"},
                },
            },
            required_perm=PermLevel.READONLY,
            source="builtin:vault",
            handler=vault_list,
        ),
        ToolDefinition(
            name="vault_search",
            description="Search text across Markdown/YAML/JSON files in the active Second Brain vault.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_hits": {"type": "integer", "default": 30},
                },
                "required": ["query"],
            },
            required_perm=PermLevel.READONLY,
            source="builtin:vault",
            handler=vault_search,
        ),
        ToolDefinition(
            name="vault_write",
            description="Create or overwrite a file inside the active Second Brain vault.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            required_perm=PermLevel.SAFE,
            source="builtin:vault",
            handler=vault_write,
            shape_output=False,
        ),
        ToolDefinition(
            name="vault_edit",
            description="Replace an exact snippet inside a file in the active Second Brain vault.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
            required_perm=PermLevel.SAFE,
            source="builtin:vault",
            handler=vault_edit,
            shape_output=False,
        ),
        ToolDefinition(
            name="web_search",
            description="Search the public web and return matching page titles and URLs.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
            required_perm=PermLevel.READONLY,
            source="builtin:web",
            handler=web_search,
        ),
        ToolDefinition(
            name="web_fetch",
            description="Fetch a public HTTP/HTTPS webpage and return readable text (SSRF-protected).",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                },
                "required": ["url"],
            },
            required_perm=PermLevel.READONLY,
            source="builtin:web",
            handler=web_fetch,
        ),
        ToolDefinition(
            name="jarvis_browse_url",
            description=(
                "Fetch a public webpage, extract its main article/documentation content into clean GitHub Markdown, "
                "extract metadata (title, author, description), and optionally save the full document into the Second Brain vault."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Public HTTP or HTTPS URL to browse"},
                    "max_chars": {
                        "type": "integer",
                        "default": 8000,
                        "description": "Max characters of Markdown content to return to the model (default: 8000, max: 40000)",
                    },
                    "save_to_vault": {
                        "type": "boolean",
                        "default": False,
                        "description": "If true, saves the full parsed article into the active brain under sources/<slug>.md",
                    },
                    "vault_path": {
                        "type": "string",
                        "description": "Optional custom vault path to save (e.g. 'sources/guide.md' or 'inbox/article.md')",
                    },
                },
                "required": ["url"],
            },
            required_perm=PermLevel.SAFE,
            source="builtin:web",
            handler=jarvis_browse_url,
            shape_output=True,
        ),
        ToolDefinition(
            name="jarvis_query",
            description="Inspect a stored large tool result by ref_id (paging, field selection, or read-only SQL over table `records`).",
            parameters={
                "type": "object",
                "properties": {
                    "ref_id": {"type": "string"},
                    "sql": {"type": "string", "description": "Optional read-only SELECT query over `records`"},
                    "fields": {"type": "array", "items": {"type": "string"}},
                    "offset": {"type": "integer", "default": 0},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["ref_id"],
            },
            required_perm=PermLevel.READONLY,
            source="builtin:query",
            handler=jarvis_query,
            shape_output=False,
        ),
        ToolDefinition(
            name="jarvis_connections",
            description="Manage or list external MCP connections and inspect their tools.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "test", "delete"], "description": "Action: 'list' (default), 'test', or 'delete'"},
                    "conn_id": {"type": "string", "description": "Target connection ID for action 'test' or 'delete'"},
                },
            },
            required_perm=PermLevel.READONLY,
            source="builtin:hub",
            handler=jarvis_connections,
            shape_output=False,
        ),
        ToolDefinition(
            name="jarvis_mcp_connect",
            description="Connect and register an external MCP server via API key or token (HTTP or Stdio). Tests the connection, discovers tools, and securely saves credentials.",
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Unique connection ID (e.g. 'brave-search', 'context7', 'github', 'tavily')"},
                    "name": {"type": "string", "description": "Optional display name (e.g. 'Brave Search MCP')"},
                    "url": {"type": "string", "description": "HTTP endpoint URL for the MCP server"},
                    "api_key": {"type": "string", "description": "API key, token, or secret for authentication"},
                    "header_name": {"type": "string", "description": "Header name for HTTP auth (default: 'Authorization' with Bearer, or 'x-api-key')"},
                    "transport": {"type": "string", "enum": ["http", "stdio"], "description": "Transport protocol: 'http' or 'stdio'"},
                    "command": {"type": "string", "description": "Command for stdio (e.g. 'npx')"},
                    "args": {"type": "array", "items": {"type": "string"}, "description": "Arguments for stdio command"},
                    "env_var": {"type": "string", "description": "Environment variable name for API key in stdio (e.g. 'BRAVE_API_KEY')"},
                    "perm": {"type": "string", "enum": ["readonly", "safe", "full"], "description": "Permission level (default: 'safe')"},
                },
                "required": ["id"],
            },
            required_perm=PermLevel.SAFE,
            source="builtin:hub",
            handler=jarvis_mcp_connect,
            shape_output=False,
        ),
        ToolDefinition(
            name="jarvis_consensus",
            description="Query multiple diverse AI models in parallel and synthesize a verified high-confidence consensus answer with agreement matrix.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The complex question, architecture choice, or problem to evaluate across models"},
                },
                "required": ["query"],
            },
            required_perm=PermLevel.READONLY,
            source="builtin:ai",
            handler=jarvis_consensus,
            shape_output=False,
        ),
        ToolDefinition(
            name="jarvis_debate",
            description="Conduct a multi-round adversarial debate between two AI models (Thesis -> Antithesis -> Arbiter Synthesis) to find edge cases or optimal trade-offs.",
            parameters={
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "The topic, technical proposal, or dilemma to debate"},
                },
                "required": ["topic"],
            },
            required_perm=PermLevel.READONLY,
            source="builtin:ai",
            handler=jarvis_debate,
            shape_output=False,
        ),
        ToolDefinition(
            name="experience_search",
            description="Search past verified solutions and diff patches for an error message or failing command from Self-Healing Memory.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The error message, exception text, or symptom to search"},
                    "failing_command": {"type": "string", "description": "Optional failing command (e.g. 'pytest', 'npm run build')"},
                },
                "required": ["query"],
            },
            required_perm=PermLevel.READONLY,
            source="builtin:experience",
            handler=experience_search,
            shape_output=False,
        ),
        ToolDefinition(
            name="experience_record",
            description="Record a verified solution, code patch, or fix for an error signature into Self-Healing Memory.",
            parameters={
                "type": "object",
                "properties": {
                    "error_text": {"type": "string", "description": "The error traceback or failure message that occurred"},
                    "solution_patch": {"type": "string", "description": "The code patch, command, or explanation that fixed it"},
                    "failing_command": {"type": "string", "description": "Optional failing command"},
                },
                "required": ["error_text", "solution_patch"],
            },
            required_perm=PermLevel.SAFE,
            source="builtin:experience",
            handler=experience_record,
            shape_output=False,
        ),
        ToolDefinition(
            name="experience_stats",
            description="Return summary statistics and top solution categories from Self-Healing Memory.",
            parameters={"type": "object", "properties": {}},
            required_perm=PermLevel.READONLY,
            source="builtin:experience",
            handler=experience_stats,
            shape_output=False,
        ),
        ToolDefinition(
            name="jarvis_remember",
            description="Record or update a durable fact, preference, rule, or project in Core Memory (always-in-context self-editing state in style of MemGPT/Letta).",
            parameters={
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "The fact, habit, technical stack, or directive to remember"},
                    "section": {
                        "type": "string",
                        "enum": ["user", "persona", "context"],
                        "default": "user",
                        "description": "Target section: 'user' for user preferences/profile, 'persona' for assistant rules/tone, 'context' for active projects/goals",
                    },
                },
                "required": ["fact"],
            },
            required_perm=PermLevel.SAFE,
            source="builtin:core_memory",
            handler=jarvis_remember,
            shape_output=False,
        ),
        ToolDefinition(
            name="jarvis_forget",
            description="Remove an outdated fact, preference, or directive from Core Memory.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keyword or text of the fact to remove"},
                    "section": {
                        "type": "string",
                        "enum": ["user", "persona", "context"],
                        "default": "user",
                        "description": "Target section (defaults to 'user')",
                    },
                },
                "required": ["query"],
            },
            required_perm=PermLevel.SAFE,
            source="builtin:core_memory",
            handler=jarvis_forget,
            shape_output=False,
        ),
        ToolDefinition(
            name="jarvis_core_memory_view",
            description="View the complete raw Markdown of Core Memory (User Profile, Persona, Context & Goals).",
            parameters={"type": "object", "properties": {}},
            required_perm=PermLevel.READONLY,
            source="builtin:core_memory",
            handler=jarvis_core_memory_view,
            shape_output=False,
        ),
        ToolDefinition(
            name="jarvis_search",
            description=(
                "Search the Second Brain (memory, wiki, sources, skills, loops) using Hybrid Semantic Search "
                "(BM25 exact keyword matching + dense vector embeddings with Reciprocal Rank Fusion)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search question, topic, or keyword"},
                    "max_results": {
                        "type": "integer",
                        "default": 5,
                        "description": "Max matching note chunks to return (default: 5, max: 20)",
                    },
                    "path_filter": {
                        "type": "string",
                        "description": "Optional folder prefix filter (e.g. 'wiki', 'sources', 'memory')",
                    },
                },
                "required": ["query"],
            },
            required_perm=PermLevel.READONLY,
            source="builtin:hybrid_search",
            handler=jarvis_search,
            shape_output=True,
        ),
        ToolDefinition(
            name="jarvis_reindex",
            description="Re-index markdown notes in the active Second Brain for hybrid semantic search.",
            parameters={
                "type": "object",
                "properties": {
                    "force": {
                        "type": "boolean",
                        "default": False,
                        "description": "If true, forces a full re-index of all notes regardless of modification time",
                    },
                },
            },
            required_perm=PermLevel.SAFE,
            source="builtin:hybrid_search",
            handler=jarvis_reindex,
            shape_output=False,
        ),
        ToolDefinition(
            name="jarvis_character_view",
            description="View the active brain persona, character card, and operating directives from memory/CHARACTER.md.",
            parameters={
                "type": "object",
                "properties": {
                    "brain": {"type": "string", "description": "Optional brain name (defaults to active brain)"},
                },
            },
            required_perm=PermLevel.READONLY,
            source="builtin:character",
            handler=jarvis_character_view,
            shape_output=False,
        ),
        ToolDefinition(
            name="jarvis_character_apply_template",
            description="Switch the active brain's character card and persona to a pre-configured template (architect, researcher, writer, executive, default).",
            parameters={
                "type": "object",
                "properties": {
                    "template_id": {
                        "type": "string",
                        "enum": ["default", "architect", "researcher", "writer", "executive"],
                        "description": "Character template ID to apply",
                    },
                    "brain": {"type": "string", "description": "Optional brain name (defaults to active brain)"},
                },
                "required": ["template_id"],
            },
            required_perm=PermLevel.SAFE,
            source="builtin:character",
            handler=jarvis_character_apply_template,
            shape_output=False,
        ),
        ToolDefinition(
            name="jarvis_create_skill",
            description="Autonomously create or improve an Agent Skill (SKILL.md) in the active Second Brain with strict description budget (<= 150 chars).",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Human-readable skill name"},
                    "slug": {"type": "string", "description": "Lowercase hyphenated identifier e.g. 'pdf-parser'"},
                    "description": {"type": "string", "description": "Actionable description (strictly <= 150 characters)"},
                    "instructions": {"type": "string", "description": "Markdown instructions body for how the agent executes this skill"},
                    "group": {"type": "string", "default": "Synthesized", "description": "Skill category group"},
                },
                "required": ["name", "slug", "description", "instructions"],
            },
            required_perm=PermLevel.SAFE,
            source="builtin:skills",
            handler=jarvis_create_skill,
            shape_output=False,
        ),
        ToolDefinition(
            name="jarvis_profile_user",
            description="Proactively update the dialectic User Profile and Context in Core Memory based on conversational traits and preferences.",
            parameters={
                "type": "object",
                "properties": {
                    "facts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "fact": {"type": "string", "description": "Specific user trait, preference, or goal"},
                                "section": {"type": "string", "enum": ["user", "persona", "context"], "default": "user"},
                            },
                            "required": ["fact"],
                        },
                        "description": "List of observed facts or user preferences to update",
                    },
                },
                "required": ["facts"],
            },
            required_perm=PermLevel.SAFE,
            source="builtin:core_memory",
            handler=jarvis_profile_user,
            shape_output=False,
        ),
        ToolDefinition(
            name="jarvis_browser_task",
            description="Autonomous browser agent: execute multi-step interactive web tasks (clicking buttons, filling forms, SPA navigation) using browser-use or Playwright with SSRF guard.",
            parameters={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Goal or instruction for the browser agent (e.g. 'Search for recent AI papers and extract titles')"},
                    "url": {"type": "string", "description": "Initial target website URL"},
                    "max_steps": {"type": "integer", "default": 10, "description": "Maximum interactive browser actions"},
                    "headless": {"type": "boolean", "default": True, "description": "Whether to run headless"},
                },
                "required": ["task"],
            },
            required_perm=PermLevel.FULL,
            source="builtin:browser",
            handler=jarvis_browser_task_fn,
            shape_output=False,
        ),
        ToolDefinition(
            name="jarvis_browser_navigate",
            description="Navigate to a webpage using Playwright / SSRF-safe reader and extract page content and title.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Target webpage URL to visit"},
                },
                "required": ["url"],
            },
            required_perm=PermLevel.SAFE,
            source="builtin:browser",
            handler=jarvis_browser_navigate_fn,
            shape_output=False,
        ),
    ]
