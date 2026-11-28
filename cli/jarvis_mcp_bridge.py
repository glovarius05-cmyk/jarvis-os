#!/usr/bin/env python3
"""Jarvis OS - Stdio Model Context Protocol (MCP) Bridge for Cursor, VS Code & Claude Desktop.

This lightweight stdio bridge reads JSON-RPC 2.0 requests from stdin, forwards them to the
running Jarvis OS MCP Hub (http://127.0.0.1:7777/hub/mcp), and writes JSON-RPC responses to stdout.

Supports all standard MCP tools:
- Second Brain vault notes (vault_read, vault_write, vault_search, vault_edit)
- Multi-AI Consensus & Adversarial Debate (jarvis_consensus, jarvis_debate)
- Web search and SSRF-safe page fetch (web_search, web_fetch)
- Background task automation (jarvis_task, jarvis_schedule)
- Composio social media integrations (Twitter, Threads, LinkedIn, etc.)

No heavy external dependencies required; runs on standard library Python 3.10+.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_HUB_URL = "http://127.0.0.1:7777/hub/mcp"


def find_default_token(state_dir: Path | None = None) -> str:
    """Locate state/.hub_token automatically from known directories or environment."""
    env_token = os.getenv("JARVIS_HUB_TOKEN", "").strip()
    if env_token:
        return env_token

    search_dirs: list[Path] = []
    if state_dir:
        search_dirs.append(state_dir)

    env_state = os.getenv("JARVIS_STATE_DIR", "").strip()
    if env_state:
        search_dirs.append(Path(env_state))

    # Project root relative to cli/jarvis_mcp_bridge.py
    cli_dir = Path(__file__).resolve().parent
    project_root = cli_dir.parent
    search_dirs.append(project_root / "state")
    search_dirs.append(Path.home() / ".jarvis" / "state")

    for s_dir in search_dirs:
        candidate = s_dir / ".hub_token"
        if candidate.is_file():
            try:
                tok = candidate.read_text(encoding="utf-8").strip()
                if tok:
                    return tok
            except OSError:
                pass
    return ""


def send_hub_request(
    hub_url: str,
    token: str,
    payload: dict[str, Any],
    *,
    brain: str = "Brain Default",
    mode: str = "full",
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Forward a JSON-RPC request to the Jarvis MCP Hub HTTP endpoint."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "X-Jarvis-Brain": brain,
        "X-Jarvis-Mode": mode,
    }
    req = urllib.request.Request(hub_url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")[:300]
        return {
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "error": {
                "code": -32000,
                "message": f"Jarvis MCP Hub HTTP {exc.code}: {err_body}",
            },
        }
    except urllib.error.URLError as exc:
        return {
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "error": {
                "code": -32000,
                "message": (
                    f"Jarvis OS server is offline or unreachable at {hub_url}. "
                    f"Please verify Jarvis OS is running ({exc.reason})."
                ),
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "error": {
                "code": -32603,
                "message": f"Bridge internal error: {exc}",
            },
        }


def print_json_rpc(msg: dict[str, Any]) -> None:
    """Write a single JSON-RPC line to stdout and flush."""
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def generate_config_snippet(hub_url: str, token: str) -> None:
    """Print configuration snippet for Cursor (.cursor/mcp.json) and Claude Desktop."""
    python_path = sys.executable
    script_path = str(Path(__file__).resolve())
    cfg = {
        "mcpServers": {
            "jarvis": {
                "command": python_path,
                "args": [script_path],
                "env": {
                    "JARVIS_HUB_URL": hub_url,
                    "JARVIS_HUB_TOKEN": token,
                },
            }
        }
    }
    print(json.dumps(cfg, indent=2))


def run_bridge(
    hub_url: str,
    token: str,
    brain: str = "Brain Default",
    mode: str = "full",
    timeout: float = 120.0,
) -> int:
    """Read JSON-RPC from stdin, forward to Jarvis MCP Hub, and stream replies to stdout."""
    sys.stderr.write(f"[jarvis-mcp-bridge] Connected to Jarvis Hub at {hub_url} (mode={mode}, brain={brain})\n")
    sys.stderr.flush()

    stdin_reader = sys.stdin

    while True:
        line = stdin_reader.readline()
        if not line:
            break

        line_strip = line.strip()
        if not line_strip:
            continue

        # Support optional Content-Length: <n> framing (LSP format)
        if line_strip.lower().startswith("content-length:"):
            try:
                parts = line_strip.split(":", 1)
                content_len = int(parts[1].strip())
                # Read until empty line separating headers from body
                while True:
                    hdr = stdin_reader.readline()
                    if hdr in ("\r\n", "\n", ""):
                        break
                payload_str = stdin_reader.read(content_len)
                body = json.loads(payload_str)
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write(f"[jarvis-mcp-bridge] Framing parse error: {exc}\n")
                sys.stderr.flush()
                continue
        else:
            try:
                body = json.loads(line_strip)
            except json.JSONDecodeError as exc:
                sys.stderr.write(f"[jarvis-mcp-bridge] JSON decode error: {exc}\n")
                sys.stderr.flush()
                continue

        if not isinstance(body, dict):
            continue

        req_id = body.get("id")
        method = body.get("method", "")

        # Notifications (no 'id' present)
        if req_id is None:
            if method in {"notifications/initialized", "$/cancelRequest", "notifications/cancelled"}:
                # Expected standard notifications; ACK silently without responding
                continue
            # Forward notification without printing response
            send_hub_request(hub_url, token, body, brain=brain, mode=mode, timeout=timeout)
            continue

        # Handle ping locally for zero-latency health checks
        if method == "ping":
            print_json_rpc({"jsonrpc": "2.0", "id": req_id, "result": {}})
            continue

        # Forward request to Hub
        response = send_hub_request(hub_url, token, body, brain=brain, mode=mode, timeout=timeout)
        print_json_rpc(response)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Jarvis OS Stdio MCP Bridge for Cursor, VS Code, and Claude Desktop."
    )
    parser.add_argument(
        "--url",
        default=os.getenv("JARVIS_HUB_URL", DEFAULT_HUB_URL),
        help=f"Jarvis MCP Hub URL (default: {DEFAULT_HUB_URL})",
    )
    parser.add_argument(
        "--token",
        default="",
        help="MCP Hub Bearer token (defaults to JARVIS_HUB_TOKEN or state/.hub_token)",
    )
    parser.add_argument(
        "--state-dir",
        default="",
        help="Path to state/ directory to locate .hub_token automatically",
    )
    parser.add_argument(
        "--brain",
        default=os.getenv("JARVIS_BRAIN", "Brain Default"),
        help="Active Second Brain vault (default: 'Brain Default')",
    )
    parser.add_argument(
        "--mode",
        default=os.getenv("JARVIS_MODE") or os.getenv("JARVIS_RUN_MODE") or "full",
        choices=["suggest", "auto", "full"],
        help="Permission mode: suggest (readonly), auto (safe), full (all tools)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Tool execution timeout in seconds (default: 120.0)",
    )
    parser.add_argument(
        "--generate-config",
        action="store_true",
        help="Print ready-to-paste MCP config snippet for Cursor/Claude Desktop and exit",
    )

    args = parser.parse_args()

    state_path = Path(args.state_dir) if args.state_dir else None
    token = args.token or find_default_token(state_path)

    if args.generate_config:
        generate_config_snippet(args.url, token)
        return 0

    return run_bridge(
        hub_url=args.url,
        token=token,
        brain=args.brain,
        mode=args.mode,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
