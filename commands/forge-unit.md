---
description: Manage, SHA-256 install, sync, or sanitize-and-tailor canonical specialist domain units.
argument-hint: "<list|init|install|sync|tailor> [domain-or-source] [--target <path>] [--dry-run]"
---

# /forge-unit

Manage canonical domain specialist units in `AGENT_FORGE_HOME/units/` and deploy Stage-2-gated unit skills into target workspaces.

## Steps

1. If tailoring from raw transcripts or domain documents, sanitize PII/secrets first:
   - Call `forge_workspace(action="sanitize", target_path="<raw-corpus>", options_json='{"verify": false}')`.
2. Execute the unit action via Slim-MCP:
   - Call `forge_eval(action="unit", target_path="<target-or-cwd>", spec_json='{"subcommand": "<list|install|sync>", "unit": "<domain>", "source": "<optional>", "dry_run": true}')`.
3. **CLI fallback**:
   - `agent-forge unit list` | `agent-forge unit init <domain>` | `agent-forge unit install <source> --dry-run`
   - `agent-forge unit sync <domain> --target <path> --dry-run`
   - `agent-forge tailor --unit <domain> --base-skill <slug> --corpus <sanitized-path> --dry-run`

## Output Contract

- Return compact JSON: `{"status": "ok"|"error", "summary_one_line": "...", "metrics": {"units": [...], "gate_passed": true}, "artifact_paths": ["<AGENT_FORGE_HOME>/units/..."]}`.
- Persist SHA-256 locks and tailored unit outputs under `AGENT_FORGE_HOME/units/` or `runs/<run_id>/`.
