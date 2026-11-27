---
description: Run live-first exploratory Playwright QA with VP9 screencast, telemetry, and bug-vs-locator triage.
argument-hint: "--url <http://localhost:3000> [--charter all|impatient-user|adversarial-empty|mobile-layout] [--dry-run]"
---

# /forge-qa

Execute Stage 6 live exploratory browser QA before writing or healing static `*.spec.ts` regression tests.

## Steps

1. Restrict scope to localhost/staging URLs; delegate interactive runs to `@qa-explorer` to preserve main context.
2. Run exploratory QA via Slim-MCP:
   - Call `forge_eval(action="qa", target_path="<target-or-cwd>", spec_json='{"url": "<url>", "charter": "all", "video_audit": true, "dry_run": false}')`.
3. **CLI fallback**:
   - `agent-forge qa --url <url> --charter <charter> [--dry-run]`.
4. Triage every finding strictly as `locator-healing` (test code fix) vs `product-bug` (app code fix) before authoring `*.spec.ts`.

## Output Contract

- Return compact JSON: `{"status": "ok"|"error", "summary_one_line": "...", "metrics": {"findings_count": 0, "product_bugs": 0, "locator_fixes": 0}, "artifact_paths": ["<AGENT_FORGE_HOME>/runs/<run_id>/qa_report.json"]}`.
- Write VP9 clips, console/network logs, and reproduction specs under `AGENT_FORGE_HOME/runs/<run_id>/` or `qa-artifacts/`.
