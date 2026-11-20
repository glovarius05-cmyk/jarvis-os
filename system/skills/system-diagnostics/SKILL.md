---
name: System Diagnostics
description: "Diagnose system health, inspect logs, analyze error traces, and suggest fixes for crashed processes."
group: Engineering
---

# System Diagnostics

## When to use
Activate when an error, crash, exception traceback, timeout, or degraded system performance is reported by the user or background task.

## Procedure
1. Parse the error traceback or log output to extract:
   - Root cause exception type and message
   - Originating file, line number, and function
   - Process exit code or HTTP status code
2. Differentiate between environmental issues (missing PATH binary, port already in use, permission denied, DNS network failure) vs code bugs.
3. Formulate a 3-step resolution plan:
   - Immediate mitigation (e.g. restart service, free port, grant permission)
   - Code or config fix (precise patch)
   - Verification command to confirm stability
4. Offer to execute the fix if running in Full mode, or guide the user step-by-step.
