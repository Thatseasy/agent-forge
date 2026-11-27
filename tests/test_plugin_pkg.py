"""Tests for Ticket #21 (`agent-forge plugin validate/build` and wheel packaging)."""

from __future__ import annotations

import shutil
from pathlib import Path

from typer.testing import CliRunner

from forge.cli import app
from forge.plugin_pkg import build_plugin, validate_plugin

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_plugin_validate_strict_passes_repo_root() -> None:
    report = validate_plugin(REPO_ROOT)
    assert report.valid is True, f"Errors: {report.errors}"
    assert report.name == "agent-forge"
    assert report.command_count == 8
    assert 1 <= report.skill_count <= 3
    assert report.agent_count >= 3
    assert report.rules_line_count < 60
    assert report.static_context_tokens < 1500
    assert report.mcp_schema_tokens <= 1200
    assert report.hooks_valid is True


def test_plugin_validate_rejects_bom_and_absolute_paths(tmp_path: Path) -> None:
    bad_root = tmp_path / "bad_plugin"
    for item in (
        "plugin.json",
        "hooks.json",
        "mcp_config.json",
        "assets",
        "rules",
        "commands",
        "skills",
        "agents",
    ):
        src = REPO_ROOT / item
        dst = bad_root / item
        if src.is_dir():
            shutil.copytree(src, dst)
        elif src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    (bad_root / "src" / "forge" / "hooks_global").mkdir(parents=True)
    for pyf in (REPO_ROOT / "src" / "forge" / "hooks_global").glob("*.py"):
        shutil.copy2(pyf, bad_root / "src" / "forge" / "hooks_global" / pyf.name)

    # Inject UTF-8 BOM + absolute path into plugin.json
    manifest_text = (bad_root / "plugin.json").read_text(encoding="utf-8")
    manifest_text = manifest_text.replace('"./commands"', '"C:/absolute/commands"')
    (bad_root / "plugin.json").write_bytes(b"\xef\xbb\xbf" + manifest_text.encode("utf-8"))

    rep = validate_plugin(bad_root)
    assert rep.valid is False
    assert any("BOM" in e for e in rep.errors)
    assert any("relative path" in e for e in rep.errors)


def test_plugin_build_wheel_contains_bundled_assets(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    build_rep = build_plugin(REPO_ROOT, out_dir=dist_dir)
    assert build_rep.validation.valid is True
    assert Path(build_rep.wheel_path).is_file()
    assert build_rep.wheel_bundled_assets_count >= 25


def test_cli_plugin_validate_strict() -> None:
    runner = CliRunner()
    res = runner.invoke(app, ["plugin", "validate", "--strict"])
    assert res.exit_code == 0, res.output
    assert "valid=True" in res.output
