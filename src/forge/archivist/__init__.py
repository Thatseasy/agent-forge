"""Automated Project Archivist & Portfolio Auditor (read-only portfolio scanner)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge.archivist.auditor import (
    IGNORED_DIRS,
    AgentArtifactSummary,
    AuditRecommendation,
    DocHealthRecord,
    PortfolioAuditReport,
    ProjectAuditor,
    ProjectAuditReport,
    render_archive_audit_markdown,
    write_audit_reports,
)
from forge.archivist.extractor import (
    ExtractedSkillCandidate,
    ExtractionReport,
    PatternExtractor,
)
from forge.config import ForgeConfig
from forge.paths import resolve_target_workspace

archivist_app = typer.Typer(
    name="archive-audit",
    help="Read-only portfolio documentation audit and reusable skill extraction.",
)


def expand_portfolio_targets(
    projects: list[Path] | None = None,
    roots: list[Path] | None = None,
) -> list[Path]:
    """Expand explicit projects and container roots into deduplicated project paths."""
    explicit_mode = bool(projects) or bool(roots)
    raw_projects = (
        [Path(p) for p in projects]
        if projects
        else ([] if explicit_mode else [resolve_target_workspace(None)])
    )
    raw_roots = [Path(r) for r in roots] if roots else []

    seen: set[Path] = set()
    targets: list[Path] = []

    def _add(candidate: Path) -> None:
        try:
            key = candidate.resolve()
        except OSError:
            key = candidate
        if key not in seen:
            seen.add(key)
            targets.append(candidate)

    for proj in raw_projects:
        _add(proj)

    for root in raw_roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if child.is_dir() and child.name not in IGNORED_DIRS and not child.name.startswith("."):
                _add(child)

    return targets


def run_archive_audit(
    projects: list[Path] | None = None,
    roots: list[Path] | None = None,
    out_dir: Path | None = None,
    extract_skills: bool = True,
    inbox_dir: Path | None = None,
    config: ForgeConfig | None = None,
) -> tuple[PortfolioAuditReport, ExtractionReport | None]:
    """Run a read-only audit across `projects`/`roots`, write reports, and extract skills."""
    cfg = config or ForgeConfig.load()
    target_projects = expand_portfolio_targets(projects=projects, roots=roots)
    report_dir = (out_dir or (cfg.root / "staging" / "archive_audit")).resolve()

    auditor = ProjectAuditor()
    audit_report = auditor.audit_portfolio(target_projects)
    write_audit_reports(audit_report, report_dir)

    extraction_report: ExtractionReport | None = None
    if extract_skills:
        extractor = PatternExtractor(cfg)
        extraction_report = extractor.extract(
            audit_report,
            out_dir=(inbox_dir or cfg.staging_inbox_dir),
        )

    return audit_report, extraction_report


@archivist_app.callback(invoke_without_command=True)
def cmd_archive_audit(
    project: Annotated[
        list[Path] | None,
        typer.Option("--project", "-p", help="Project directory to scan (repeatable)."),
    ] = None,
    root: Annotated[
        list[Path] | None,
        typer.Option(
            "--root",
            "-r",
            help="Container directory whose subprojects are scanned (repeatable).",
        ),
    ] = None,
    out_dir: Annotated[
        Path | None,
        typer.Option("--out-dir", help="Output directory for ARCHIVE_AUDIT.md/.json."),
    ] = None,
    extract_skills: Annotated[
        bool,
        typer.Option("--extract/--no-extract", help="Extract portfolio skills to staging/inbox/."),
    ] = True,
    inbox_dir: Annotated[
        Path | None,
        typer.Option("--inbox-dir", help="Override staging/inbox/ output directory."),
    ] = None,
) -> None:
    """Audit local project repositories read-only and extract reusable skill candidates."""
    cfg = ForgeConfig.load()
    audit_report, extraction_report = run_archive_audit(
        projects=project,
        roots=root,
        out_dir=out_dir,
        extract_skills=extract_skills,
        inbox_dir=inbox_dir,
        config=cfg,
    )

    console = Console()
    table = Table(title="Portfolio Archive & Documentation Audit")
    table.add_column("Project")
    table.add_column("Class")
    table.add_column("Score", justify="right")
    table.add_column("Tech Stack")
    table.add_column("Skills", justify="right")
    for proj in audit_report.projects:
        skills_count = len(proj.agent_artifacts.agents_skills) + len(
            proj.agent_artifacts.claude_skills
        )
        table.add_row(
            proj.name,
            proj.classification,
            f"{proj.doc_score}/100",
            ", ".join(proj.tech_stack),
            str(skills_count),
        )
    console.print(table)
    if extraction_report is not None:
        console.print(
            f"[dim]Extracted {len(extraction_report.candidates)} portfolio skill(s) to "
            f"{extraction_report.output_dir}[/dim]"
        )


__all__ = [
    "AgentArtifactSummary",
    "AuditRecommendation",
    "DEFAULT_PORTFOLIO_PATHS",
    "DEFAULT_PORTFOLIO_ROOTS",
    "DocHealthRecord",
    "ExtractedSkillCandidate",
    "ExtractionReport",
    "PatternExtractor",
    "PortfolioAuditReport",
    "ProjectAuditReport",
    "ProjectAuditor",
    "archivist_app",
    "cmd_archive_audit",
    "expand_portfolio_targets",
    "render_archive_audit_markdown",
    "run_archive_audit",
    "write_audit_reports",
]
