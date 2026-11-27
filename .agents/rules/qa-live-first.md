---
trigger: glob
globs: "*.spec.ts, *.test.ts, *playwright*"
description: Enforce exploratory live QA walkthroughs before authoring static Playwright test scripts.
---

# QA Live-First

1. Erstelle keine neuen `*.spec.ts`-Dateien vor einem dokumentierten explorativen Live-Walkthrough (`/browser` + `chrome-devtools-mcp --slim` oder `uv run python forge.py qa`).
2. Trenne bei Fehlern strikt zwischen Locator-Healing im Test und echtem Produkt-Bug-Fix.
3. Nutze für `chrome-devtools-mcp` ausschließlich das schlanke Profil `--slim --no-usage-statistics --no-performance-crux`.
