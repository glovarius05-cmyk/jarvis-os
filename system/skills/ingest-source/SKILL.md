---
name: Ingest Source
description: "Distill a raw note or document from sources/ into structured wiki/ articles linked with [[wikilinks]]."
group: Knowledge
---

# Ingest Source

## Procedure
1. Read the source material from `sources/` using `vault_read`.
2. Extract key durable concepts, entities, and frameworks (skip ephemeral chatter).
3. Search `wiki/` via `vault_search` to avoid duplicating existing articles.
4. Create or update concise `wiki/<slug>.md` notes with `[[wikilinks]]` connecting related concepts and citing the source file.
5. Summarize what was distilled and which wiki notes were created or updated.
