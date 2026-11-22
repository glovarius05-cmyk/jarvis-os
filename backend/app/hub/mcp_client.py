"""External MCP connection store (encrypted secrets) and HTTP/Stdio JSON-RPC client pool."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.hub.builtin_tools import ToolDefinition
from app.hub.permissions import PermLevel
from app.security.secrets import SecretStore

_CONN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_WRITE_HINT_RE = re.compile(
    r"(^|_)(create|update|delete|remove|drop|write|edit|send|post|publish|pay|charge|transfer|deploy|exec|run)(_|$)",
    re.IGNORECASE,
)


def normalize_mcp_url(url: str) -> str:
    """Normalize and auto-route MCP URLs (e.g. converting marketing/docs URLs to live endpoints)."""
    clean = (url or "").strip()
    if not clean:
        return clean
    if not clean.startswith(("http://", "https://")):
        clean = f"https://{clean}"
    try:
        parsed = urlparse(clean)
        host = (parsed.hostname or "").lower()
        if host in ("agensi.io", "www.agensi.io"):
            return "https://mcp.agensi.io/mcp"
        if host == "mcp.agensi.io":
            p = parsed.path.rstrip("/")
            if p in ("", "/mcp/auth", "/auth"):
                return "https://mcp.agensi.io/mcp"
    except Exception:
        pass
    return clean


@dataclass
class MCPConnection:
    id: str
    name: str
    transport: str  # "http" | "stdio"
    perm: str = "readonly"  # "readonly" | "safe" | "full"
    enabled: bool = True
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    write_tools: list[str] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "transport": self.transport,
            "perm": self.perm,
            "enabled": self.enabled,
            "url": self.url,
            "has_headers": bool(self.headers),
            "command": self.command,
            "args": list(self.args),
            "has_env": bool(self.env),
            "write_tools": list(self.write_tools),
        }


class MCPConnectionStore:
    """Persists external MCP server definitions in STATE_DIR/mcp_servers.json with Fernet secrets."""

    def __init__(self, state_dir: Path, secrets: SecretStore) -> None:
        self._path = state_dir / "mcp_servers.json"
        self._secrets = secrets
        self._lock = threading.RLock()

    def list_connections(self) -> list[MCPConnection]:
        with self._lock:
            raw = self._read_raw()
            out: list[MCPConnection] = []
            for item in raw:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                headers = {
                    str(k): self._secrets.decrypt(str(v))
                    for k, v in (item.get("headers") or {}).items()
                }
                env = {
                    str(k): self._secrets.decrypt(str(v))
                    for k, v in (item.get("env") or {}).items()
                }
                out.append(
                    MCPConnection(
                        id=str(item["id"]),
                        name=str(item.get("name") or item["id"]),
                        transport=str(item.get("transport") or "http"),
                        perm=PermLevel.parse(str(item.get("perm") or "readonly")).label(),
                        enabled=bool(item.get("enabled", True)),
                        url=str(item.get("url") or ""),
                        headers=headers,
                        command=str(item.get("command") or ""),
                        args=[str(a) for a in (item.get("args") or [])],
                        env=env,
                        write_tools=[str(w) for w in (item.get("write_tools") or [])],
                    )
                )
            return out

    def get(self, conn_id: str) -> MCPConnection | None:
        for c in self.list_connections():
            if c.id == conn_id:
                return c
        return None

    def upsert(self, conn: MCPConnection) -> MCPConnection:
        if not _CONN_ID_RE.match(conn.id):
            raise ValueError("Connection id must be lowercase ASCII alphanumeric/hyphen/underscore (1-48 chars)")
        if conn.transport not in {"http", "stdio"}:
            raise ValueError("Transport must be 'http' or 'stdio'")
        if conn.transport == "http":
            conn.url = normalize_mcp_url(conn.url)
            if not conn.url.strip():
                raise ValueError("HTTP MCP connection requires 'url'")
        if conn.transport == "stdio" and not conn.command.strip():
            raise ValueError("Stdio MCP connection requires 'command'")

        conn.perm = PermLevel.parse(conn.perm, PermLevel.READONLY).label()

        with self._lock:
            existing = self.list_connections()
            replaced = False
            next_list: list[MCPConnection] = []
            for item in existing:
                if item.id == conn.id:
                    # Preserve existing encrypted headers/env if caller sent empty dicts on update,
                    # EXCEPT when explicitly reset or for presets like agensi where empty headers = public mode
                    if not conn.headers and item.headers and conn.id != "agensi":
                        conn.headers = item.headers
                    if not conn.env and item.env:
                        conn.env = item.env
                    next_list.append(conn)
                    replaced = True
                else:
                    next_list.append(item)
            if not replaced:
                next_list.append(conn)
            self._write_all(next_list)
            return conn

    def delete(self, conn_id: str) -> bool:
        with self._lock:
            existing = self.list_connections()
            kept = [c for c in existing if c.id != conn_id]
            if len(kept) == len(existing):
                return False
            self._write_all(kept)
            return True

    def _read_raw(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _write_all(self, conns: list[MCPConnection]) -> None:
        serialized: list[dict[str, Any]] = []
        for c in conns:
            serialized.append(
                {
                    "id": c.id,
                    "name": c.name,
                    "transport": c.transport,
                    "perm": c.perm,
                    "enabled": c.enabled,
                    "url": c.url,
                    "headers": {k: self._secrets.encrypt(v) for k, v in c.headers.items()},
                    "command": c.command,
                    "args": c.args,
                    "env": {k: self._secrets.encrypt(v) for k, v in c.env.items()},
                    "write_tools": c.write_tools,
                }
            )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(serialized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self._path)


def classify_external_tool(tool_name: str, write_patterns: list[str]) -> PermLevel:
    """Classify an external MCP tool as READONLY or FULL based on write patterns or verb heuristics."""
    for pat in write_patterns:
        if fnmatch.fnmatch(tool_name, pat):
            return PermLevel.FULL
    if _WRITE_HINT_RE.search(tool_name):
        return PermLevel.FULL
    return PermLevel.READONLY


def _extract_mcp_result_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
            return "\n".join(parts)
        return json.dumps(result, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


class MCPClientPool:
    """Discovers and invokes tools on external HTTP and stdio MCP servers."""

    def __init__(
        self,
        store: MCPConnectionStore,
        *,
        http_transport: httpx.AsyncBaseTransport | None = None,
        cache_ttl_s: float = 30.0,
    ) -> None:
        self._store = store
        self._http_transport = http_transport
        self._cache_ttl = cache_ttl_s
        self._cache: dict[str, tuple[float, list[ToolDefinition]]] = {}

    def invalidate(self, conn_id: str | None = None) -> None:
        if conn_id is None:
            self._cache.clear()
        else:
            self._cache.pop(conn_id, None)

    async def discover_all(self) -> list[ToolDefinition]:
        conns = [c for c in self._store.list_connections() if c.enabled]
        if not conns:
            return []

        results = await asyncio.gather(*(self._discover_one(c) for c in conns), return_exceptions=True)
        tools: list[ToolDefinition] = []
        for res in results:
            if isinstance(res, list):
                tools.extend(res)
        return tools

    async def test_connection(self, conn: MCPConnection) -> list[dict[str, Any]]:
        """Test an MCP connection by querying its tools list directly."""
        if conn.transport == "http":
            return await self._http_list_tools(conn)
        return await self._stdio_list_tools(conn)

    async def _discover_one(self, conn: MCPConnection) -> list[ToolDefinition]:
        now = time.time()
        cached = self._cache.get(conn.id)
        if cached is not None and (now - cached[0]) < self._cache_ttl:
            return cached[1]

        try:
            if conn.transport == "http":
                raw_tools = await self._http_list_tools(conn)
            else:
                raw_tools = await self._stdio_list_tools(conn)
        except Exception:  # noqa: BLE001
            # Fail soft: one downed MCP server must never break other tools
            return cached[1] if cached is not None else []

        conn_perm = PermLevel.parse(conn.perm, PermLevel.READONLY)
        defs: list[ToolDefinition] = []
        for item in raw_tools:
            remote_name = str(item.get("name") or "").strip()
            if not remote_name:
                continue
            namespaced = f"mcp__{conn.id}__{remote_name}"
            req_perm = classify_external_tool(remote_name, conn.write_tools)
            schema = item.get("inputSchema") or item.get("parameters") or {"type": "object", "properties": {}}

            async def _invoke(
                args: dict[str, Any],
                _brain: str,
                _conn: MCPConnection = conn,
                _rname: str = remote_name,
            ) -> str:
                if _conn.transport == "http":
                    return await self._http_call_tool(_conn, _rname, args)
                return await self._stdio_call_tool(_conn, _rname, args)

            defs.append(
                ToolDefinition(
                    name=namespaced,
                    description=f"[{conn.name}] {item.get('description') or remote_name}",
                    parameters=schema,
                    required_perm=req_perm,
                    connection_perm=conn_perm,
                    source=f"mcp:{conn.id}",
                    handler=_invoke,
                )
            )

        self._cache[conn.id] = (now, defs)
        return defs

    async def _http_rpc(self, conn: MCPConnection, method: str, params: dict[str, Any] | None = None) -> Any:
        url = normalize_mcp_url(conn.url)
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **conn.headers,
        }
        async with httpx.AsyncClient(timeout=25.0, transport=self._http_transport, follow_redirects=True) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                err_text = resp.text[:300]
                if "Invalid or expired token" in err_text:
                    raise RuntimeError("Неверный или устаревший токен. Очистите поле API Key или нажмите кнопку '1-Click Sign in with Agensi' для входа через OAuth.")
                raise RuntimeError(f"MCP HTTP {resp.status_code}: {err_text}")
            text = resp.text.strip()
            if "data: " in text:
                for line in text.splitlines():
                    if line.startswith("data: "):
                        text = line[6:].strip()
                        break
            data = json.loads(text)
            if "error" in data and data["error"]:
                raise RuntimeError(f"MCP error: {data['error']}")
            return data.get("result")

    async def _http_list_tools(self, conn: MCPConnection) -> list[dict[str, Any]]:
        result = await self._http_rpc(conn, "tools/list", {})
        if isinstance(result, dict) and isinstance(result.get("tools"), list):
            return result["tools"]
        return []

    async def _http_call_tool(self, conn: MCPConnection, tool_name: str, args: dict[str, Any]) -> str:
        try:
            result = await self._http_rpc(conn, "tools/call", {"name": tool_name, "arguments": args})
            return _extract_mcp_result_text(result)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    async def _stdio_rpc_session(
        self,
        conn: MCPConnection,
        requests: list[dict[str, Any]],
        timeout_s: float = 12.0,
    ) -> list[dict[str, Any]]:
        env = {**os.environ, **conn.env}
        proc = await asyncio.create_subprocess_exec(
            conn.command,
            *conn.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        responses: list[dict[str, Any]] = []
        try:
            assert proc.stdin is not None and proc.stdout is not None
            for req in requests:
                proc.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()

            raw_out = await asyncio.wait_for(proc.stdout.read(), timeout=timeout_s)
            for line in raw_out.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict):
                        responses.append(parsed)
                except json.JSONDecodeError:
                    continue
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        return responses

    async def _stdio_list_tools(self, conn: MCPConnection) -> list[dict[str, Any]]:
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        resps = await self._stdio_rpc_session(conn, reqs)
        for r in resps:
            if r.get("id") == 2 and isinstance(r.get("result"), dict):
                tools = r["result"].get("tools")
                if isinstance(tools, list):
                    return tools
        return []

    async def _stdio_call_tool(self, conn: MCPConnection, tool_name: str, args: dict[str, Any]) -> str:
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool_name, "arguments": args}},
        ]
        try:
            resps = await self._stdio_rpc_session(conn, reqs)
            for r in resps:
                if r.get("id") == 2:
                    if r.get("error"):
                        return f"ERROR: {r['error']}"
                    return _extract_mcp_result_text(r.get("result"))
            return "ERROR: No JSON-RPC response from stdio MCP server."
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"
