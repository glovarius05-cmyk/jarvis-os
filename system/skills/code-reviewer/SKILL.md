---
name: Code Reviewer
description: "Deep code review: analyze architecture, edge cases, security, test coverage, and suggest clean diffs."
group: Engineering
---

# Code Reviewer

## When to use
Activate when reviewing pull requests, inspecting code snippets, evaluating refactorings, or hunting for subtle bugs and security vulnerabilities.

## Procedure
1. Inspect the code structure, language idioms, and dependencies.
2. Evaluate across 5 key dimensions:
   - **Correctness & Edge Cases:** Off-by-one errors, null/None checks, concurrent race conditions, resource leaks.
   - **Security:** Injections (SQL, command), SSRF, path traversal, sensitive token leaks.
   - **Architecture & Modularity:** Adherence to SOLID principles, separation of concerns, no premature complexity.
   - **Performance:** Algorithmic complexity (O(n)), database N+1 queries, unneeded allocations.
   - **Testability:** Are tests provided? Are edge cases covered?
3. Present feedback constructively:
   - Summary of changes
   - Critical blockers (must fix)
   - Suggestions & improvements (nice to have)
   - Proposed replacement diff or snippet
