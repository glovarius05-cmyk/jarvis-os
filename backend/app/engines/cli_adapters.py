"""Subscription CLI engine adapters: Codex CLI (ChatGPT plan) and Antigravity CLI (Google plan).

Why this exists: Users who already pay for ChatGPT or Google plans can run Jarvis on those
subscriptions without buying separate API credits. Both adapters spawn the vendor's official
binary, wire Jarvis's MCP Hub (`POST /hub/mcp`) with Bearer token + mode/brain headers, and
translate CLI output into the exact same EngineEvent stream used by API engines.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator

from app.engines.base import EngineEvent, EngineRequest, sanitize_text


def find_cli_binary(binary: str) -> str | None:
    """Locate a CLI binary on PATH plus common macOS/Linux user bin directories."""
    if not binary:
        return None
    if os.path.isabs(binary):
        return binary if Path(binary).exists() else None
    found = shutil.which(binary)
    if found:
        return found
    extra_dirs = [
        Path.home() / ".local" / "bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path.home() / ".npm-global" / "bin",
    ]
    for directory in extra_dirs:
        candidate = directory / binary
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _compose_cli_prompt(request: EngineRequest) -> str:
    """Build a self-contained prompt carrying system kernel and recent conversation context."""
    parts = [f"<system>\n{request.system_prompt}\n</system>"]
    recent = request.messages[-20:]
    if len(recent) > 1:
        history_lines = []
        for msg in recent[:-1]:
            role = str(msg.get("role") or "user").upper()
            history_lines.append(f"{role}: {msg.get('content', '')}")
        parts.append("<conversation_history>\n" + "\n\n".join(history_lines) + "\n</conversation_history>")
    latest = str(recent[-1].get("content") or "") if recent else ""
    parts.append(latest)
    return "\n\n".join(parts)


async def _terminate_proc(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=3.0)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
            await proc.wait()
        except Exception:  # noqa: BLE001
            pass


def _parse_json_event(line: str, provider: str, model: str) -> list[EngineEvent]:
    """Extract normalized EngineEvents from JSONL emitted by Codex or Antigravity CLI."""
    stripped = line.strip()
    if not stripped:
        return []
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        clean = sanitize_text(line)
        return [EngineEvent(type="text", content=clean, provider=provider, model=model)] if clean else []

    if not isinstance(data, dict):
        return []

    ev_type = str(data.get("type") or data.get("event") or "").lower()

    # Antigravity CLI stream-json result event (`{"event": "result", "result": {...}}`)
    if ev_type == "result":
        res = data.get("result") if isinstance(data.get("result"), dict) else {}
        if res.get("status") == "ERROR" or res.get("error"):
            err_msg = sanitize_text(str(res.get("error") or "CLI error"))
            return [EngineEvent(type="error", content=f"[{provider}] {err_msg}", provider=provider, model=model)]
        if res.get("response"):
            resp_txt = sanitize_text(str(res.get("response")))
            return [EngineEvent(type="text", content=resp_txt, provider=provider, model=model)]

    # Antigravity CLI stream-json format (`step_update.text_delta` / `text_delta` / `assistant`)
    if ev_type in {"text_delta", "content_delta", "message_delta"}:
        delta = sanitize_text(str(data.get("delta") or data.get("text") or data.get("content") or ""))
        return [EngineEvent(type="text", content=delta, provider=provider, model=model)] if delta else []

    if ev_type == "step_update":
        delta = sanitize_text(str(data.get("text_delta") or data.get("text") or ""))
        return [EngineEvent(type="text", content=delta, provider=provider, model=model)] if delta else []

    # Codex exec --json format (`item.completed` with agent_message / command_execution)
    if ev_type in {"item.completed", "item.updated"}:
        item = data.get("item") if isinstance(data.get("item"), dict) else {}
        item_type = str(item.get("type") or "").lower()
        if item_type in {"agent_message", "message"}:
            txt = sanitize_text(str(item.get("text") or item.get("content") or ""))
            return [EngineEvent(type="text", content=txt, provider=provider, model=model)] if txt else []
        if item_type in {"mcp_tool_call", "tool_call", "command_execution"}:
            tname = str(item.get("tool") or item.get("name") or item_type)
            return [EngineEvent(type="tool_call", tool_name=tname, provider=provider, model=model)]

    if ev_type in {"tool_call", "tool_use"}:
        tname = str(data.get("tool") or data.get("name") or "tool")
        return [EngineEvent(type="tool_call", tool_name=tname, provider=provider, model=model)]

    if ev_type in {"tool_result"}:
        tname = str(data.get("tool") or data.get("name") or "tool")
        txt = sanitize_text(str(data.get("output") or data.get("content") or ""))[:1000]
        return [EngineEvent(type="tool_result", tool_name=tname, content=txt, provider=provider, model=model)]

    if ev_type == "error":
        err = sanitize_text(str(data.get("message") or data.get("error") or "CLI error"))
        return [EngineEvent(type="error", content=err, provider=provider, model=model)]

    # Generic text fallback if JSON carries top-level `text` or `response`
    for key in ("text", "response", "output_text"):
        if isinstance(data.get(key), str) and data[key]:
            return [EngineEvent(type="text", content=sanitize_text(data[key]), provider=provider, model=model)]

    return []


class BaseCliEngine:
    """Shared subprocess lifecycle, MCP Hub wiring, token redaction, and cancellation."""

    provider_id: str = "cli"

    def __init__(
        self,
        *,
        binary: str,
        hub_url: str,
        hub_token: str,
        cwd: Path,
        run_mode: str = "full",
        redact_fn=None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self._binary = binary
        self._hub_url = hub_url
        self._hub_token = hub_token
        self._cwd = cwd
        self._run_mode = run_mode
        self._redact = redact_fn or (lambda s: s.replace(hub_token, "[REDACTED_HUB_TOKEN]") if hub_token else s)
        self._extra_env = extra_env or {}

    def _build_command(self, prompt: str, request: EngineRequest, mcp_config_path: Path) -> list[str]:
        raise NotImplementedError

    def _build_env(self, request: EngineRequest, mcp_config_path: Path) -> dict[str, str]:
        env = dict(os.environ)
        env["JARVIS_HUB_URL"] = self._hub_url
        env["JARVIS_HUB_TOKEN"] = self._hub_token
        env["JARVIS_RUN_MODE"] = self._run_mode
        env["JARVIS_BRAIN"] = request.brain
        env["JARVIS_MCP_CONFIG"] = str(mcp_config_path)
        env.update(self._extra_env)
        return env

    async def stream(self, request: EngineRequest) -> AsyncIterator[EngineEvent]:
        resolved_bin = find_cli_binary(self._binary)
        if not resolved_bin:
            yield EngineEvent(
                type="error",
                content=f"[{self.provider_id}] Binary '{self._binary}' not found on PATH.",
                provider=self.provider_id,
                model=request.model,
            )
            return

        prompt = _compose_cli_prompt(request)
        mcp_cfg = {
            "mcpServers": {
                "jarvis": {
                    "type": "http",
                    "url": self._hub_url,
                    "headers": {
                        "Authorization": f"Bearer {self._hub_token}",
                        "X-Jarvis-Mode": self._run_mode,
                        "X-Jarvis-Brain": request.brain,
                        "X-Jarvis-Session": request.session_id,
                    },
                }
            }
        }

        with tempfile.NamedTemporaryFile("w", suffix=".json", prefix="jarvis_mcp_", delete=False, encoding="utf-8") as tf:
            json.dump(mcp_cfg, tf)
            mcp_cfg_path = Path(tf.name)
        try:
            os.chmod(mcp_cfg_path, 0o600)
        except OSError:
            pass

        proc: asyncio.subprocess.Process | None = None
        acc_text: list[str] = []
        try:
            cmd = self._build_command(prompt, request, mcp_cfg_path)
            cmd[0] = resolved_bin
            env = self._build_env(request, mcp_cfg_path)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self._cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert proc.stdout is not None and proc.stderr is not None

            while True:
                if request.cancel_event and request.cancel_event.is_set():
                    await _terminate_proc(proc)
                    yield EngineEvent(
                        type="final",
                        content="".join(acc_text),
                        provider=self.provider_id,
                        model=request.model,
                    )
                    return

                try:
                    raw_line = await asyncio.wait_for(proc.stdout.readline(), timeout=0.2)
                except asyncio.TimeoutError:
                    if proc.returncode is not None:
                        break
                    continue

                if not raw_line:
                    break

                line = self._redact(raw_line.decode("utf-8", errors="replace"))
                for ev in _parse_json_event(line, self.provider_id, request.model):
                    if ev.type == "text":
                        acc_text.append(ev.content)
                    yield ev
                    if ev.type == "error":
                        await _terminate_proc(proc)
                        return

            stderr_bytes = await proc.stderr.read()
            rc = await proc.wait()
            if rc != 0 and not acc_text:
                err_txt = self._redact(stderr_bytes.decode("utf-8", errors="replace")).strip()[:500]
                yield EngineEvent(
                    type="error",
                    content=f"[{self.provider_id}] CLI exited with code {rc}: {err_txt or 'no stderr'}",
                    provider=self.provider_id,
                    model=request.model,
                )
                return

            yield EngineEvent(
                type="final",
                content="".join(acc_text).strip(),
                provider=self.provider_id,
                model=request.model,
            )
        finally:
            if proc is not None and proc.returncode is None:
                await _terminate_proc(proc)
            try:
                mcp_cfg_path.unlink(missing_ok=True)
            except OSError:
                pass


class CodexCliEngine(BaseCliEngine):
    """Adapter for OpenAI Codex CLI (`codex exec --json`) running on the user's ChatGPT subscription."""

    provider_id = "codex-cli"

    def _build_command(self, prompt: str, request: EngineRequest, mcp_config_path: Path) -> list[str]:
        cmd = [self._binary, "exec", "--json"]
        if request.model and request.model != "codex-default":
            cmd += ["-m", request.model]
        # Wire Jarvis MCP Hub dynamically via Codex -c override
        cmd += [
            "-c",
            f'mcp_servers.jarvis.url="{self._hub_url}"',
            "-c",
            f'mcp_servers.jarvis.bearer_token_env_var="JARVIS_HUB_TOKEN"',
        ]
        cmd.append(prompt)
        return cmd


class AntigravityCliEngine(BaseCliEngine):
    """Adapter for Google Antigravity CLI (`agy`) running on the user's personal Google subscription."""

    provider_id = "antigravity-cli"

    def _build_command(self, prompt: str, request: EngineRequest, mcp_config_path: Path) -> list[str]:
        cmd = [self._binary, "--output-format", "stream-json"]
        if request.model and request.model not in {"agy-default", "default"}:
            cmd += ["--model", request.model]
        cmd += ["-p", prompt]
        return cmd
