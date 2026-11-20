"""Second Brain Knowledge Graph builder extracting [[wikilinks]] across Markdown notes."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")


def _classify_group(rel_path: str) -> str:
    top = rel_path.split("/", 1)[0].lower() if "/" in rel_path else ""
    if top in {"memory", "wiki", "sources", "agents", "workflows", "skills", "javis"}:
        return top
    return "root"


def build_knowledge_graph(vault_root: Path) -> dict[str, Any]:
    """Scan all Markdown files in vault_root and return {nodes, edges, stats}."""
    root = vault_root.resolve()
    md_files = sorted(
        (p for p in root.rglob("*.md") if ".git" not in p.parts and ".jarvis" not in p.parts),
        key=lambda p: p.as_posix().lower(),
    )

    # Map normalized stems and relative paths to canonical node id (relative POSIX path)
    nodes_by_id: dict[str, dict[str, Any]] = {}
    lookup_by_stem: dict[str, str] = {}
    lookup_by_rel_no_ext: dict[str, str] = {}

    for p in md_files:
        rel = p.relative_to(root).as_posix()
        rel_no_ext = rel[:-3] if rel.endswith(".md") else rel
        nodes_by_id[rel] = {
            "id": rel,
            "label": p.stem,
            "group": _classify_group(rel),
            "degree": 0,
            "exists": True,
        }
        lookup_by_rel_no_ext[rel_no_ext.lower()] = rel
        lookup_by_stem.setdefault(p.stem.lower(), rel)

    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str]] = set()

    for p in md_files:
        src_id = p.relative_to(root).as_posix()
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _WIKILINK_RE.finditer(content):
            raw_target = match.group(1).strip()
            if not raw_target:
                continue
            key = raw_target.lower().removesuffix(".md")
            target_id = lookup_by_rel_no_ext.get(key) or lookup_by_stem.get(Path(key).name.lower())
            if target_id is None:
                # Represent unresolved wikilinks as phantom nodes so the graph highlights missing notes
                target_id = f"unresolved:{raw_target}"
                if target_id not in nodes_by_id:
                    nodes_by_id[target_id] = {
                        "id": target_id,
                        "label": raw_target,
                        "group": "unresolved",
                        "degree": 0,
                        "exists": False,
                    }
            if target_id == src_id:
                continue
            pair = (src_id, target_id)
            if pair not in seen_edges:
                seen_edges.add(pair)
                edges.append({"source": src_id, "target": target_id})
                nodes_by_id[src_id]["degree"] += 1
                nodes_by_id[target_id]["degree"] += 1

    nodes = list(nodes_by_id.values())
    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "total_notes": len(md_files),
            "total_nodes": len(nodes),
            "total_edges": len(edges),
        },
    }
