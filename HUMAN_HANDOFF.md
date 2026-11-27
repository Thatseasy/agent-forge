# Pre-Release Security Gate & Marketplace Handoff Checklist (`v1.0.0`)

This repository has completed Stage 3 hardening (`#15`–`#22`) on private `main` with full 3-OS cold-start CI verification. Complete the manual human-gate steps below before switching repository visibility to **Public**:

## 1. Pre-Release Security & Privacy Gate (5 Mandatory Checks)

- [x] **Clean Git History Export (`public-release` orphan branch):** Verify the single-commit clean export branch (`public-release`) has `1` commit and `0` local drive paths, `0` private domain corpora, and `0` real API keys or secrets.
- [x] **Zero-Default Exfiltration:** `AGENT_FORGE_HOME/config.toml` defaults to `[telemetry] enabled = false` and `[cloud_sync] enabled = false`; `.github/workflows/cloud-sync.yml` runs strictly on `workflow_dispatch`.
- [x] **Supply-Chain & Code Safety:** Dependencies in `pyproject.toml` are version-bounded with pinned `uv.lock`; `ruff`, `mypy`, `bandit -r src -ll`, `skills-lock.json`, and `agent-forge plugin validate --strict` pass with `0` errors.
- [x] **Failsafe Stdlib Global Hooks:** `hooks.json` & `src/forge/hooks_global/` import only Python standard library (`timeout <= 5s`), support both `read_file|view_file` and `write_file|write_to_file`, and log failures to `AGENT_FORGE_HOME/logs/hooks.log`.
- [x] **License & Asset Compliance:** Root `LICENSE` (`MIT`) and `plugin.json` match; only generic core assets and `example-python-cli` are bundled (private specialist units live separately in `Thatseasy/agent-forge-units`).

## 2. Final Human-Gate Actions (Manual Switch to Public)

1. Verify `public-release` history one final time:
   ```bash
   git log -p public-release
   ```
   *(Optional if `gitleaks` is installed locally: `gitleaks detect --log-opts="public-release"`)*
2. Publish clean history to the public repository branch (either force-push clean history onto `Thatseasy/agent-forge` `main` OR push `public-release:main` to your chosen public repository):
   ```bash
   git push --force origin public-release:main
   ```
3. Switch repository visibility from **Private** to **Public** in GitHub Settings (`Settings -> General -> Change visibility`) or create tag `v1.0.0` to trigger `.github/workflows/release.yml`:
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```
4. Test end-to-end installation from a clean directory:
   ```bash
   agy plugin install https://github.com/Thatseasy/agent-forge
   # or
   uvx --from git+https://github.com/Thatseasy/agent-forge agent-forge init
   ```
5. Connect your private specialist unit repo inside `AGENT_FORGE_HOME`:
   ```bash
   agent-forge unit install https://github.com/Thatseasy/agent-forge-units.git
   ```
