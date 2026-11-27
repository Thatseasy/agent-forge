---
description: Run zero-friction onboarding, initialize AGENT_FORGE_HOME, check hooks, and display feature readiness.
argument-hint: "[--non-interactive] [--with-setup] [--target <path>]"
---

# /forge-init

Initialize `~/.agent-forge` (`AGENT_FORGE_HOME`), validate plugin hooks, verify 0-USD offline vs optional API readiness, and confirm zero secrets in the repository.

## Steps

1. Run Slim-MCP diagnostics on the target workspace:
   - Call `forge_doctor(target_path="<target-or-cwd>")`.
2. Initialize `AGENT_FORGE_HOME` safely (or run CLI onboarding):
   - Run `agent-forge init --non-interactive --target "<target-or-cwd>"` (or omit `--non-interactive` for interactive secret prompts stored strictly in `~/.agent-forge/secrets.env` at `0600`).
3. Verify secrets & graceful degradation:
   - Show the 0-USD offline matrix (`setup`, `archive-audit`, `gate`, `sanitize`, `lock`, `prune`, `unit`) vs optional extras (`PERPLEXITY_API_KEY`, `FIRECRAWL_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `playwright`, `docker`).
   - Report only boolean presence flags, never raw secret values.

## Output Contract

- Return compact JSON: `{"status": "ok"|"error", "summary_one_line": "...", "metrics": {"tools": {...}, "api_keys_present": {...}, "hooks_valid": true}, "artifact_paths": ["<AGENT_FORGE_HOME>/runs/<run_id>/doctor.json"]}`.
- Suggest the next `/` commands (`/forge-setup`, `/forge-audit`, `/forge-gate`, `/forge-unit`).
