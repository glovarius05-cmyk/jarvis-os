"""Plugin Host for bundled and user Python plugins (native tools + pre/post tool hooks).

Safety invariants (enforced in code, never in prompts):
- User plugins created from chat ALWAYS start with `enabled: false`; only the human
  can enable them in the UI (`toggle_plugin`).
- Setting `JARVIS_ENABLE_USER_PLUGINS=false` disables all global/vault user plugins.
- Reserved built-in tool names cannot be shadowed by any plugin.
- Every plugin load, tool call, and hook runs inside try/except so one buggy plugin
  can never take down the MCP Hub.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from app.brain.vault import VaultManager, safe_resolve
from app.hub.builtin_tools import ToolDefinition
from app.hub.permissions import PermLevel

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_TOOL_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_VALID_HOOKS = frozenset({"pre_tool_call", "post_tool_call"})

RESERVED_TOOLS = frozenset(
    {
        "vault_read",
        "vault_list",
        "vault_search",
        "vault_write",
        "vault_edit",
        "web_search",
        "web_fetch",
        "jarvis_query",
        "jarvis_connections",
        "jarvis_mcp_connect",
    }
)


def user_plugins_allowed() -> bool:
    raw = (os.getenv("JARVIS_ENABLE_USER_PLUGINS") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


@dataclass
class PluginRecord:
    slug: str
    name: str
    description: str
    version: str
    scope: str  # "bundled" | "global" | "vault"
    enabled: bool
    min_mode: str
    directory: Path
    tools: list[ToolDefinition] = field(default_factory=list)
    hooks: dict[str, list[Callable[..., Any]]] = field(default_factory=dict)
    error: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "scope": self.scope,
            "enabled": self.enabled,
            "min_mode": self.min_mode,
            "tools": [t.name for t in self.tools],
            "hooks": sorted(self.hooks.keys()),
            "error": self.error,
        }


class PluginContext:
    """Context object passed to each plugin's `register(ctx)` entrypoint."""

    def __init__(
        self,
        *,
        slug: str,
        scope: str,
        min_mode: PermLevel,
        data_dir: Path,
        services: dict[str, Any],
    ) -> None:
        self.slug = slug
        self.scope = scope
        self.min_mode = min_mode
        self.data_dir = data_dir
        self.services = services
        self._tools: list[ToolDefinition] = []
        self._hooks: dict[str, list[Callable[..., Any]]] = {}

    def register_tool(
        self,
        *,
        name: str,
        description: str,
        handler: Callable[[dict[str, Any], str], Any],
        min_mode: str | None = None,
        schema: dict[str, Any] | None = None,
    ) -> None:
        if not _TOOL_RE.match(name or ""):
            raise ValueError(f"Invalid plugin tool name: {name}")
        if name in RESERVED_TOOLS:
            raise ValueError(f"Plugin '{self.slug}' cannot shadow reserved built-in tool '{name}'")

        req_perm = PermLevel.parse(min_mode, self.min_mode) if min_mode else self.min_mode
        # A tool cannot downgrade below its plugin manifest's min_mode floor
        effective_req = PermLevel(max(int(req_perm), int(self.min_mode)))

        async def _wrapped(args: dict[str, Any], brain: str) -> str:
            res = handler(args, brain)
            if inspect.isawaitable(res):
                res = await res
            if isinstance(res, str):
                return res
            return json.dumps(res, ensure_ascii=False)

        self._tools.append(
            ToolDefinition(
                name=name,
                description=description,
                parameters=schema or {"type": "object", "properties": {}},
                required_perm=effective_req,
                source=f"plugin:{self.slug}",
                handler=_wrapped,
            )
        )

    def register_hook(self, event: str, callback: Callable[..., Any]) -> None:
        if event not in _VALID_HOOKS:
            raise ValueError(f"Unsupported hook event '{event}'. Allowed: {sorted(_VALID_HOOKS)}")
        self._hooks.setdefault(event, []).append(callback)


class PluginHost:
    """Discovers, isolates, and manages bundled + user plugins and tool hooks."""

    def __init__(
        self,
        *,
        code_dir: Path,
        state_dir: Path,
        vaults: VaultManager,
        services: dict[str, Any] | None = None,
    ) -> None:
        self._bundled_dir = code_dir / "backend" / "plugins_bundled"
        self._global_dir = state_dir / "plugins"
        self._data_root = state_dir / "plugin-data"
        self._state_path = state_dir / "plugins.json"
        self._vaults = vaults
        self._services = services if services is not None else {}
        self._lock = threading.RLock()
        self._services["plugin_host"] = self

    def bundled_slugs(self) -> set[str]:
        if not self._bundled_dir.exists():
            return set()
        return {
            p.name
            for p in self._bundled_dir.iterdir()
            if p.is_dir() and (p / "plugin.yaml").is_file()
        }

    def _read_overrides(self) -> dict[str, bool]:
        if not self._state_path.exists():
            return {}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return {str(k): bool(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_overrides(self, overrides: dict[str, bool]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(overrides, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self._state_path)

    def _iter_plugin_dirs(self, brain: str = "") -> list[tuple[str, Path]]:
        entries: list[tuple[str, Path]] = []
        if self._bundled_dir.exists():
            for p in sorted(self._bundled_dir.iterdir(), key=lambda x: x.name.lower()):
                if p.is_dir() and (p / "plugin.yaml").is_file():
                    entries.append(("bundled", p))

        if user_plugins_allowed():
            if self._global_dir.exists():
                for p in sorted(self._global_dir.iterdir(), key=lambda x: x.name.lower()):
                    if p.is_dir() and (p / "plugin.yaml").is_file():
                        entries.append(("global", p))
            if brain:
                try:
                    vdir = self._vaults.brain_path(brain) / "plugins"
                    if vdir.exists():
                        for p in sorted(vdir.iterdir(), key=lambda x: x.name.lower()):
                            if p.is_dir() and (p / "plugin.yaml").is_file():
                                entries.append(("vault", p))
                except ValueError:
                    pass
        return entries

    def load_all(self, brain: str = "") -> list[PluginRecord]:
        with self._lock:
            overrides = self._read_overrides()
            seen: set[str] = set()
            records: list[PluginRecord] = []

            for scope, pdir in self._iter_plugin_dirs(brain):
                try:
                    manifest = yaml.safe_load((pdir / "plugin.yaml").read_text(encoding="utf-8")) or {}
                    if not isinstance(manifest, dict):
                        manifest = {}
                except Exception as exc:  # noqa: BLE001
                    records.append(
                        PluginRecord(
                            slug=pdir.name,
                            name=pdir.name,
                            description="",
                            version="0.0.0",
                            scope=scope,
                            enabled=False,
                            min_mode="readonly",
                            directory=pdir,
                            error=f"Invalid plugin.yaml: {exc}",
                        )
                    )
                    continue

                slug = str(manifest.get("slug") or pdir.name).strip().lower()
                if slug in seen or not _SLUG_RE.match(slug):
                    continue
                seen.add(slug)

                manifest_enabled = bool(manifest.get("enabled", False))
                enabled = overrides.get(slug, manifest_enabled) if scope == "bundled" else manifest_enabled
                min_mode_enum = PermLevel.parse(str(manifest.get("min_mode") or "readonly"), PermLevel.READONLY)

                rec = PluginRecord(
                    slug=slug,
                    name=str(manifest.get("name") or slug),
                    description=str(manifest.get("description") or ""),
                    version=str(manifest.get("version") or "1.0.0"),
                    scope=scope,
                    enabled=enabled,
                    min_mode=min_mode_enum.label(),
                    directory=pdir,
                )

                if enabled:
                    self._load_module_into(rec, min_mode_enum)
                records.append(rec)

            return records

    def _load_module_into(self, rec: PluginRecord, min_mode: PermLevel) -> None:
        entry = rec.directory / "plugin.py"
        if not entry.is_file():
            rec.error = "Missing plugin.py"
            return

        data_dir = self._data_root / rec.slug
        data_dir.mkdir(parents=True, exist_ok=True)
        ctx = PluginContext(
            slug=rec.slug,
            scope=rec.scope,
            min_mode=min_mode,
            data_dir=data_dir,
            services=self._services,
        )
        try:
            mod_name = f"jarvis_plugin_{rec.slug.replace('-', '_').replace('.', '_')}"
            spec = importlib.util.spec_from_file_location(mod_name, entry)
            if spec is None or spec.loader is None:
                raise RuntimeError("Could not build module spec")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            reg_fn = getattr(module, "register", None)
            if not callable(reg_fn):
                raise RuntimeError("plugin.py must define register(ctx)")
            reg_fn(ctx)
            rec.tools = ctx._tools
            rec.hooks = ctx._hooks
        except Exception as exc:  # noqa: BLE001
            rec.error = f"Load failed: {exc}"
            rec.tools = []
            rec.hooks = {}

    def get_tools(self, brain: str = "") -> list[ToolDefinition]:
        tools: list[ToolDefinition] = []
        for rec in self.load_all(brain):
            if rec.enabled and not rec.error:
                tools.extend(rec.tools)
        return tools

    async def fire_hooks(self, event: str, brain: str, payload: dict[str, Any]) -> None:
        """Fire pre_tool_call / post_tool_call hooks with full error isolation."""
        for rec in self.load_all(brain):
            if not rec.enabled or rec.error:
                continue
            for cb in rec.hooks.get(event) or ():
                try:
                    res = cb(**payload)
                    if inspect.isawaitable(res):
                        await res
                except Exception:  # noqa: BLE001
                    continue

    def toggle_plugin(self, slug: str, enabled: bool, brain: str = "") -> PluginRecord | None:
        clean = slug.strip().lower()
        with self._lock:
            for rec in self.load_all(brain):
                if rec.slug != clean:
                    continue
                if rec.scope == "bundled":
                    overrides = self._read_overrides()
                    overrides[clean] = bool(enabled)
                    self._write_overrides(overrides)
                else:
                    yaml_path = rec.directory / "plugin.yaml"
                    manifest = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
                    manifest["enabled"] = bool(enabled)
                    yaml_path.write_text(
                        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
                        encoding="utf-8",
                    )
                for updated in self.load_all(brain):
                    if updated.slug == clean:
                        return updated
        return None

    def write_user_plugin(
        self,
        *,
        slug: str,
        files: dict[str, str],
        scope: str = "global",
        brain: str = "",
    ) -> tuple[bool, str]:
        """Write or update a user plugin with syntax validation and MANDATORY `enabled: false`."""
        clean_slug = (slug or "").strip().lower()
        if not _SLUG_RE.match(clean_slug):
            return False, "ERROR: Invalid plugin slug (use lowercase ASCII alphanumeric/hyphen/underscore)."
        if clean_slug in self.bundled_slugs():
            return False, f"ERROR: Cannot overwrite or clone bundled system plugin '{clean_slug}'."
        if scope not in {"global", "vault"}:
            return False, "ERROR: scope must be 'global' or 'vault'."
        if not files:
            return False, "ERROR: files mapping cannot be empty."

        if scope == "global":
            base_dir = self._global_dir / clean_slug
        else:
            if not brain:
                return False, "ERROR: brain is required for scope='vault'."
            base_dir = safe_resolve(self._vaults.brain_path(brain), f"plugins/{clean_slug}")

        # Validate filenames and Python syntax BEFORE touching disk
        validated_files: dict[str, str] = {}
        for rel_name, content in files.items():
            clean_rel = rel_name.strip().lstrip("/")
            if ".." in Path(clean_rel).parts or not clean_rel:
                return False, f"ERROR: Unsafe plugin file path: {rel_name}"
            if Path(clean_rel).suffix.lower() not in {".py", ".yaml", ".yml", ".md", ".txt", ".json"}:
                return False, f"ERROR: Disallowed file extension in plugin: {clean_rel}"
            if clean_rel.endswith(".py"):
                try:
                    compile(content, clean_rel, "exec")
                except SyntaxError as exc:
                    return False, f"ERROR: Python syntax error in {clean_rel} line {exc.lineno}: {exc.msg}"
            validated_files[clean_rel] = content

        if not base_dir.exists():
            if "plugin.yaml" not in validated_files or "plugin.py" not in validated_files:
                return False, "ERROR: New plugin must include both 'plugin.yaml' and 'plugin.py'."

        # Force enabled: false whenever creating a plugin or modifying plugin.yaml / any .py file
        if "plugin.yaml" in validated_files:
            try:
                manifest = yaml.safe_load(validated_files["plugin.yaml"]) or {}
                if not isinstance(manifest, dict):
                    return False, "ERROR: plugin.yaml must be a YAML mapping."
            except yaml.YAMLError as exc:
                return False, f"ERROR: Invalid plugin.yaml: {exc}"
            manifest["slug"] = clean_slug
            manifest["enabled"] = False  # HARD SAFETY: chat cannot self-enable executable Python
            validated_files["plugin.yaml"] = yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)
        elif any(k.endswith(".py") for k in validated_files) and (base_dir / "plugin.yaml").exists():
            manifest = yaml.safe_load((base_dir / "plugin.yaml").read_text(encoding="utf-8")) or {}
            manifest["enabled"] = False
            validated_files["plugin.yaml"] = yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)

        base_dir.mkdir(parents=True, exist_ok=True)
        for rel_name, content in validated_files.items():
            target = safe_resolve(base_dir, rel_name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        return (
            True,
            f"Saved user plugin '{clean_slug}' in scope='{scope}' with enabled=false. "
            "Review the code and enable it on the Studio -> Plugins page before use.",
        )
