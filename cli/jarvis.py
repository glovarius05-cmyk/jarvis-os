#!/usr/bin/env python3
"""Jarvis OS command-line client (`python cli/jarvis.py "your prompt"`)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="Talk to your self-hosted Jarvis OS from the terminal.")
    parser.add_argument("prompt", nargs="*", help="Prompt or slash command (/new, /status)")
    parser.add_argument("--url", default=os.getenv("JARVIS_URL", "http://127.0.0.1:7777"), help="Jarvis server URL")
    parser.add_argument("--peer", default="local-cli", help="CLI peer session identifier")
    parser.add_argument("--mode", default="full", choices=["suggest", "auto", "full"], help="MCP Hub permission mode")
    args = parser.parse_args()

    text = " ".join(args.prompt).strip()
    if not text:
        if not sys.stdin.isatty():
            text = sys.stdin.read().strip()
        else:
            parser.print_help()
            return 1

    endpoint = f"{args.url.rstrip('/')}/api/chat/cli"
    payload = json.dumps({"prompt": text, "peer_id": args.peer, "mode": args.mode}).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(data.get("reply") or "")
            return 0
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"Error connecting to {endpoint}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
