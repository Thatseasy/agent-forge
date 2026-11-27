"""Tests for three-layer path resolution and CWD-decoupled execution."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from forge.benchmark.cost_oracle import CostOracle
from forge.cli import app
from forge.config import ForgeConfig
from forge.discovery import DiscoveryCollector
from forge.paths import (
    ensure_agent_forge_home,
    find_forge_checkout,
    resolve_agent_forge_home,
    resolve_package_assets_dir,
    resolve_paths,
    resolve_platformdirs_home,
    resolve_target_workspace,
)
from forge.setup import SetupOrchestrator
from forge.sourcer import SourceCatalog


def test_package_assets_dir_contains_bundled_defaults() -> None:
    assets = resolve_package_assets_dir()
    assert assets.is_dir()
    assert (assets / "__init__.py").is_file()
    assert (assets / "clis" / "cost_oracle_snapshot.json").is_file()
    assert (assets / "clis" / "sourcer_catalog.yaml").is_file()
    assert (assets / "clis" / "mcp_templates.json").is_file()
    assert (assets / "profiles" / "core-minimal.yaml").is_file()
    assert (assets / "nudges" / "skill-rules.json").is_file()
    assert (assets / "nudges" / "discovery_sources.yaml").is_file()
    assert (assets / "hooks.json").is_file()
    assert (assets / "hooks" / "_hook_lib.py").is_file()


def test_agent_forge_home_override_and_platformdirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom_home = tmp_path / "custom-home"
    monkeypatch.setenv("AGENT_FORGE_HOME", str(custom_home))
    assert resolve_agent_forge_home() == custom_home.resolve()

    monkeypatch.delenv("AGENT_FORGE_HOME", raising=False)
    default_home = resolve_agent_forge_home()
    assert default_home.name == ".agent-forge" or default_home == resolve_platformdirs_home()

    initialized = ensure_agent_forge_home(custom_home)
    for rel in ("state", "staging/inbox", "units", "runs", "logs", "library/skills"):
        assert (initialized / rel).is_dir()


def test_external_cwd_uses_agent_forge_home_and_bundled_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home = tmp_path / "forge_home"
    ext_workspace = tmp_path / "external_user_project"
    ext_workspace.mkdir(parents=True)
    (ext_workspace / "pyproject.toml").write_text(
        '[project]\nname = "demo-user-app"\n', encoding="utf-8", newline="\n"
    )

    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.setenv("AGENT_FORGE_HOME", str(isolated_home))
    monkeypatch.chdir(ext_workspace)

    assert find_forge_checkout(ext_workspace) is None
    paths = resolve_paths()
    assert paths.is_checkout_mode is False
    assert paths.state_root == isolated_home.resolve()
    assert paths.workspace_dir == ext_workspace.resolve()
    assert resolve_target_workspace(None) == ext_workspace.resolve()

    # Verify secrets.env under AGENT_FORGE_HOME loads automatically without repo .env
    secrets_file = isolated_home / "secrets.env"
    secrets_file.parent.mkdir(parents=True, exist_ok=True)
    secrets_file.write_text('FORGE_JUDGE_MODEL="test-home-judge"\n', encoding="utf-8", newline="\n")
    monkeypatch.delenv("FORGE_JUDGE_MODEL", raising=False)

    cfg = ForgeConfig.load()
    assert cfg.root == isolated_home.resolve()
    assert cfg.judge_model == "test-home-judge"

    # Bundled assets work with zero repo library checkout
    oracle = CostOracle(cfg)
    assert oracle.lookup("gpt-5-mini").input_per_m > 0
    catalog = SourceCatalog(cfg)
    assert len(catalog.load_candidates()) > 0
    staged = DiscoveryCollector(cfg).collect(dry_run=True)
    assert len(staged) == 1
    assert Path(staged[0].staging_path).is_relative_to(isolated_home.resolve())

    report = SetupOrchestrator(cfg).run(ext_workspace, dry_run=False)
    assert report.hooks_schema_valid is True
    assert (ext_workspace / "AGENTS.md").is_file()
    assert (ext_workspace / ".agents" / "hooks.json").is_file()


def test_cli_runs_from_external_temp_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home = tmp_path / "cli_home"
    ext_dir = tmp_path / "blank_repo"
    ext_dir.mkdir(parents=True)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.setenv("AGENT_FORGE_HOME", str(isolated_home))
    monkeypatch.chdir(ext_dir)

    runner = CliRunner()
    res_help = runner.invoke(app, ["--help"])
    assert res_help.exit_code == 0, res_help.output

    res_setup = runner.invoke(app, ["setup", "--dry-run"])
    assert res_setup.exit_code == 0, res_setup.output
    assert "Forge Setup: blank_repo" in res_setup.output

    res_audit = runner.invoke(app, ["archive-audit", "--no-extract"])
    assert res_audit.exit_code == 0, res_audit.output
    assert (isolated_home / "staging" / "archive_audit" / "ARCHIVE_AUDIT.json").is_file()
