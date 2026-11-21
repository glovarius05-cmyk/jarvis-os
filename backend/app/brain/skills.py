"""Skill discovery, system skill sync (hash-manifest protected), and lazy loading.

Enforces the strict 150-character description budget so the skill index injected into
every system prompt stays compact and predictable.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.brain.vault import VaultManager, safe_resolve

SKILL_DESC_MAX = 150
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


@dataclass(frozen=True)
class SkillInfo:
    slug: str
    name: str
    description: str
    group: str
    enabled: bool
    is_system: bool
    path: str


def parse_markdown_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a Markdown file, returning (meta_dict, body)."""
    match = _FRONTMATTER_RE.match(text or "")
    if not match:
        return {}, (text or "").strip()
    raw_yaml, body = match.group(1), match.group(2)
    try:
        parsed = yaml.safe_load(raw_yaml)
    except yaml.YAMLError:
        parsed = {}
    return (parsed if isinstance(parsed, dict) else {}), body.strip()


def dump_markdown_frontmatter(meta: dict[str, Any], body: str) -> str:
    dumped = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{dumped}\n---\n\n{body.strip()}\n"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class SkillStore:
    """Manages system and user skills inside each Second Brain vault."""

    def __init__(self, code_dir: Path, vaults: VaultManager) -> None:
        self._system_skills_dir = code_dir / "system" / "skills"
        self._vaults = vaults

    def system_slugs(self) -> set[str]:
        if not self._system_skills_dir.exists():
            return set()
        return {
            p.name
            for p in self._system_skills_dir.iterdir()
            if p.is_dir() and (p / "SKILL.md").is_file()
        }

    def sync_system_skills(self, brain: str) -> list[str]:
        """Copy bundled system skills into <brain>/skills/ without overwriting user edits."""
        if not self._system_skills_dir.exists():
            return []

        root = self._vaults.brain_path(brain)
        manifest_path = root / ".jarvis" / "system-manifest.json"
        manifest: dict[str, str] = {}
        if manifest_path.exists():
            try:
                loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    manifest = {str(k): str(v) for k, v in loaded.items()}
            except (OSError, json.JSONDecodeError):
                manifest = {}

        synced: list[str] = []
        for skill_dir in sorted(self._system_skills_dir.iterdir()):
            if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").is_file():
                continue
            for src_file in skill_dir.rglob("*"):
                if not src_file.is_file():
                    continue
                rel_inside = src_file.relative_to(self._system_skills_dir).as_posix()
                rel_vault = f"skills/{rel_inside}"
                dst = safe_resolve(root, rel_vault)
                src_bytes = src_file.read_bytes()
                src_hash = _sha256(src_bytes)

                if dst.exists():
                    cur_hash = _sha256(dst.read_bytes())
                    prev_installed_hash = manifest.get(rel_vault)
                    # Only update if the user has NOT edited the file since last system sync
                    if prev_installed_hash and cur_hash != prev_installed_hash:
                        continue
                    if cur_hash == src_hash:
                        manifest[rel_vault] = src_hash
                        continue

                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src_bytes)
                manifest[rel_vault] = src_hash
                if rel_inside.endswith("/SKILL.md"):
                    synced.append(skill_dir.name)

        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return synced

    def list_skills(self, brain: str) -> list[SkillInfo]:
        self.sync_system_skills(brain)
        root = self._vaults.brain_path(brain)
        skills_dir = root / "skills"
        if not skills_dir.exists():
            return []

        sys_slugs = self.system_slugs()
        items: list[SkillInfo] = []
        for entry in sorted(skills_dir.iterdir(), key=lambda p: p.name.lower()):
            skill_file = entry / "SKILL.md"
            if not entry.is_dir() or not skill_file.is_file():
                continue
            raw = skill_file.read_text(encoding="utf-8", errors="replace")
            meta, _ = parse_markdown_frontmatter(raw)
            desc = str(meta.get("description") or "").strip()[:SKILL_DESC_MAX]
            items.append(
                SkillInfo(
                    slug=entry.name,
                    name=str(meta.get("name") or entry.name),
                    description=desc,
                    group=str(meta.get("group") or "General"),
                    enabled=bool(meta.get("enabled", True)),
                    is_system=entry.name in sys_slugs,
                    path=f"skills/{entry.name}/SKILL.md",
                )
            )
        return items

    def read_skill_body(self, brain: str, slug: str) -> str | None:
        if not _SLUG_RE.match(slug or ""):
            return None
        root = self._vaults.brain_path(brain)
        target = safe_resolve(root, f"skills/{slug}/SKILL.md")
        if not target.is_file():
            return None
        return target.read_text(encoding="utf-8", errors="replace")

    def toggle_skill(self, brain: str, slug: str, enabled: bool) -> SkillInfo | None:
        if not _SLUG_RE.match(slug or ""):
            return None
        root = self._vaults.brain_path(brain)
        target = safe_resolve(root, f"skills/{slug}/SKILL.md")
        if not target.is_file():
            return None
        raw = target.read_text(encoding="utf-8", errors="replace")
        meta, body = parse_markdown_frontmatter(raw)
        meta["enabled"] = bool(enabled)
        target.write_text(dump_markdown_frontmatter(meta, body), encoding="utf-8")
        for info in self.list_skills(brain):
            if info.slug == slug:
                return info
        return None

    def save_skill(
        self,
        brain: str,
        slug: str,
        name: str,
        description: str,
        instructions: str,
        group: str = "Custom",
        enabled: bool = True,
    ) -> SkillInfo:
        """Create or update a skill in the active Second Brain vault."""
        norm_slug = (slug or "").strip().lower()
        if not _SLUG_RE.match(norm_slug):
            raise ValueError(f"Invalid skill slug '{slug}': must be lowercase alphanumeric and hyphens (1-64 chars).")

        clean_desc = (description or "").strip()[:SKILL_DESC_MAX]
        if not clean_desc:
            raise ValueError("Skill description cannot be empty.")

        root = self._vaults.brain_path(brain)
        target = safe_resolve(root, f"skills/{norm_slug}/SKILL.md")
        target.parent.mkdir(parents=True, exist_ok=True)

        meta = {
            "name": (name or norm_slug).strip(),
            "description": clean_desc,
            "group": (group or "Custom").strip(),
            "enabled": bool(enabled),
        }
        content = dump_markdown_frontmatter(meta, instructions.strip())
        target.write_text(content, encoding="utf-8")

        return SkillInfo(
            slug=norm_slug,
            name=meta["name"],
            description=clean_desc,
            group=meta["group"],
            enabled=meta["enabled"],
            is_system=norm_slug in self.system_slugs(),
            path=f"skills/{norm_slug}/SKILL.md",
        )

    def build_prompt_index(self, brain: str, compact: bool = False) -> str:
        """Compact skill index for system prompt injection (descriptions capped at 150 chars)."""
        enabled_skills = [s for s in self.list_skills(brain) if s.enabled]
        if not enabled_skills:
            return ""
        if compact:
            slugs = ", ".join(f"`{s.slug}`" for s in enabled_skills)
            return f"Call `use_skill(slug=...)` to load instructions for: {slugs}"
        lines = [
            f"- `{s.slug}` ({s.group}): {s.description[:SKILL_DESC_MAX]}"
            for s in enabled_skills
        ]
        return (
            "Call `use_skill(slug=...)` to load the full instructions for any relevant skill:\n"
            + "\n".join(lines)
        )
