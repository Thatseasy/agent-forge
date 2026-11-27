"""Unit and integration tests for forge.archivist (auditor, extractor, CLI)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from forge.archivist import (
    PatternExtractor,
    ProjectAuditor,
    archivist_app,
    run_archive_audit,
    write_audit_reports,
)
from forge.config import ForgeConfig, estimate_tokens
from forge.gate import GatePipeline


def _write_lf(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.replace("\r\n", "\n"), encoding="utf-8", newline="\n")
    return path


def _make_exemplary_python_project(root: Path) -> Path:
    proj = root / "exemplary-py"
    _write_lf(
        proj / "README.md",
        "# Exemplary Python Service\n\nSetup via `uv sync` and `uv run pytest`.\n"
        "Architecture and module layout documented cleanly.\n",
    )
    _write_lf(
        proj / "AGENTS.md",
        "# Routing Map\n\n- Entrypoint: `uv run python main.py --help`\n- Use LF + UTF-8.\n",
    )
    _write_lf(proj / "CLAUDE.md", "@AGENTS.md\n")
    _write_lf(proj / "docs" / "decisions" / "ADR-0001.md", "# ADR-0001: Use uv + Typer\n")
    _write_lf(proj / "tasks" / "todo.md", "- [x] Initial scaffold\n")
    _write_lf(proj / "tasks" / "lessons.md", "# Lessons\n\n- Always normalize CRLF to LF.\n")
    _write_lf(proj / "CHANGELOG.md", "## 0.1.0\n\n- Initial release.\n")
    _write_lf(
        proj / "pyproject.toml",
        '[project]\nname = "exemplary-py"\ndependencies = ["typer", "pydantic"]\n'
        "[tool.ruff]\nline-length = 100\n[tool.mypy]\ndisallow_untyped_defs = true\n",
    )
    _write_lf(proj / "uv.lock", "version = 1\n")
    _write_lf(proj / ".pre-commit-config.yaml", "repos: []\n")
    _write_lf(
        proj / ".agents" / "hooks.json",
        json.dumps({"suite": {"enabled": True, "PreToolUse": []}}, indent=2) + "\n",
    )
    _write_lf(proj / ".agents" / "hooks" / "guard.py", "print('{}')\n")
    _write_lf(proj / ".agents" / "rules" / "style.md", "# Style\nUse type hints.\n")
    _write_lf(
        proj / ".agents" / "skills" / "csv-clean" / "SKILL.md",
        (
            "---\n"
            "name: csv-clean\n"
            "description: Normalize CSV exports safely. Use when cleaning CSV tables. "
            "Do not use for image processing.\n"
            "---\n\n"
            "# CSV Clean\n\nValidate headers and write UTF-8 LF output.\n"
        ),
    )
    # Synthetic fixture must NOT leak Next.js into exemplary-py tech stack
    _write_lf(
        proj / "tests" / "fixtures" / "demo_web" / "package.json",
        '{"dependencies": {"next": "16.0.0", "react": "19.0.0"}}\n',
    )
    return proj


def _make_drifted_web_project(root: Path) -> Path:
    proj = root / "drifted-web"
    _write_lf(
        proj / "README.md",
        "This is a [Next.js](https://nextjs.org) project bootstrapped with "
        "[`create-next-app`](https://nextjs.org).\n",
    )
    _write_lf(
        proj / "package.json",
        '{"name": "drifted-web", "dependencies": {"next": "16.0.0", "react": "19.0.0"}}\n',
    )
    # Secret must never be read or leaked by auditor
    _write_lf(proj / ".env", "SUPER_SECRET_TOKEN=do_not_leak_this_value_12345\n")
    _write_lf(
        proj / ".claude" / "skills" / "legacy-helper" / "SKILL.md",
        (
            "---\n"
            "name: legacy-helper\n"
            "description: Legacy helper workflow.\n"
            "---\n\n"
            "# Legacy Helper\n\nUse $ARGUMENTS and check .claude/skills/other/SKILL.md.\n"
        ),
    )
    return proj


def test_auditor_classifies_exemplary_drifted_nested_and_empty(tmp_path: Path) -> None:
    exemplary = _make_exemplary_python_project(tmp_path)
    drifted = _make_drifted_web_project(tmp_path)
    empty_dir = tmp_path / "empty-proj"
    empty_dir.mkdir()

    nested_outer = tmp_path / "wrapped-app"
    nested_inner = nested_outer / "wrapped-app"
    _write_lf(nested_inner / "README.md", "# Wrapped App\n\nDetailed documentation for app.\n")
    _write_lf(
        nested_inner / "package.json",
        '{"name": "wrapped-app", "dependencies": {"@supabase/supabase-js": "^2.0.0"}}\n',
    )

    auditor = ProjectAuditor()
    portfolio = auditor.audit_portfolio(
        [exemplary, drifted, nested_outer, empty_dir, tmp_path / "missing-dir"]
    )

    assert portfolio.project_count == 5
    assert "exemplary-py" in portfolio.exemplary_projects
    assert "drifted-web" in portfolio.under_documented_projects
    assert "empty-proj" in portfolio.empty_projects

    by_name = {p.name: p for p in portfolio.projects}

    ex_rep = by_name["exemplary-py"]
    assert ex_rep.classification == "exemplary"
    assert ex_rep.doc_score >= 65
    assert "python-uv" in ex_rep.tech_stack
    assert "nextjs" not in ex_rep.tech_stack
    assert ex_rep.agent_artifacts.uses_agents_pointer_in_claude_md is True
    assert ex_rep.agent_artifacts.hooks_valid_schema is True

    dr_rep = by_name["drifted-web"]
    assert dr_rep.classification == "under-documented"
    assert "nextjs" in dr_rep.tech_stack
    assert dr_rep.has_env_file is True
    assert dr_rep.has_env_example is False
    assert any("template drift" in g.lower() for g in dr_rep.drift_and_gaps)
    assert any(r.target_path == ".env.example" for r in dr_rep.recommendations)

    wr_rep = by_name["wrapped-app"]
    assert wr_rep.nested_root == "wrapped-app"
    assert "supabase" in wr_rep.tech_stack

    out_dir = tmp_path / "audit_out"
    md_path, json_path = write_audit_reports(portfolio, out_dir)
    md_bytes = md_path.read_bytes()
    json_bytes = json_path.read_bytes()
    assert not md_bytes.startswith(b"\xef\xbb\xbf")
    assert not json_bytes.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in md_bytes
    assert b"\r\n" not in json_bytes
    assert "do_not_leak_this_value_12345" not in md_bytes.decode("utf-8")
    assert "do_not_leak_this_value_12345" not in json_bytes.decode("utf-8")


def test_extractor_produces_stage2_valid_skills(tmp_path: Path) -> None:
    exemplary = _make_exemplary_python_project(tmp_path / "projects")
    drifted = _make_drifted_web_project(tmp_path / "projects")

    forge_root = tmp_path / "forge_ws"
    (forge_root / "library" / "skills").mkdir(parents=True)
    cfg = ForgeConfig(root=forge_root)

    audit_report, extraction = run_archive_audit(
        projects=[exemplary, drifted],
        out_dir=forge_root / "staging" / "archive_audit",
        extract_skills=True,
        inbox_dir=forge_root / "staging" / "inbox",
        config=cfg,
    )
    assert audit_report.project_count == 2
    assert extraction is not None
    slugs = {c.slug for c in extraction.candidates}
    assert "portfolio-routing-harness" in slugs
    assert "portfolio-python-utf8-cli" in slugs
    assert "portfolio-csv-clean" in slugs
    assert "portfolio-legacy-helper" in slugs

    pipeline = GatePipeline(cfg)
    for cand in extraction.candidates:
        skill_md = Path(cand.skill_md_path)
        raw = skill_md.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" not in raw
        text = raw.decode("utf-8")
        assert len(text.splitlines()) < 500
        assert estimate_tokens(text) < 5000
        assert "Use when" in text and "Do not use" in text

        gate_rep = pipeline.evaluate_skill(
            Path(cand.skill_dir),
            apply_fixes=False,
            move_rejected=False,
            run_external=False,
        )
        assert gate_rep.status == "passed", f"{cand.slug} failed gate: {gate_rep}"
        assert gate_rep.security.safe is True


def test_extractor_skips_skills_already_in_library(tmp_path: Path) -> None:
    proj = _make_exemplary_python_project(tmp_path / "projects")
    forge_root = tmp_path / "forge_ws"
    _write_lf(
        forge_root / "library" / "skills" / "csv-clean" / "SKILL.md",
        "---\nname: csv-clean\ndescription: Already curated. Use when CSV. Do not use.\n---\n",
    )
    cfg = ForgeConfig(root=forge_root)
    report = ProjectAuditor().audit_portfolio([proj])
    extraction = PatternExtractor(cfg).extract(report, out_dir=forge_root / "staging" / "inbox")
    slugs = {c.slug for c in extraction.candidates}
    assert "portfolio-csv-clean" not in slugs
    assert any("csv-clean" in item for item in extraction.skipped)


def test_archivist_cli_and_read_only_guarantee(tmp_path: Path) -> None:
    proj = _make_exemplary_python_project(tmp_path / "projects")
    before_mtimes = {str(p): p.stat().st_mtime_ns for p in proj.rglob("*") if p.is_file()}

    out_dir = tmp_path / "out_audit"
    inbox_dir = tmp_path / "out_inbox"
    runner = CliRunner()
    result = runner.invoke(
        archivist_app,
        [
            "--project",
            str(proj),
            "--out-dir",
            str(out_dir),
            "--inbox-dir",
            str(inbox_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Portfolio Archive & Documentation Audit" in result.output
    assert (out_dir / "ARCHIVE_AUDIT.md").is_file()
    assert (out_dir / "ARCHIVE_AUDIT.json").is_file()
    assert (inbox_dir / "portfolio-routing-harness" / "SKILL.md").is_file()

    after_mtimes = {str(p): p.stat().st_mtime_ns for p in proj.rglob("*") if p.is_file()}
    assert before_mtimes == after_mtimes


def test_real_staging_archive_audit_outputs_exist_and_are_clean() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    audit_md = repo_root / "staging" / "archive_audit" / "ARCHIVE_AUDIT.md"
    audit_json = repo_root / "staging" / "archive_audit" / "ARCHIVE_AUDIT.json"
    if not audit_md.is_file() or not audit_json.is_file():
        pytest.skip("staging/archive_audit is gitignored and generated by local archive-audit runs")

    for path in (audit_md, audit_json):
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" not in raw

    data = json.loads(audit_json.read_text(encoding="utf-8"))
    assert data["project_count"] >= 1
    assert isinstance(data["projects"], list)


def test_archivist_root_discovery_and_duplicate_name_disambiguation(tmp_path: Path) -> None:
    root_a = tmp_path / "group-a"
    root_b = tmp_path / "group-b"
    app_a = root_a / "shared-app"
    app_b = root_b / "shared-app"
    _write_lf(app_a / "README.md", "# Shared App A\n\nDetailed documentation for A.\n")
    _write_lf(app_b / "README.md", "# Shared App B\n\nDetailed documentation for B.\n")

    out_dir = tmp_path / "audit_out"
    inbox_dir = tmp_path / "inbox_out"
    runner = CliRunner()
    result = runner.invoke(
        archivist_app,
        [
            "--root",
            str(root_a),
            "--root",
            str(root_b),
            "--out-dir",
            str(out_dir),
            "--inbox-dir",
            str(inbox_dir),
            "--no-extract",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads((out_dir / "ARCHIVE_AUDIT.json").read_text(encoding="utf-8"))
    names = {p["name"] for p in data["projects"]}
    assert names == {"group-a/shared-app", "group-b/shared-app"}
