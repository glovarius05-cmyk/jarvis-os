---
name: Vault Curator
description: "Curate Second Brain knowledge graph: resolve broken wikilinks, identify orphan notes, and generate Maps of Content (MOC)."
group: Knowledge
---

# Vault Curator

## When to use
Activate when maintaining Second Brain health, organizing notes, fixing broken links, eliminating orphan notes, or generating structured topic hubs.

## Procedure
1. **Link Audit:**
   - Scan all `.md` files in the active brain for `[[wikilink]]` references.
   - Detect broken links pointing to nonexistent notes.
   - Suggest either creating scaffold stub notes or fixing spelling typos in links.
2. **Orphan Note Discovery:**
   - Find notes that have 0 incoming and 0 outgoing links.
   - Categorize orphan notes by semantic similarity using `jarvis_search` dense embeddings.
   - Propose natural parent notes or relevant MOC hubs where they should be linked.
3. **Map of Content (MOC) Generation:**
   - When a topic domain (e.g. `wiki/architecture/`, `wiki/ai/`) reaches 5+ related notes, create or update a dedicated Map of Content note (`wiki/<domain>-moc.md`).
   - Group links into logical subsections with one-sentence summaries of each note.
