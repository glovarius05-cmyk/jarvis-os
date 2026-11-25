"""Bundled plugin: jarvis-memory - persistent fact saving and hybrid recall."""

from __future__ import annotations

import re
import time


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", (text or "").lower(), flags=re.UNICODE)
    slug = re.sub(r"[\s_-]+", "-", cleaned).strip("-")[:48]
    return slug or f"fact-{int(time.time())}"


def register(ctx) -> None:
    def jarvis_memory_save(args: dict, brain: str) -> str:
        content = str(args.get("content") or "").strip()
        title = str(args.get("title") or "").strip()
        target = str(args.get("target") or "fact").strip().lower()
        if not content:
            return "ERROR: 'content' parameter is required."

        if target in ("core", "core_memory"):
            core_mem = ctx.services.get("core_memory")
            if core_mem is None:
                return "ERROR: CoreMemoryManager service is unavailable."
            section = str(args.get("section") or "Owner Preferences").strip()
            try:
                core_mem.append_entry(brain, section, content)
                return f"Saved to CORE_MEMORY.md under '{section}'."
            except Exception as exc:
                return f"ERROR: Failed to update CORE_MEMORY.md: {exc}"

        vaults = ctx.services.get("vaults")
        if vaults is None:
            return "ERROR: VaultManager service is unavailable."

        slug = _slugify(title or content[:40])
        rel_path = f"memory/facts/{slug}.md"
        heading = title or slug.replace("-", " ").title()
        note_body = f"# {heading}\n\n{content}\n"
        vaults.write_text(brain, rel_path, note_body)

        # Append link to memory/MEMORY.md if not already present
        try:
            mem_idx = vaults.read_text(brain, "memory/MEMORY.md")
        except OSError:
            mem_idx = "# Long-Term Memory Index\n"
        bullet = f"- [{heading}]({rel_path})"
        if rel_path not in mem_idx:
            updated_idx = mem_idx.rstrip() + f"\n{bullet}\n"
            vaults.write_text(brain, "memory/MEMORY.md", updated_idx)

        return f"Saved durable memory fact to `{rel_path}` and indexed in `memory/MEMORY.md`."

    def jarvis_memory_recall(args: dict, brain: str) -> str:
        query = str(args.get("query") or "").strip()
        limit = int(args.get("limit") or 6)
        if not query:
            return "ERROR: 'query' parameter is required."

        lines: list[str] = []
        hybrid = ctx.services.get("hybrid_index")
        if hybrid is not None:
            try:
                hits = hybrid.search(brain, query, limit=max(1, min(limit, 15)))
                for h in hits:
                    if isinstance(h, dict):
                        lines.append(
                            f"- [{h.get('path')} > {h.get('heading')}] (score={h.get('score')}, {h.get('match_type')}): {h.get('snippet')}"
                        )
            except Exception:
                pass

        vaults = ctx.services.get("vaults")
        if not lines and vaults is not None:
            try:
                facts_dir = vaults.brain_path(brain) / "memory" / "facts"
                q_low = query.lower()
                if facts_dir.exists():
                    for p in sorted(facts_dir.glob("*.md")):
                        txt = p.read_text(encoding="utf-8", errors="ignore")
                        if q_low in txt.lower():
                            lines.append(f"- [memory/facts/{p.name}]: {txt.strip()[:220]}")
            except Exception:
                pass

        exp = ctx.services.get("experience")
        if exp is not None:
            try:
                ex_hits = exp.search(brain=brain, query=query, limit=3)
                for ex in ex_hits:
                    lines.append(f"- [Experience] {ex.title}: {ex.lesson}")
            except Exception:
                pass

        if not lines:
            return f"No matching memories found in '{brain}' for '{query}'."
        return f"Recalled memories for '{query}':\n" + "\n".join(lines)

    ctx.register_tool(
        name="jarvis_memory_save",
        description="Save a durable fact to memory/facts/ (indexed in MEMORY.md) or append a rule to CORE_MEMORY.md.",
        handler=jarvis_memory_save,
        min_mode="safe",
        schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short title for the memory fact"},
                "content": {"type": "string", "description": "Fact, preference, or knowledge to persist"},
                "target": {
                    "type": "string",
                    "enum": ["fact", "core"],
                    "description": "'fact' saves to memory/facts/<slug>.md; 'core' appends to CORE_MEMORY.md",
                    "default": "fact",
                },
                "section": {"type": "string", "description": "Section name when target='core'"},
            },
            "required": ["content"],
        },
    )

    ctx.register_tool(
        name="jarvis_memory_recall",
        description="Search Second Brain notes and past experiences using hybrid BM25 + semantic recall.",
        handler=jarvis_memory_recall,
        min_mode="readonly",
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query or topic to recall"},
                "limit": {"type": "integer", "description": "Max results (default: 6)", "default": 6},
            },
            "required": ["query"],
        },
    )
