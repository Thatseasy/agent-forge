"""Tests for Ticket #20 zero-friction onboarding wizard and graceful degradation."""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from forge.benchmark import CostOracle
from forge.cli import app
from forge.config import ForgeConfig
from forge.gate import GatePipeline
from forge.init_wizard import run_init, upsert_secrets_env
from forge.setup import SetupOrchestrator


def test_init_cold_start_non_interactive_creates_home_and_graceful_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home_dir = tmp_path / "cold_home"
    ext_repo = tmp_path / "fresh_user_repo"
    ext_repo.mkdir(parents=True)
    (ext_repo / "README.md").write_text("# Demo App\n", encoding="utf-8", newline="\n")

    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.setenv("AGENT_FORGE_HOME", str(home_dir))
    for key in (
        "PERPLEXITY_API_KEY",
        "FIRECRAWL_API_KEY",
        "CONTEXT7_API_KEY",
        "EXA_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(ext_repo)

    runner = CliRunner()
    res = runner.invoke(app, ["init", "--non-interactive", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["status"] == "ok"
    assert Path(payload["home_dir"]) == home_dir.resolve()

    config_toml = home_dir / "config.toml"
    secrets_env = home_dir / "secrets.env"
    cache_db = home_dir / "state" / "research_cache.db"
    assert config_toml.is_file()
    toml_text = config_toml.read_text(encoding="utf-8")
    assert "enabled = false" in toml_text
    assert secrets_env.is_file()
    assert cache_db.is_file()
    assert (home_dir / "library" / "clis" / "cost_oracle_snapshot.json").is_file()
    assert (home_dir / "library" / "units" / "example-python-cli" / "unit.yaml").is_file()

    # Zero secrets or files leaked into external user workspace
    assert not (ext_repo / ".env").exists()
    assert not (ext_repo / "secrets.env").exists()

    # Verify 0-USD offline capabilities work cleanly without API keys
    cfg = ForgeConfig.load()
    assert CostOracle(cfg).lookup("gpt-5-mini").input_per_m > 0
    setup_rep = SetupOrchestrator(cfg).run(ext_repo, dry_run=False)
    assert setup_rep.hooks_schema_valid is True
    gate_rep = GatePipeline(cfg).evaluate_skill(
        home_dir
        / "library"
        / "units"
        / "example-python-cli"
        / "skills"
        / "example-python-cli-conventions",
        apply_fixes=False,
        run_external=False,
    )
    assert gate_rep.status == "passed"


def test_init_secrets_0600_and_graceful_missing_optional_binaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home_dir = tmp_path / "sec_home"
    ext_repo = tmp_path / "repo"
    ext_repo.mkdir(parents=True)

    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.setenv("AGENT_FORGE_HOME", str(home_dir))
    monkeypatch.chdir(ext_repo)

    secrets_path = upsert_secrets_env(
        home_dir / "secrets.env",
        {"PERPLEXITY_API_KEY": "pplx-demo-key-placeholder"},
    )
    assert secrets_path.is_file()
    if os.name != "nt":
        mode = stat.S_IMODE(secrets_path.stat().st_mode)
        assert mode == 0o600

    # Simulate missing optional binaries (`docker`, `playwright`) cleanly -> no crash
    orig_which = shutil.which

    def _fake_which(cmd: str) -> str | None:
        if cmd in {"docker", "playwright"}:
            return None
        return orig_which(cmd)

    monkeypatch.setattr(shutil, "which", _fake_which)
    cfg = ForgeConfig.load()
    rep = run_init(cfg, target_dir=ext_repo, dry_run=False)
    assert rep.status == "ok"
    modes = {f.feature: f.mode for f in rep.features}
    assert "graceful" in modes["Docker Container Benchmark Isolation"]
    assert rep.doctor.api_keys_configured["PERPLEXITY_API_KEY"] is True
