"""Acceptance tests for Stage 4 token pruner, retro nudges, and usage pruning."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from forge.config import ForgeConfig
from forge.refine import RetroEngine, audit_and_prune_skills, propose_skill_pruning


def test_pruner_detects_dead_sections_and_shell_sequences(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        (
            "---\n"
            "name: demo\n"
            "description: CLI refactor helper. Use when editing CLIs. Do not use for UI.\n"
            "---\n\n"
            "# Demo\n\n"
            "## Help Flag Handling\n\n"
            "Always add general coding practices and multi-paragraph docstrings everywhere.\n\n"
            "## Batch Shell Steps\n\n"
            "```bash\n"
            "python step1.py\n"
            "python step2.py\n"
            "python step3.py\n"
            "```\n"
        ),
        encoding="utf-8",
    )
    (skill_dir / "BENCHMARK.json").write_text(
        json.dumps(
            {
                "trial_records": [
                    {"arm": "vanilla", "task_id": "task-help-flag", "passed": True},
                    {"arm": "vanilla", "task_id": "task-help-flag", "passed": True},
                ]
            }
        ),
        encoding="utf-8",
    )

    prop = propose_skill_pruning(skill_dir)
    assert "## Help Flag Handling" in prop.dead_sections
    assert "## Batch Shell Steps" in prop.script_extraction_candidates
    assert prop.tokens_after < prop.tokens_before
    assert (skill_dir / "SKILL.pruned.md").exists()


def test_retro_caps_at_ten_and_routes_canonically(tmp_path: Path) -> None:
    cfg = ForgeConfig(root=tmp_path)
    tx = tmp_path / "transcript.jsonl"
    lines = [
        json.dumps({"type": "USER_INPUT", "content": f"Never do mistake {i} with skill"})
        for i in range(15)
    ]
    tx.write_text("\n".join(lines) + "\n", encoding="utf-8")

    engine = RetroEngine(cfg)
    nudges = engine.run(mode="session", transcript_path=tx)
    assert len(nudges) == 10
    valid_targets = {"agents_md", "skill_update", "new_hook", "new_linter", "delete_stale_rule"}
    assert all(n.target in valid_targets for n in nudges)
    assert all(Path(n.backlog_file).exists() for n in nudges)

    applied_path = engine.apply_nudge(nudges[0].id)
    assert "applied: true" in applied_path.read_text("utf-8")


def test_usage_audit_flags_and_archives_unused_library_skill_outside_agents(
    tmp_path: Path,
) -> None:
    cfg = ForgeConfig(root=tmp_path)
    unused = tmp_path / "library" / "skills" / "stale-skill"
    used = tmp_path / "library" / "skills" / "hot-skill"
    unused.mkdir(parents=True)
    used.mkdir(parents=True)
    (unused / "SKILL.md").write_text("---\nname: stale-skill\ndescription: Old.\n---\n", "utf-8")
    (used / "SKILL.md").write_text("---\nname: hot-skill\ndescription: Active.\n---\n", "utf-8")

    state_dir = tmp_path / ".agents" / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "metrics.jsonl").write_text(
        json.dumps({"skill_loaded": ".agents/skills/hot-skill/SKILL.md"}) + "\n",
        encoding="utf-8",
    )

    rep = audit_and_prune_skills(cfg, days=30, apply_archive=True, dry_run=False)
    assert rep.flagged_count == 1
    assert not unused.exists()
    archived = list((tmp_path / "archive" / "deprecated").glob("*-stale-skill"))
    assert len(archived) == 1
    assert (archived[0] / "DEPRECATION_REASON.md").exists()
    assert not (tmp_path / ".agents" / "skills" / "_archive").exists()


def test_cli_retro_and_prune_dry_run() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    retro_proc = subprocess.run(
        [sys.executable, str(repo_root / "forge.py"), "retro", "--mode", "session", "--dry-run"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert "Stage 4 Retro" in retro_proc.stdout

    prune_proc = subprocess.run(
        [sys.executable, str(repo_root / "forge.py"), "prune", "--days", "30", "--dry-run"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert "Stage 4 Usage & Token Audit" in prune_proc.stdout
