# agent-forge

**Token-lean Antigravity plugin & CLI for discovering, security-gating, benchmarking, refining, QA-testing, and syncing AI agent skills across any repository.**

`agent-forge` ships with zero-clone `uvx` execution, a strict 5-tool Slim-MCP server (`<= 1,200` schema tokens), stdlib-only global safety hooks, and a 3-layer path model that keeps your personal secrets and domain units outside project repositories.

---

## 60-Second Quickstart

### 1. Install the Antigravity Plugin

Install directly from GitHub URL (or Antigravity Marketplace):

```bash
agy plugin install https://github.com/Thatseasy/agent-forge
```

Or run the CLI / Slim-MCP server anywhere with zero manual clone via `uv`:

```bash
uvx --from git+https://github.com/Thatseasy/agent-forge agent-forge init
```

### 2. Initialize Your Environment (`/forge-init`)

Inside any project directory:

```bash
agent-forge doctor --hooks
agent-forge init
```

Or run `/forge-init` in Antigravity chat. This initializes `~/.agent-forge` (`AGENT_FORGE_HOME`) with default catalogs, SQLite research cache, `config.toml` (`telemetry.enabled = false`, `cloud_sync.enabled = false`), and `~/.agent-forge/secrets.env` (`chmod 0600`).

### 3. Bootstrap Any Workspace (`/forge-setup`)

```bash
agent-forge setup --target .
```

Or run `/forge-setup`. It assesses repository maturity, detects the tech stack, generates `AGENTS.md`, `ADR-0001`, `tasks/todo.md`, `tasks/lessons.md`, recommended MCP configs, and installs opt-in workspace workflow hooks cleanly.

---

## Architecture: 3 Physical Layers & Dual-Repo Isolation

| Layer | Location | Access | Contents |
| :--- | :--- | :--- | :--- |
| **1. Package Assets** | `forge.assets` (`importlib.resources`) | Read-Only | Core profiles, `cost_oracle_snapshot.json`, `sourcer_catalog.yaml`, generic `example-python-cli` unit, hook templates |
| **2. User State** | `AGENT_FORGE_HOME` (`~/.agent-forge/`) | Read/Write | `config.toml`, `secrets.env` (`0600`), `research_cache.db`, `units/`, `staging/`, `runs/`, `logs/` |
| **3. Target Workspace** | `--target <path>` (default `Path.cwd()`) | Project-Scoped | `AGENTS.md`, `.agents/skills/`, `.agents/hooks.json`, `docs/decisions/`, `tasks/` |

Private company playbooks, PII-sanitized corpora, and custom specialist units stay in your own private repository (e.g., `Thatseasy/agent-forge-units`) and install into `AGENT_FORGE_HOME` with strict Stage-2 gating and SHA-256 pinning:

```bash
agent-forge unit install git+ssh://git@github.com/your-org/private-units.git
agent-forge unit sync my-domain-unit --target /path/to/repo
```

---

## Feature & 0-USD Offline Readiness Matrix

| Feature | Tier | Requirements | Fallback Without Extras |
| :--- | :--- | :--- | :--- |
| `/forge-init`, `/forge-setup`, `/forge-audit`, `/forge-gate`, `/forge-unit`, `sanitize`, `lock`, `prune` | **0-USD Core** | None (`uv` + Python >= 3.11) | Runs 100% locally offline |
| `/forge-research` (`sonar`, `firecrawl`, `context7`, `exa`) | Optional API | Keys in `~/.agent-forge/secrets.env` | `--dry-run` / cached SQLite / bundled catalogs |
| `/forge-bench` live 3-arm LLM ablation & Flash-Judge | Optional API | `OPENAI_API_KEY` / `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` | `--dry-run` deterministic simulation & `cost-oracle` |
| `/forge-qa` live browser exploration & VP9 video audit | Optional Binary | `uvx playwright install chromium` | `--dry-run` |
| Container sandbox benchmark isolation | Optional Binary | Docker daemon | `--isolation worktree` |

---

## Slash Commands & Slim-MCP Tools

To prevent prompt bloat, `agent-forge-mcp` exposes only **5 coarse-grained tools** (`505` compact schema tokens total) writing heavy artifacts under `AGENT_FORGE_HOME/runs/<run_id>/`:

| Slash Command | Slim-MCP Tool | CLI Equivalent | Purpose |
| :--- | :--- | :--- | :--- |
| `/forge-init` | `forge_doctor` | `agent-forge init`, `agent-forge doctor --hooks` | Diagnostics, `AGENT_FORGE_HOME` setup & hook parity |
| `/forge-setup` | `forge_workspace(action="setup")` | `agent-forge setup --target .` | Stack detection, `AGENTS.md`, ADRs & opt-in hooks |
| `/forge-audit` | `forge_workspace(action="audit")` | `agent-forge archive-audit` | Read-only repository documentation & harness audit |
| `/forge-gate` | `forge_workspace(action="gate")` | `agent-forge gate --strict` | Deterministic 0-USD Security, Spec & TF-IDF dedup gate |
| `/forge-bench` | `forge_eval(action="bench")` | `agent-forge bench --suite ...` | 3-arm skill A/B ablation, `ValueScore` & Flash-Judge |
| `/forge-unit` | `forge_eval(action="unit")` | `agent-forge unit init\|install\|sync` | Install, sanitize, tailor & deploy domain units |
| `/forge-qa` | `forge_eval(action="qa")` | `agent-forge qa --url ...` | Exploratory Playwright QA + VP9 video & bug specs |
| `/forge-retro` | `forge_evolve(action="retro")` | `agent-forge retro`, `prune`, `lock` | Capped evolutionary nudges, pruning & SHA-256 locks |

---

## Security Model

1. **Global Stdlib-Only Plugin Hooks (`hooks.json`):** Runs solely on Python's standard library (`timeout <= 5s`, failsafe exit `0` logged to `~/.agent-forge/logs/hooks.log`) — zero third-party imports so foreign repositories are never blocked.
   - **Lethal Trifecta Blocker:** Denies outbound shell network calls (`curl`, `wget`, `Invoke-WebRequest`, `ssh`, inline `requests`/`httpx`) in the same `conversationId` after reading `.env`, `secrets.env`, or private key files.
   - **Secret & PII Write Guard:** Denies hardcoded API keys/private keys or `.env` writes in tracked project files; prompts (`ask`) on suspicious credentials.
2. **Opt-In Repo Hooks (`.agents/hooks.json`):** Deployed only when running `/forge-setup` (Two-Try Skill Guard, Live-First QA Gate, Swarm `owned_paths`, Stop Doc-Drift nudge).
3. **Zero-Default Exfiltration:** `telemetry` and `cloud_sync` default to `enabled = false` in `config.toml`.
4. **Repository Hygiene:** Keep keys exclusively in `~/.agent-forge/secrets.env` (`0600`), never inside any project repository.

---

## Development & Verification

```bash
uv sync
uv run ruff check src tests .agents/hooks forge.py scripts
uv run mypy src
uv run bandit -r src -ll
uv run agent-forge plugin validate --strict
uv run pytest -q
```

Licensed under the [MIT License](LICENSE).
