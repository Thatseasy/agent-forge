---
id: session-err-1
mode: session
target: new_hook
applied: false
---

# Prevent recurring tool failure (2x)

**Target:** `new_hook`

## Rationale
Observed `ruff E501 line too long` 2 time(s) in session telemetry.

## Proposed Incremental Change
Add targeted pre-check or lint rule for: `ruff E501 line too long`.
