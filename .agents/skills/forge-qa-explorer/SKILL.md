---
name: forge-qa-explorer
description: Run exploratory hybrid browser QA with Playwright VP9 screencasts, console/network telemetry, mobile overflow checks, and optional video audit before generating regression .spec.ts files. Use when testing web UIs or diagnosing browser bugs. Do not use for backend-only Python unit tests.
---

# Forge QA Explorer

Führe immer zuerst einen explorativen Live-Walkthrough aus, bevor statische `*.spec.ts`-Skripte entstehen.

## Schritte

1. Wähle eine Charter aus `library/nudges/qa_charters/` (`impatient-user`, `adversarial-empty`, `mobile-layout`).
2. Starte explorative Session via CLI:
   `uv run python forge.py qa --url http://localhost:3000 --charter mobile-layout`
3. Für interaktive Live-Inspektion nutze `/browser` und `chrome-devtools-mcp --slim --no-usage-statistics --no-performance-crux`.
4. Trenne bei Fehlern strikt zwischen Locator-Healing im Test und Produkt-Bug-Fix im App-Code.
