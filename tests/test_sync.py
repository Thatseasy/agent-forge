"""Acceptance tests for Stage 5 lockfile, selective workspace sync, and cloud exporters."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

from forge.config import ForgeConfig
from forge.sync import CloudExporter, LockfileManager, WorkspaceSyncer, validate_hooks_schema


def test_lockfile_update_verify_and_tamper_detection(tmp_path: Path) -> None:
    cfg = ForgeConfig(root=tmp_path)
    sdir = tmp_path / "library" / "skills" / "sample-skill"
    sdir.mkdir(parents=True)
    (sdir / "SKILL.md").write_text(
        "---\nname: sample-skill\ndescription: Test.\n---\n\nOriginal\n",
        encoding="utf-8",
    )

    mgr = LockfileManager(cfg)
    lf = mgr.update()
    assert "library/skills/sample-skill" in lf.skills
    assert lf.skills["library/skills/sample-skill"].version == "0.1.0"
    assert mgr.verify().valid is True

    # Tamper skill without updating lockfile -> verify fails
    (sdir / "SKILL.md").write_text("Tampered\n", encoding="utf-8")
    res = mgr.verify()
    assert res.valid is False
    assert "library/skills/sample-skill" in res.mismatches

    # Re-update bumps SemVer patch cleanly
    lf2 = mgr.update()
    assert lf2.skills["library/skills/sample-skill"].version == "0.1.1"
    assert mgr.verify().valid is True


def test_workspace_sync_dry_run_and_live_validation(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    cfg = ForgeConfig(root=repo_root)
    target = tmp_path / "target-project"

    # Dry-run writes nothing
    dry_rep = WorkspaceSyncer(cfg).sync("core-minimal", target, dry_run=True)
    assert dry_rep.dry_run is True
    assert not target.exists()

    # Live sync copies only profile skills + hooks + filtered rules & validates schema
    live_rep = WorkspaceSyncer(cfg).sync("core-minimal", target, dry_run=False)
    assert live_rep.hooks_schema_valid is True
    assert validate_hooks_schema(target / ".agents" / "hooks.json") is True
    assert (target / ".agents" / "skills" / "python-cli-clean" / "SKILL.md").exists()
    assert not (target / ".agents" / "skills" / "demo-hype").exists()
    rules = json.loads((target / "library" / "nudges" / "skill-rules.json").read_text("utf-8"))
    assert [r["id"] for r in rules["rules"]] == ["forge-research"]

    # Modify a synced file locally -> dry-run reports diff before overwrite
    (target / ".agents" / "skills" / "python-cli-clean" / "SKILL.md").write_text(
        "Local override\n",
        encoding="utf-8",
    )
    diff_rep = WorkspaceSyncer(cfg).sync("core-minimal", target, dry_run=True)
    assert len(diff_rep.diffs) >= 1


def test_cloud_exporters_obsidian_notion_and_drive(tmp_path: Path) -> None:
    cfg = ForgeConfig(root=tmp_path, notion_api_key="nkey", notion_database_id="ndb")
    sdir = tmp_path / "library" / "skills" / "demo-skill"
    sdir.mkdir(parents=True)
    (sdir / "SKILL.md").write_text("---\nname: demo-skill\ndescription: Demo.\n---\n", "utf-8")
    LockfileManager(cfg).update()

    posted: list[dict[str, object]] = []

    def _notion_handler(req: httpx.Request) -> httpx.Response:
        posted.append(json.loads(req.content.decode("utf-8")))
        return httpx.Response(200, json={"id": "page-1"})

    exporter = CloudExporter(
        cfg, http_client=httpx.Client(transport=httpx.MockTransport(_notion_handler))
    )
    obs = exporter.export_obsidian(dry_run=True)
    assert any(p.endswith("Leaderboard.md") for p in obs.exported_files)
    assert (tmp_path / "staging" / "obsidian_export" / "Leaderboard.md").exists()

    notion_res = exporter.export_notion(dry_run=False)
    assert "Pushed 1 Notion" in notion_res.notes
    assert len(posted) == 1

    (tmp_path / "archive").mkdir()
    drive_res = exporter.export_drive(dry_run=True)
    assert any("--dry-run" in c for c in drive_res.commands_run)


def test_cli_lock_sync_and_cloud_sync_acceptance(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, str(repo_root / "forge.py"), "lock", "--update"],
        cwd=repo_root,
        check=True,
    )
    subprocess.run(
        [sys.executable, str(repo_root / "forge.py"), "lock", "--verify"],
        cwd=repo_root,
        check=True,
    )

    ws_target = tmp_path / "test-workspace"
    subprocess.run(
        [
            sys.executable,
            str(repo_root / "forge.py"),
            "sync",
            "--profile",
            "core-minimal",
            "--target",
            str(ws_target),
        ],
        cwd=repo_root,
        check=True,
    )
    assert validate_hooks_schema(ws_target / ".agents" / "hooks.json")
    shutil.rmtree(ws_target, ignore_errors=True)

    obs_proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "forge.py"),
            "cloud-sync",
            "--target",
            "obsidian",
            "--dry-run",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert "Exported" in obs_proc.stdout
