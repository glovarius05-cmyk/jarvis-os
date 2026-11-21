"""Self-editing Core Memory in the style of Letta / MemGPT.

Why this exists: Traditional RAG or static files are not actively maintained by the LLM.
Core Memory maintains structured, persistent, always-in-context memory blocks
(User Profile, Assistant Persona, Context & Goals) that the model inspects on every turn
and self-edits dynamically whenever the user shares facts, preferences, or rules.
"""

from __future__ import annotations

import re
from typing import Any

from app.brain.vault import VaultManager

CORE_MEMORY_PATH = "memory/CORE_MEMORY.md"

DEFAULT_CORE_MEMORY = """# Core Memory (Always-in-Context)

## User Profile
- Language: Russian (ru)
- Communication style: Concise, lead with conclusions, no em-dashes (use hyphens)
- Technical stack: Python, TypeScript, React, Docker, Linux/macOS

## Persona
- Identity: Jarvis, personal autonomous AI assistant for engineering, business, and daily operations
- Principles: High agency, verified accuracy, transparent about background tasks, zero fabricated metrics

## Context & Goals
- Project: Jarvis OS
- Focus: Self-enhancing operating system and autonomous second brain
"""

SECTION_TITLES = {
    "user": "User Profile",
    "persona": "Persona",
    "context": "Context & Goals",
}


class CoreMemoryManager:
    """Manages active self-editing Core Memory blocks inside the active Second Brain."""

    def __init__(self, vaults: VaultManager) -> None:
        self._vaults = vaults

    def get_raw(self, brain: str) -> str:
        """Read CORE_MEMORY.md or auto-scaffold it if missing."""
        try:
            content = self._vaults.read_text(brain, CORE_MEMORY_PATH).strip()
            if content:
                return content
        except OSError:
            pass
        self._vaults.write_text(brain, CORE_MEMORY_PATH, DEFAULT_CORE_MEMORY)
        return DEFAULT_CORE_MEMORY.strip()

    def parse_sections(self, brain: str) -> dict[str, list[str]]:
        """Parse Core Memory bullet points by section (user, persona, context)."""
        raw = self.get_raw(brain)
        sections: dict[str, list[str]] = {"user": [], "persona": [], "context": []}
        current_section = ""
        for line in raw.splitlines():
            sline = line.strip()
            if sline.startswith("## User"):
                current_section = "user"
            elif sline.startswith("## Persona"):
                current_section = "persona"
            elif sline.startswith("## Context") or sline.startswith("## Goal"):
                current_section = "context"
            elif sline.startswith("##"):
                current_section = ""
            elif current_section and sline.startswith(("-", "*")):
                item = sline.lstrip("-* ").strip()
                if item and not item.startswith("(Empty"):
                    sections[current_section].append(item)
        return sections

    def render_markdown(self, sections: dict[str, list[str]]) -> str:
        """Render sections back to standard Markdown."""
        lines = ["# Core Memory (Always-in-Context)", ""]
        for key in ("user", "persona", "context"):
            title = SECTION_TITLES.get(key, key.capitalize())
            lines.append(f"## {title}")
            items = sections.get(key) or []
            if not items:
                lines.append("- (Empty)")
            else:
                for item in items:
                    lines.append(f"- {item}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def remember(self, brain: str, fact: str, section: str = "user") -> str:
        """Add or update a fact in Core Memory."""
        norm_sec = section.lower().strip()
        if norm_sec not in SECTION_TITLES:
            norm_sec = "user"
        clean_fact = fact.strip().lstrip("-* ")
        if not clean_fact:
            return "Error: Cannot remember an empty fact."

        sections = self.parse_sections(brain)
        items = sections.setdefault(norm_sec, [])

        # Check for key: value update (e.g. "Language: English" replaces "Language: Russian")
        updated = False
        if ":" in clean_fact:
            prefix = clean_fact.split(":", 1)[0].strip().lower()
            for idx, existing in enumerate(items):
                if ":" in existing and existing.split(":", 1)[0].strip().lower() == prefix:
                    items[idx] = clean_fact
                    updated = True
                    break

        if not updated:
            if clean_fact in items:
                return f"[Core Memory] Fact already present in '{SECTION_TITLES[norm_sec]}': {clean_fact}"
            items.append(clean_fact)

        md = self.render_markdown(sections)
        self._vaults.write_text(brain, CORE_MEMORY_PATH, md)
        verb = "Updated" if updated else "Added to"
        return f"[Core Memory] {verb} '{SECTION_TITLES[norm_sec]}': {clean_fact}"

    def forget(self, brain: str, query: str, section: str = "user") -> str:
        """Remove facts matching query from Core Memory."""
        norm_sec = section.lower().strip()
        clean_query = query.strip().lower()
        if not clean_query:
            return "Error: Query cannot be empty."

        sections = self.parse_sections(brain)
        target_sections = [norm_sec] if norm_sec in SECTION_TITLES else ["user", "persona", "context"]

        removed_count = 0
        removed_items: list[str] = []
        for sec in target_sections:
            items = sections.get(sec, [])
            retained = []
            for it in items:
                if clean_query in it.lower():
                    removed_count += 1
                    removed_items.append(f"[{SECTION_TITLES.get(sec, sec)}] {it}")
                else:
                    retained.append(it)
            sections[sec] = retained

        if removed_count == 0:
            return f"[Core Memory] No facts matching '{query}' found to remove."

        md = self.render_markdown(sections)
        self._vaults.write_text(brain, CORE_MEMORY_PATH, md)
        return f"[Core Memory] Removed {removed_count} fact(s): " + "; ".join(removed_items)

    def replace(self, brain: str, section: str, old_content: str, new_content: str) -> str:
        """Replace exact or partial content in a section."""
        norm_sec = section.lower().strip()
        if norm_sec not in SECTION_TITLES:
            norm_sec = "user"
        sections = self.parse_sections(brain)
        items = sections.get(norm_sec, [])

        old_clean = old_content.strip().lower()
        new_clean = new_content.strip().lstrip("-* ")
        replaced = False
        for idx, item in enumerate(items):
            if old_clean in item.lower():
                items[idx] = new_clean
                replaced = True
                break

        if not replaced:
            items.append(new_clean)

        md = self.render_markdown(sections)
        self._vaults.write_text(brain, CORE_MEMORY_PATH, md)
        return f"[Core Memory] Successfully updated '{SECTION_TITLES[norm_sec]}'."

    def profile_user(self, brain: str, facts: list[dict[str, str]]) -> list[str]:
        """Proactively record dialectic user traits or preferences into Core Memory (Hermes/Honcho-style)."""
        results: list[str] = []
        for item in facts:
            fact_text = str(item.get("fact") or "").strip()
            section = str(item.get("section") or "user").strip()
            if fact_text:
                res = self.remember(brain, fact_text, section=section)
                results.append(res)
        return results

    def view(self, brain: str) -> dict[str, Any]:
        """Return structured and raw Core Memory status."""
        raw = self.get_raw(brain)
        sections = self.parse_sections(brain)
        return {
            "brain": brain,
            "raw": raw,
            "sections": sections,
            "char_count": len(raw),
            "total_facts": sum(len(v) for v in sections.values()),
        }
