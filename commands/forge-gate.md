---
description: Run Stage 2 deterministic 0-USD Security, Spec, UTF-8/LF Normalization, and Dedup checks on skills.
argument-hint: "[<skill-path>] [--all] [--strict] [--no-fix]"
---

# /forge-gate

Validate candidate or bundled skills deterministically at zero LLM cost for security findings, YAML spec compliance, UTF-8 no-BOM + LF, and library deduplication.

## Steps

1. Run Stage 2 Gate via Slim-MCP:
   - Call `forge_workspace(action="gate", target_path="<skill-or-repo-path>", options_json='{"all": true, "strict": true, "no_fix": false}')`.
2. **CLI fallback**:
   - `agent-forge gate <skill-path> --strict` or `agent-forge gate --all --strict`.
3. If any skill reports `failed_security` or `failed_spec`, fix the root cause (secrets, BOM/CRLF, oversized frontmatter, missing triggers) and re-run `--strict`.

## Output Contract

- Return compact JSON: `{"status": "ok"|"error", "summary_one_line": "...", "metrics": {"evaluated": 0, "passed": 0, "failed": 0, "max_similarity": 0.0}, "artifact_paths": ["<AGENT_FORGE_HOME>/runs/<run_id>/gate_report.json"]}`.
- Store detailed finding reports under `AGENT_FORGE_HOME/runs/<run_id>/`.
