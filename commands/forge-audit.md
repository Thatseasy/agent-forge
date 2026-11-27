---
description: Audit repo docs read-only, discover stack sources, sanitize raw corpora, or run zero-token skill radar.
argument-hint: "[--target <path>] [--mode audit|source|sanitize|discover] [--dry-run]"
---

# /forge-audit

Run read-only project auditing, stack doc/MCP sourcing, local PII/secret sanitization, or zero-token skill discovery.

## Steps

1. Select the matching Slim-MCP operation based on `--mode` (default `audit`):
   - `audit`: `forge_workspace(action="audit", target_path="<path>", options_json='{"extract": false}')`.
   - `source`: `forge_workspace(action="source", target_path="<path>", options_json='{"dry_run": true}')`.
   - `sanitize`: `forge_workspace(action="sanitize", target_path="<corpus-path>", options_json='{"verify": false, "dry_run": true}')`.
   - `discover`: `forge_research(mode="discover", query="dry_run=true")`.
2. **CLI fallback**:
   - `agent-forge archive-audit --project <path> --no-extract`
   - `agent-forge source <path> --dry-run`
   - `agent-forge sanitize --input <path> --dry-run` (or `--verify`)
   - `agent-forge discover --dry-run`

## Output Contract

- Return compact JSON: `{"status": "ok"|"error", "summary_one_line": "...", "metrics": {...}, "artifact_paths": ["<AGENT_FORGE_HOME>/staging/..."]}`.
- Write extracted candidates, source recommendations, or sanitized outputs strictly to `AGENT_FORGE_HOME/runs/<run_id>/` or `staging/`.
