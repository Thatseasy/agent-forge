---
name: superarchivist-setup
description: Assess repository maturity, audit docs read-only, source primary stack docs/MCPs, and bootstrap an idempotent agent harness. Use when onboarding a new or vibe-coded project, running /forge-setup, or invoking archive-audit, source, and setup. Do not use for Stage 3 ablation benchmarks or browser QA.
---

# Superarchivist Setup

1. **Classify maturity read-only:** Inspect manifests (`package.json`, `pyproject.toml`), docs (`AGENTS.md`, `docs/decisions/ADR-*.md`, `tasks/`), and `.agents/` to label `prototype_3h_chat`, `active_product`, or `mature_repo`.
2. **Audit & source first:** Run `forge_workspace(action="audit")` and `forge_workspace(action="source")` (or `agent-forge archive-audit` / `source --dry-run`) to surface stack docs and lean MCP templates.
3. **Preview then apply:** Run `forge_workspace(action="setup", options_json='{"dry_run": true}')` before live apply so existing custom docs are never overwritten.
4. **Verify invariants:** Confirm `AGENTS.md` stays `< 100` lines, secrets stay in `~/.agent-forge/secrets.env`, and all outputs use UTF-8 without BOM + LF.
