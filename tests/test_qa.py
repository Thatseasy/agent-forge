"""Acceptance tests for Stage 6 exploratory QA, buggy_page.html fixture & video audit."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from forge.config import ForgeConfig
from forge.qa import QAExplorer, audit_qa_video


def test_qa_blocks_unlisted_external_domain(tmp_path: Path) -> None:
    explorer = QAExplorer(ForgeConfig(root=tmp_path))
    with pytest.raises(ValueError, match="Blocked domain"):
        explorer.explore("https://evil.example.com", dry_run=True)


def test_qa_live_playwright_detects_console_and_overflow_on_buggy_fixture(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    fixture_url = (repo_root / "tests" / "fixtures" / "buggy_page.html").resolve().as_uri()
    out_dir = tmp_path / "qa-out"

    cfg = ForgeConfig(root=repo_root)
    explorer = QAExplorer(cfg)
    report = explorer.explore(url=fixture_url, charter="all", out_dir=out_dir, dry_run=False)

    categories = {f.category for f in report.findings}
    assert "console_error" in categories
    assert "layout_overflow" in categories
    assert report.overflow_detected is True
    assert report.overflow_metrics["scrollWidth"] > report.overflow_metrics["innerWidth"]
    assert Path(report.action_tape_path).exists()
    assert Path(report.video_clip_path).exists()
    assert Path(report.video_clip_path).stat().st_size > 0
    spec_path = Path(report.reproduction_spec_path)
    assert spec_path.exists()
    assert spec_path.name == "reproduction_001.spec.ts"
    assert "expect(overflow).toBeFalsy()" in spec_path.read_text("utf-8")

    va = audit_qa_video(cfg, Path(report.video_clip_path), dry_run=True)
    assert va.fps == 1
    assert va.content_layers == ["frames"]
    assert va.estimated_cost_usd < 0.01


def test_cli_qa_dry_run_localhost(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    out_dir = tmp_path / "cli-qa"
    proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "forge.py"),
            "qa",
            "--url",
            "http://localhost:0",
            "--out-dir",
            str(out_dir),
            "--dry-run",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert "Stage 6 Exploratory QA" in proc.stdout
    assert (out_dir / "reproduction_001.spec.ts").exists()
    assert (out_dir / "session_vp9.webm").exists()
    assert (out_dir / "qa_report.json").exists()
