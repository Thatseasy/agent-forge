# Agent Forge — Plugin Routing Map

Token-lean skill engineering, setup, gating, benchmarking, and QA pipeline.

## 3-Layer Paths

1. `forge.assets` — read-only bundled profiles, skills, charters, and catalogs (`importlib.resources`).
2. `AGENT_FORGE_HOME` — writable user state (`~/.agent-forge`: `state/`, `runs/`, `units/`, `staging/`, `logs/`, `secrets.env`).
3. Target CWD — active workspace (`.agents/`, `AGENTS.md`, `docs/decisions/`, `tasks/`).

## Secrets & Context Firewall

- **Zero-repo-secrets:** Never commit API keys or `.env` into target repos. Store keys solely in `~/.agent-forge/secrets.env`.
- **Swallow-on-pass:** Keep verbose reports under `AGENT_FORGE_HOME/runs/` or `staging/`; pass compact JSON summaries in context.
- **Isolation:** Route web research to `@research-scout`, read-only reviews to `@adversarial-reviewer`, and live browser QA to `@qa-explorer`.

## Command -> Slim-MCP & CLI Routing

| Slash Command | Slim-MCP Tool | CLI Fallback (`agent-forge`) |
| --- | --- | --- |
| `/forge-init` | `forge_doctor` | `agent-forge doctor --hooks` |
| `/forge-setup` | `forge_workspace(action="setup")` | `agent-forge setup --target .` |
| `/forge-audit` | `forge_workspace(action="audit"\|"source"\|"sanitize")` | `agent-forge archive-audit` / `source` / `sanitize` |
| `/forge-gate` | `forge_workspace(action="gate")` | `agent-forge gate --all --strict` |
| `/forge-bench` | `forge_eval(action="bench")` / `forge_research(mode="cost_oracle")` | `agent-forge bench` / `cost-oracle` |
| `/forge-unit` | `forge_eval(action="unit")` | `agent-forge unit list\|install\|sync` / `tailor` |
| `/forge-qa` | `forge_eval(action="qa")` | `agent-forge qa --url <url>` |
| `/forge-retro` | `forge_evolve(action="retro"\|"nudge"\|"prune"\|"lock")` | `agent-forge retro` / `prune` / `lock --verify` |
