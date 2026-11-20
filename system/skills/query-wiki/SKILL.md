---
name: Query Wiki
description: "Search and synthesize answers grounded in the user's Second Brain wiki/ and memory/ notes."
group: Knowledge
---

# Query Wiki

## Procedure
1. Search relevant keywords across the vault with `vault_search`.
2. Read the top matching notes from `wiki/` and `memory/facts/` via `vault_read`.
3. Synthesize a direct answer citing the exact note paths used. Do not invent facts absent from the vault.
