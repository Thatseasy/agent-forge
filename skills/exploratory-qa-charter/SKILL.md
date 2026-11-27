---
name: exploratory-qa-charter
description: Execute live-first exploratory Playwright QA with VP9 screencasts, console/network telemetry, and bug-vs-locator triage before writing *.spec.ts files. Use when testing web UIs or running /forge-qa. Do not use for backend-only unit tests or static skill gating.
---

# Exploratory QA Charter

1. **Live-first rule:** Never author static `*.spec.ts` files before completing an exploratory walkthrough against localhost or staging.
2. **Pick charter & scope:** Choose `impatient-user`, `adversarial-empty`, `mobile-layout`, or `all`; restrict domains to `localhost`/`127.0.0.1` or explicitly allowed staging hosts.
3. **Run & record:** Invoke `forge_eval(action="qa")` (`agent-forge qa --url <url> --charter <charter>`) via `@qa-explorer` to capture VP9 video, DOM/layout overflow, and console/network errors.
4. **Triage strictly:** Classify each finding as `locator-healing` (test selector fix) or `product-bug` (app code fix) and store clips/specs in `AGENT_FORGE_HOME/runs/<run_id>/`.
