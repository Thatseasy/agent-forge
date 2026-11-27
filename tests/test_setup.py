"""Acceptance and unit tests for `forge.setup` (assessor, orchestrator, fixture, skills)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from forge.config import ForgeConfig, estimate_tokens
from forge.gate import GatePipeline
from forge.setup import ProjectAssessor, SetupOrchestrator
from forge.sync import validate_hooks_schema

REPO_ROOT = Path(__file__).resolve().parents[1]
VIBE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "vibe_saas_3h"


def test_assessor_classifies_vibe_prototype_active_and_mature(tmp_path: Path) -> None:
    assessor = ProjectAssessor()

    # 1. 3h vibe-coded prototype fixture
    proto = tmp_path / "proto"
    shutil.copytree(VIBE_FIXTURE, proto)
    proto_res = assessor.assess(proto)
    assert proto_res.maturity == "prototype_3h_chat"
    assert "nextjs-react" in proto_res.detected_stack
    assert "supabase" in proto_res.detected_stack
    assert "playwright-qa" in proto_res.detected_stack
    assert "AGENTS.md" in proto_res.missing_artifacts
    assert "docs/decisions/ADR-0001.md" in proto_res.missing_artifacts

    # 2. Active product (has README and tests, missing ADRs/AGENTS/hooks)
    active = tmp_path / "active"
    shutil.copytree(VIBE_FIXTURE, active)
    (active / "README.md").write_text("# Active App\n\nReal product.\n", encoding="utf-8")
    (active / "tests").mkdir()
    (active / "tests" / "smoke.test.ts").write_text("test('ok', () => {})\n", encoding="utf-8")
    active_res = assessor.assess(active)
    assert active_res.maturity == "active_product"

    # 3. Mature repo (AGENTS.md + ADR + tests + pre-commit + hooks)
    mature = tmp_path / "mature"
    shutil.copytree(active, mature)
    (mature / "AGENTS.md").write_text("# Routing\n", encoding="utf-8")
    (mature / "docs" / "decisions").mkdir(parents=True)
    (mature / "docs" / "decisions" / "ADR-0001.md").write_text("# ADR\n", encoding="utf-8")
    (mature / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    (mature / ".agents").mkdir()
    (mature / ".agents" / "hooks.json").write_text("{}\n", encoding="utf-8")
    mature_res = assessor.assess(mature)
    assert mature_res.maturity == "mature_repo"


def test_setup_orchestrator_dry_run_live_and_idempotency(tmp_path: Path) -> None:
    cfg = ForgeConfig(root=REPO_ROOT)
    orch = SetupOrchestrator(cfg)

    target = tmp_path / "vibe_saas_copy"
    shutil.copytree(VIBE_FIXTURE, target)
    before_files = sorted(
        p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()
    )

    # 1. Dry-run writes nothing
    dry_report = orch.run(target, dry_run=True)
    assert dry_report.dry_run is True
    assert dry_report.assessment.maturity == "prototype_3h_chat"
    assert dry_report.created_files == []
    after_dry = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
    assert after_dry == before_files

    # 2. Live apply bootstraps all expected artifacts cleanly
    live_report = orch.run(target, dry_run=False)
    assert live_report.dry_run is False
    assert live_report.hooks_schema_valid is True
    assert validate_hooks_schema(target / ".agents" / "hooks.json") is True
    assert live_report.archivist_summary["available"] is True
    assert live_report.archivist_summary["classification"] == "under-documented"
    assert live_report.sourcer_summary["available"] is True
    assert 3 <= len(live_report.sourcer_summary["top_sources"]) <= 5

    expected_paths = [
        target / "AGENTS.md",
        target / "docs" / "decisions" / "ADR-0001.md",
        target / "tasks" / "todo.md",
        target / "tasks" / "lessons.md",
        target / ".pre-commit-config.yaml",
        target / "mcp_config.recommended.json",
        target / "SOURCES_RECOMMENDATION.md",
        target / ".agents" / "hooks.json",
        target / ".agents" / "skills" / "superarchivist-setup" / "SKILL.md",
        target / "library" / "nudges" / "skill-rules.json",
    ]
    for path in expected_paths:
        assert path.is_file(), f"Missing expected setup artifact: {path}"
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"BOM found in {path}"
        assert b"\r\n" not in raw, f"CRLF found in {path}"

    agents_text = (target / "AGENTS.md").read_text(encoding="utf-8")
    assert len(agents_text.splitlines()) < 100
    assert len(agents_text.encode("utf-8")) < 24_000

    rules = json.loads((target / "library" / "nudges" / "skill-rules.json").read_text("utf-8"))
    rule_ids = {r["id"] for r in rules["rules"]}
    assert "superarchivist-setup" in rule_ids

    # 3. Idempotency: custom edits to AGENTS.md & ADR-0001.md are preserved on re-run
    custom_agents = "# Custom AGENTS\n\nKeep me intact.\n"
    (target / "AGENTS.md").write_text(custom_agents, encoding="utf-8", newline="\n")
    custom_adr = "# Custom ADR\n\nPreserved.\n"
    (target / "docs" / "decisions" / "ADR-0001.md").write_text(
        custom_adr, encoding="utf-8", newline="\n"
    )

    second_report = orch.run(target, dry_run=False)
    assert (target / "AGENTS.md").read_text(encoding="utf-8") == custom_agents
    assert (target / "docs" / "decisions" / "ADR-0001.md").read_text(encoding="utf-8") == custom_adr
    assert "AGENTS.md" in second_report.skipped_files
    assert "docs/decisions/ADR-0001.md" in second_report.skipped_files
    assert second_report.sync_report is not None
    assert second_report.sync_report.diffs == []


def test_superarchivist_setup_skill_passes_stage2_gate(tmp_path: Path) -> None:
    cfg = ForgeConfig(root=REPO_ROOT)
    pipeline = GatePipeline(cfg)

    for loc in (
        REPO_ROOT / "library" / "skills" / "superarchivist-setup",
        REPO_ROOT / ".agents" / "skills" / "superarchivist-setup",
    ):
        skill_md = loc / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        assert len(text.splitlines()) < 500
        assert estimate_tokens(text) < 5000
        assert "Use when" in text and "Do not use" in text

        # Evaluate on temp copy so GATE_REPORT.json is not written into tracked skill dir
        copy_dir = tmp_path / loc.parent.parent.name / loc.name
        shutil.copytree(loc, copy_dir)
        report = pipeline.evaluate_skill(
            copy_dir, apply_fixes=False, move_rejected=False, run_external=False
        )
        assert report.status == "passed", f"Gate failed for {loc}: {report}"
        assert report.security.safe is True
        assert report.spec.passed is True
        assert report.normalization.needs_porting is False


def test_cli_hooks_test_suite_still_passes_with_updated_skill_rules() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_hooks.py", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert "passed" in proc.stdout


def test_cli_archive_audit_source_and_setup_acceptance(tmp_path: Path) -> None:
    for subcmd in ("archive-audit", "source", "setup"):
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "forge.py"), subcmd, "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        assert subcmd in proc.stdout.lower() or "usage" in proc.stdout.lower()

    target = tmp_path / "cli_vibe"
    shutil.copytree(VIBE_FIXTURE, target)

    dry_proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "forge.py"),
            "setup",
            "--target",
            str(target),
            "--dry-run",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert "prototype_3h_chat" in dry_proc.stdout
    assert not (target / "AGENTS.md").exists()

    live_proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "forge.py"), "setup", "--target", str(target)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert "hooks_schema_valid=True" in live_proc.stdout
    assert (target / "AGENTS.md").is_file()
    assert validate_hooks_schema(target / ".agents" / "hooks.json") is True
