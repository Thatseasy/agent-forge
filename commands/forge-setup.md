---
description: Assess repository maturity and idempotently bootstrap AGENTS.md, ADRs, tasks, sources, and opt-in hooks.
argument-hint: "[--target <path>] [--profile <name>] [--dry-run] [--live-source]"
---

# /forge-setup

Assess the target workspace (`prototype_3h_chat`, `active_product`, or `mature_repo`) and idempotently install lean harness docs, stack sources, and `.agents/` hooks without overwriting custom docs.

## Steps

1. **Dry-run preview first** (zero writes):
   - Call `forge_workspace(action="setup", target_path="<target-or-cwd>", options_json='{"dry_run": true}')`.
   - CLI fallback: `agent-forge setup --target <path> --dry-run`.
2. Review planned creates/skips (`AGENTS.md`, `docs/decisions/ADR-0001.md`, `tasks/todo.md`, `tasks/lessons.md`, `.agents/hooks.json`, `mcp_config.recommended.json`).
3. **Apply idempotently**:
   - Call `forge_workspace(action="setup", target_path="<target-or-cwd>", options_json='{"dry_run": false, "profile": "<optional>", "live_source": false}')`.
   - CLI fallback: `agent-forge setup --target <path>`.

## Output Contract

- Return compact JSON: `{"status": "ok"|"error", "summary_one_line": "...", "metrics": {"maturity": "...", "created_count": 0, "skipped_count": 0, "hooks_schema_valid": true}, "artifact_paths": ["<AGENT_FORGE_HOME>/runs/<run_id>/setup_report.json"]}`.
- Store full setup diff/plan under `AGENT_FORGE_HOME/runs/<run_id>/`.
