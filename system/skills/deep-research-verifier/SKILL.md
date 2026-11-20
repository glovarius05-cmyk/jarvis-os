---
name: Deep Research Verifier
description: "Autonomous multi-query web research, cross-source fact checking, and synthesis of verified Second Brain wiki notes."
group: Research
---

# Deep Research Verifier

## When to use
Activate when the user asks for in-depth technical or scientific research, competitive market analysis, or verification of disputed claims across multiple independent web sources.

## Procedure
1. **Deconstruct Topic:** Break the core question into 3-5 distinct, targeted search queries covering different angles (e.g. primary sources, official benchmarks, independent critique).
2. **Multi-Source Crawling:**
   - Use web search to identify high-credibility primary domains.
   - Use `jarvis_browse_url` to inspect the full page contents, stripping noise and sponsored content.
3. **Fact Verification & Consensus Analysis:**
   - Cross-check claims across at least 2 independent sources.
   - Explicitly highlight consensus findings vs disputed opinions.
   - Disclose publication dates, methodology caveats, and potential bias.
4. **Second Brain Note Creation:**
   - Synthesize a comprehensive article in `wiki/<topic-slug>.md`.
   - Include structured sections: Executive Overview, Key Findings, Verified Evidence, Unresolved Questions, and Direct Source Citations.
