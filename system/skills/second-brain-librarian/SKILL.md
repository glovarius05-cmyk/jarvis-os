---
name: Second Brain Librarian
description: "Organize Second Brain notes, maintain Maps of Content (MOC), fix broken wikilinks, and cluster related topics."
group: Knowledge
---

# Second Brain Librarian

## When to use
Activate when the user wants to organize, index, review, or clean up their Second Brain notes, fix broken references, or generate high-level topic overviews.

## Procedure
1. Scan the vault structure using `vault_list` and `vault_search`.
2. Check for:
   - Orphaned notes (notes with no incoming or outgoing wikilinks `[[...]]`)
   - Broken wikilinks pointing to non-existent notes
   - Missing tags or inconsistent metadata in YAML frontmatter
3. Maintain or generate Maps of Content (MOC):
   - Group related notes under parent index files (e.g. `MOC-Business.md`, `MOC-Engineering.md`, `MOC-Life.md`)
   - Summarize each linked note with a 1-sentence descriptor
4. Propose clean reorganization or updates without overwriting user notes.
