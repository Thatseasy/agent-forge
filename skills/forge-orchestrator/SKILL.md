---
name: forge-orchestrator
description: Route agent-forge tasks across Slim-MCP tools, slash commands, and isolated subagents while enforcing Context Firewall and Stage 2 gates. Use when coordinating multi-stage skill engineering, gating, benchmarking, or evolution workflows. Do not use for single-file code edits unrelated to agent-forge.
---

# Forge Orchestrator

1. **Diagnose first:** Run `forge_doctor` (`/forge-init`) to confirm 3-layer paths (`forge.assets`, `AGENT_FORGE_HOME`, target CWD) and `~/.agent-forge/secrets.env`.
2. **Enforce Context Firewall:** Delegate web/doc lookups to `@research-scout`, read-only reviews to `@adversarial-reviewer`, and live browser QA to `@qa-explorer`.
3. **Sequence stages cleanly:** `audit`/`source` -> `setup` -> `gate` (`0-USD` check) -> `bench` (with `cost_oracle` arbitrage) -> `retro`/`prune`/`lock`.
4. **Swallow-on-pass:** Keep full logs/artifacts in `AGENT_FORGE_HOME/runs/<run_id>/`; return only compact JSON summaries (`status`, `summary_one_line`, `metrics`, `artifact_paths`).
