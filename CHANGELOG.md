# Changelog

All notable changes to `agent-forge` are documented in this file.

## [1.0.0] - 2026-11-27

### Added
- **3-Layer Path Architecture (`src/forge/paths.py`):** Decoupled CWD from `AGENT_FORGE_HOME` (`~/.agent-forge` / `platformdirs`), bundled `forge.assets` (`importlib.resources`), and explicit `--target` workspace paths.
- **Dual-Repo Specialist Unit Support:** Added `agent-forge unit install <git-url|local-path>` with Stage-2 strict gating and SHA-256 pinning (`units-lock.json`), plus generic `example-python-cli` reference unit.
- **Lean Slim-MCP Server (`agent-forge-mcp`):** 5 coarse-grained tools (`forge_doctor`, `forge_workspace`, `forge_research`, `forge_eval`, `forge_evolve`) under `1,200` schema tokens (`505` compact tokens) with file-pointer Context Firewall.
- **Native Antigravity Plugin Packaging:** Added `plugin.json`, `assets/logo.svg`, compact `rules/AGENTS.md`, 8 `/forge-*` commands, 3 core skills, and 3 isolated subagent blueprints.
- **Scoped Global Safety Hooks & Doctor:** Stdlib-only plugin root `hooks.json` (Lethal Trifecta + Secret/PII Write Guard supporting both `read_file|view_file` and `write_file|write_to_file`), opt-in repo workflow hooks, and `agent-forge doctor --hooks`.
- **Zero-Friction Onboarding & CI:** `agent-forge init`, `/forge-init`, `~/.agent-forge/secrets.env` (`0600`), `agent-forge plugin validate|build`, and 3-OS cold-start `uvx` matrix workflow.
- **MIT Open-Source Release Docs:** Added `LICENSE`, end-user `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, and `.github/workflows/release.yml`.
