---
name: Document Parser
description: "Parse and convert PDF, CSV, Excel, and JSON documents into structured Markdown for Second Brain."
group: Knowledge
---

# Document Parser

## When to use
Activate when the user uploads, references, or asks to extract structured data and notes from local files (PDF reports, CSV datasets, Excel spreadsheets, JSON exports, or raw text dumps).

## Procedure
1. Identify the input format and path:
   - **CSV / TSV / Excel:** Convert columns and rows into clean Markdown comparison tables. Calculate column totals or key summary statistics where appropriate.
   - **PDF / Word:** Extract the document outline, section headings, and core body paragraphs. Filter out repetitive headers, footers, and page numbers.
   - **JSON / Structured Logs:** Flatten nested keys into readable Markdown lists or key-value summary blocks.
2. Structure the output into Second Brain format:
   - Add YAML frontmatter with `title`, `source_file`, `date`, and `tags`.
   - Include an executive summary of key takeaways.
   - Retain numerical data fidelity.
3. Suggest saving the structured extraction to `sources/<filename>.md` and link relevant entities with `[[wikilinks]]`.
