---
name: playwright-triage
description: Diagnose web UI issues with exploratory browser checks before writing Playwright regression specs. Use when investigating frontend bugs or mobile overflows. Do not use for offline Python unit tests.
---

# Playwright Triage

1. Run an exploratory session first (`forge.py qa` or `/browser` + `chrome-devtools-mcp --slim`).
2. Check console errors, HTTP >= 400 responses, and `scrollWidth > innerWidth` at `375x812`.
3. Separate test locator healing from genuine product bug fixes.
