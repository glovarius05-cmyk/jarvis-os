"""Bundled plugin: jarvis_plugin - list, read, and write user plugins safely (always disabled by default)."""

from __future__ import annotations

import json


def register(ctx) -> None:
    def jarvis_plugin(args: dict, brain: str) -> str:
        host = ctx.services.get("plugin_host")
        if host is None:
            return "ERROR: PluginHost unavailable."
        op = str(args.get("op") or "list").strip().lower()

        if op == "list":
            rows = [p.to_public_dict() for p in host.load_all(brain)]
            return json.dumps(rows, ensure_ascii=False, indent=2)

        if op == "read":
            slug = str(args.get("slug") or "").strip().lower()
            for p in host.load_all(brain):
                if p.slug == slug:
                    out = {}
                    for fname in ("plugin.yaml", "plugin.py"):
                        fp = p.directory / fname
                        if fp.is_file():
                            out[fname] = fp.read_text(encoding="utf-8", errors="replace")
                    return json.dumps(out, ensure_ascii=False, indent=2)
            return f"ERROR: Plugin '{slug}' not found."

        if op == "write":
            slug = str(args.get("slug") or "").strip().lower()
            files = args.get("files")
            scope = str(args.get("scope") or "global").strip().lower()
            if not isinstance(files, dict):
                return "ERROR: files must be an object mapping relative paths to file contents."
            _ok, msg = host.write_user_plugin(
                slug=slug,
                files={str(k): str(v) for k, v in files.items()},
                scope=scope,
                brain=brain,
            )
            return msg

        return f"ERROR: Unsupported op '{op}'. Use list, read, or write."

    ctx.register_tool(
        name="jarvis_plugin",
        description="Manage native Python plugins (op=list|read|write). New/edited plugins are always saved with enabled=false.",
        handler=jarvis_plugin,
        min_mode="safe",
        schema={
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["list", "read", "write"]},
                "slug": {"type": "string"},
                "scope": {"type": "string", "enum": ["global", "vault"], "default": "global"},
                "files": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            "required": ["op"],
        },
    )
