"""Unified MCP Hub Gateway — single tool entry point for BOTH API and CLI engines.

API engines call `discover_tools()` and `call_tool()` in-process.
CLI engines (Codex CLI, Antigravity CLI) connect to `POST /hub/mcp` (localhost + Bearer token).
Both paths execute the exact same permission gate, audit log, and Tool Result Shaper.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

import httpx

from app import APP_VERSION
from app.brain.vault import VaultManager
from app.engines.base import ToolExecutor
from app.hub.builtin_tools import ToolDefinition, build_builtin_tools
from app.hub.mcp_client import MCPClientPool, MCPConnectionStore
from app.hub.permissions import AuditEntry, AuditLogger, PermLevel, is_tool_allowed
from app.hub.shaper import ToolResultShaper, ToolResultStore
from app.security.secrets import SecretStore


class MCPHubGateway:
    """Central gateway merging built-in, external MCP, and plugin tools under hard permissions."""

    def __init__(
        self,
        *,
        code_dir: Path,
        state_dir: Path,
        port: int,
        vaults: VaultManager,
        secrets_store: SecretStore,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._code_dir = code_dir
        self._state_dir = state_dir
        self._port = port
        self._token_path = state_dir / ".hub_token"
        self._cached_token: str = ""

        self.audit = AuditLogger(state_dir)
        self.result_store = ToolResultStore(state_dir, secrets_store)
        self.shaper = ToolResultShaper(self.result_store)
        self.connections = MCPConnectionStore(state_dir, secrets_store)
        self.mcp_pool = MCPClientPool(self.connections, http_transport=http_transport)
        self._engine_router: Any = None
        self._experience_store: Any = None
        self._core_memory: Any = None
        self._hybrid_index: Any = None
        self._character_manager: Any = None
        self._skills_store: Any = None
        self._builtins = build_builtin_tools(
            vaults=vaults,
            result_store=self.result_store,
            list_connections_fn=lambda: [c.to_public_dict() for c in self.connections.list_connections()],
            connections_store=self.connections,
            mcp_pool=self.mcp_pool,
            http_transport=http_transport,
            router_getter=lambda: self._engine_router,
            experience_getter=lambda: self._experience_store,
            core_memory_getter=lambda: self._core_memory,
            hybrid_index_getter=lambda: self._hybrid_index,
            character_getter=lambda: self._character_manager,
            skills_getter=lambda: self._skills_store,
        )
        # Phase 3 PluginHost registers dynamic tool providers and pre/post hooks here
        self._extra_tool_providers: list[Any] = []
        self._hook_runner: Any = None

    def set_hook_runner(self, runner: Any) -> None:
        self._hook_runner = runner

    def set_engine_router(self, router: Any) -> None:
        self._engine_router = router

    def set_experience_store(self, store: Any) -> None:
        self._experience_store = store

    def set_core_memory(self, core_mem: Any) -> None:
        self._core_memory = core_mem

    def set_hybrid_index(self, index: Any) -> None:
        self._hybrid_index = index

    def set_character_manager(self, mgr: Any) -> None:
        self._character_manager = mgr

    def set_skills_store(self, store: Any) -> None:
        self._skills_store = store

    def hub_url(self) -> str:
        return f"http://127.0.0.1:{self._port}/hub/mcp"

    def hub_token(self) -> str:
        if self._cached_token:
            return self._cached_token
        if self._token_path.exists():
            tok = self._token_path.read_text(encoding="utf-8").strip()
            if tok:
                self._cached_token = tok
                return tok
        tok = secrets.token_urlsafe(32)
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._token_path.with_suffix(".tmp")
        tmp.write_text(tok + "\n", encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self._token_path)
        self._cached_token = tok
        return tok

    def verify_bearer(self, auth_header: str | None) -> bool:
        if not auth_header or not auth_header.startswith("Bearer "):
            return False
        provided = auth_header[7:].strip()
        return hmac.compare_digest(provided, self.hub_token())

    def hide_hub_token(self, text: str) -> str:
        """Redact the local hub bearer token from logs or subprocess output."""
        if not text:
            return text
        tok = self._cached_token or (self._token_path.read_text(encoding="utf-8").strip() if self._token_path.exists() else "")
        if tok and tok in text:
            return text.replace(tok, "[REDACTED_HUB_TOKEN]")
        return text

    def load_catalog(self) -> list[dict[str, Any]]:
        path = self._code_dir / "system" / "mcp-catalog.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return list(data.get("connectors") or [])
        except (OSError, json.JSONDecodeError):
            return []

    def register_tool_provider(self, provider: Any) -> None:
        self._extra_tool_providers.append(provider)

    async def all_tool_definitions(self, brain: str = "") -> dict[str, ToolDefinition]:
        table: dict[str, ToolDefinition] = {t.name: t for t in self._builtins}
        for ext in await self.mcp_pool.discover_all():
            if ext.name not in table:
                table[ext.name] = ext
        for prov in self._extra_tool_providers:
            try:
                extra = prov(brain) if callable(prov) else []
                for t in extra:
                    if t.name not in table:
                        table[t.name] = t
            except Exception:  # noqa: BLE001
                continue
        return table

    async def discover_schemas(self, *, brain: str, run_mode: str = "full") -> list[dict[str, Any]]:
        """Return tool schemas allowed for the given run_mode (for API engines)."""
        defs = await self.all_tool_definitions(brain)
        out: list[dict[str, Any]] = []
        for t in defs.values():
            allowed, _ = is_tool_allowed(
                required=t.required_perm,
                connection_perm=t.connection_perm,
                run_mode=run_mode,
            )
            if allowed:
                out.append(t.to_schema())
        return out

    async def list_tools_info(self, *, brain: str, run_mode: str = "full") -> list[dict[str, Any]]:
        defs = await self.all_tool_definitions(brain)
        rows: list[dict[str, Any]] = []
        for t in defs.values():
            allowed, reason = is_tool_allowed(
                required=t.required_perm,
                connection_perm=t.connection_perm,
                run_mode=run_mode,
            )
            rows.append(
                {
                    "name": t.name,
                    "description": t.description,
                    "source": t.source,
                    "required_perm": t.required_perm.label(),
                    "connection_perm": t.connection_perm.label(),
                    "allowed_in_mode": allowed,
                    "reason": reason,
                }
            )
        return rows

    async def call_tool(
        self,
        *,
        name: str,
        args: dict[str, Any],
        brain: str,
        run_mode: str = "full",
        session_id: str = "",
        for_model: bool = True,
    ) -> str:
        """Execute a tool through the hard permission gate, audit log, and result shaper."""
        t0 = time.perf_counter()
        defs = await self.all_tool_definitions(brain)
        tool = defs.get(name)
        if tool is None:
            msg = f"ERROR: Unknown tool '{name}'."
            self.audit.record(
                AuditEntry(
                    ts=time.time(),
                    brain=brain,
                    session_id=session_id,
                    run_mode=run_mode,
                    tool=name,
                    source="unknown",
                    allowed=False,
                    reason="unknown tool",
                )
            )
            return msg

        allowed, reason = is_tool_allowed(
            required=tool.required_perm,
            connection_perm=tool.connection_perm,
            run_mode=run_mode,
        )
        if not allowed:
            self.audit.record(
                AuditEntry(
                    ts=time.time(),
                    brain=brain,
                    session_id=session_id,
                    run_mode=run_mode,
                    tool=name,
                    source=tool.source,
                    allowed=False,
                    reason=reason,
                )
            )
            return f"ERROR: {reason}"

        if self._hook_runner is not None:
            await self._hook_runner(
                "pre_tool_call",
                brain,
                {"tool_name": name, "args": args or {}, "brain": brain, "run_mode": run_mode, "session_id": session_id},
            )

        try:
            raw_result = await tool.handler(args or {}, brain)
        except Exception as exc:  # noqa: BLE001
            raw_result = f"ERROR: Tool '{name}' failed: {exc}"

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if self._hook_runner is not None:
            await self._hook_runner(
                "post_tool_call",
                brain,
                {
                    "tool_name": name,
                    "args": args or {},
                    "result": raw_result,
                    "brain": brain,
                    "run_mode": run_mode,
                    "session_id": session_id,
                    "duration_ms": elapsed_ms,
                },
            )
        self.audit.record(
            AuditEntry(
                ts=time.time(),
                brain=brain,
                session_id=session_id,
                run_mode=run_mode,
                tool=name,
                source=tool.source,
                allowed=True,
                reason=reason,
                duration_ms=elapsed_ms,
            )
        )

        safe_text = self.hide_hub_token(raw_result)
        if for_model and tool.shape_output:
            return self.shaper.shape(brain=brain, tool_name=name, raw_text=safe_text).text
        return safe_text

    def make_executor(self, *, brain: str, run_mode: str = "full", session_id: str = "") -> ToolExecutor:
        async def _exec(name: str, args: dict[str, Any]) -> str:
            return await self.call_tool(
                name=name,
                args=args,
                brain=brain,
                run_mode=run_mode,
                session_id=session_id,
                for_model=True,
            )

        return _exec

    async def handle_jsonrpc(
        self,
        body: dict[str, Any],
        *,
        brain: str,
        run_mode: str = "full",
        session_id: str = "",
    ) -> dict[str, Any]:
        """Handle MCP JSON-RPC 2.0 calls arriving at POST /hub/mcp from CLI engines."""
        req_id = body.get("id")
        method = str(body.get("method") or "")
        params = body.get("params") if isinstance(body.get("params"), dict) else {}

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "jarvis-mcp-hub", "version": APP_VERSION},
                },
            }

        if method == "notifications/initialized":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        if method == "tools/list":
            defs = await self.all_tool_definitions(brain)
            mcp_tools = []
            for t in defs.values():
                allowed, _ = is_tool_allowed(
                    required=t.required_perm,
                    connection_perm=t.connection_perm,
                    run_mode=run_mode,
                )
                if allowed:
                    mcp_tools.append(t.to_mcp_tool())
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": mcp_tools}}

        if method == "tools/call":
            tool_name = str(params.get("name") or "")
            tool_args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            output = await self.call_tool(
                name=tool_name,
                args=tool_args,
                brain=brain,
                run_mode=run_mode,
                session_id=session_id,
                for_model=True,
            )
            is_err = output.startswith("ERROR:")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": output}],
                    "isError": is_err,
                },
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
