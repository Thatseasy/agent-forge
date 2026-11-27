---
description: Run session retrospectives, apply canonical backlog nudges, audit/prune unused skills, or verify lockfiles.
argument-hint: "[--mode session|outcome|audit] [--prune] [--lock-verify] [--dry-run]"
---

# /forge-retro

Close the self-improvement loop by extracting canonical nudges, auditing skill activation bloat, or verifying `skills-lock.json` integrity.

## Steps

1. Choose the evolution action via Slim-MCP:
   - Retro nudges: `forge_evolve(action="retro", target_path="<cwd>", options_json='{"mode": "session", "dry_run": false}')`.
   - Apply nudge: `forge_evolve(action="nudge", target_path="<cwd>", options_json='{"nudge_id": "<id>"}')`.
   - Usage & bloat audit: `forge_evolve(action="prune", target_path="<cwd>", options_json='{"days": 30, "dry_run": true}')`.
   - Lockfile integrity: `forge_evolve(action="lock", target_path="<cwd>", options_json='{"verify": true}')`.
2. **CLI fallback**:
   - `agent-forge retro --mode session` | `agent-forge nudge apply <id>` | `agent-forge prune --days 30 --dry-run` | `agent-forge lock --verify`.

## Output Contract

- Return compact JSON: `{"status": "ok"|"error", "summary_one_line": "...", "metrics": {"nudges_extracted": 0, "flagged_skills": 0, "lock_valid": true}, "artifact_paths": ["<AGENT_FORGE_HOME>/runs/<run_id>/evolve_report.json"]}`.
- Write backlog nudges to `library/nudges/backlog/` and audit logs under `AGENT_FORGE_HOME/runs/<run_id>/`.
