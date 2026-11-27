"""Intelligence-Sourcer: stack detection, source probing, scoring, and lean MCP emission."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.table import Table

from forge.config import ForgeConfig, write_json_utf8_lf, write_text_utf8_lf
from forge.sourcer.catalog import (
    ARTIFICIAL_ANALYSIS_MCP_URL,
    OPENROUTER_MODELS_URL,
    BindingType,
    ProbeResult,
    SourceCandidate,
    SourceCatalog,
)
from forge.sourcer.detector import DetectedSignal, StackDetectionResult, StackDetector
from forge.sourcer.scorer import (
    ScoredSource,
    SourceReport,
    SourceScorer,
    build_recommended_mcp_config,
    render_sources_markdown,
)

sourcer_app = typer.Typer(
    help="Detect project tech stack and recommend top 3-5 lean documentation/MCP sources.",
)
_console = Console()


class IntelligenceSourcer:
    """End-to-end orchestrator for stack detection, probing, scoring, and artifact emission."""

    def __init__(
        self,
        config: ForgeConfig | None = None,
        *,
        detector: StackDetector | None = None,
        catalog: SourceCatalog | None = None,
        scorer: SourceScorer | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config or ForgeConfig.load()
        self.detector = detector or StackDetector()
        self.catalog = catalog or SourceCatalog(self.config, http_client=http_client)
        self.scorer = scorer or SourceScorer()

    def source_project(
        self,
        target_dir: Path,
        *,
        out_dir: Path | None = None,
        min_sources: int = 3,
        max_sources: int = 5,
        dry_run: bool = False,
        write_artifacts: bool = True,
        http_client: httpx.Client | None = None,
    ) -> SourceReport:
        """Analyze `target_dir`, score primary sources, and optionally emit artifacts."""
        resolved_target = target_dir.resolve()
        detection = self.detector.detect(resolved_target)
        candidates = self.catalog.candidates_for_domains(detection.domains)
        probes = self.catalog.probe_candidates(
            candidates,
            dry_run=dry_run,
            client=http_client,
        )
        all_scored = self.scorer.score_all(candidates, detection, probes=probes)
        top_sources = self.scorer.select_top_sources(
            all_scored,
            detection,
            min_sources=min_sources,
            max_sources=max_sources,
        )

        report = SourceReport(
            project_name=detection.project_name,
            project_dir=str(resolved_target),
            dry_run=dry_run,
            detection=detection,
            top_sources=top_sources,
            all_candidates=all_scored,
            probes=list(probes.values()),
        )

        if write_artifacts:
            dest_dir = (out_dir or resolved_target).resolve()
            mcp_path = dest_dir / "mcp_config.recommended.json"
            md_path = dest_dir / "SOURCES_RECOMMENDATION.md"
            json_path = dest_dir / "SOURCES_REPORT.json"

            mcp_payload = build_recommended_mcp_config(report)
            md_content = render_sources_markdown(report)
            report.artifacts = {
                "mcp_config": str(mcp_path),
                "recommendation_md": str(md_path),
                "report_json": str(json_path),
            }

            write_json_utf8_lf(mcp_path, mcp_payload)
            write_text_utf8_lf(md_path, md_content)
            write_json_utf8_lf(json_path, report.model_dump(mode="json"))

        return report


def run_sourcing(
    target_dir: Path,
    *,
    config: ForgeConfig | None = None,
    out_dir: Path | None = None,
    min_sources: int = 3,
    max_sources: int = 5,
    dry_run: bool = False,
    write_artifacts: bool = True,
    http_client: httpx.Client | None = None,
) -> SourceReport:
    """Public convenience API to run Intelligence-Sourcer on `target_dir`."""
    sourcer = IntelligenceSourcer(config=config, http_client=http_client)
    return sourcer.source_project(
        target_dir,
        out_dir=out_dir,
        min_sources=min_sources,
        max_sources=max_sources,
        dry_run=dry_run,
        write_artifacts=write_artifacts,
        http_client=http_client,
    )


def _render_console_summary(report: SourceReport, console: Console | None = None) -> None:
    out = console or _console
    domains = ", ".join(report.detection.domains) or "general"
    table = Table(title=f"Intelligence-Sourcer: {report.project_name} ({domains})")
    table.add_column("Rank", justify="right")
    table.add_column("Source ID")
    table.add_column("Binding")
    table.add_column("Signal", justify="right")
    table.add_column("Fresh", justify="right")
    table.add_column("TokEff", justify="right")
    table.add_column("Score", justify="right")
    for idx, item in enumerate(report.top_sources, start=1):
        table.add_row(
            str(idx),
            item.candidate.id,
            item.binding_type,
            f"{item.signal_quality:.2f}",
            f"{item.freshness:.2f}",
            f"{item.token_efficiency:.2f}",
            f"{item.total_score:.3f}",
        )
    out.print(table)
    if report.artifacts:
        out.print(
            f"[dim]artifacts: mcp={report.artifacts.get('mcp_config')} "
            f"md={report.artifacts.get('recommendation_md')}[/dim]"
        )


def cmd_source(
    target: Annotated[Path, typer.Argument(help="Target project directory to inspect.")] = Path(
        "."
    ),
    out_dir: Annotated[
        Path | None,
        typer.Option("--out-dir", help="Output directory for artifacts (defaults to target)."),
    ] = None,
    max_sources: Annotated[
        int, typer.Option("--max-sources", help="Top primary sources to select (3-5 recommended).")
    ] = 5,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Run offline mock probes without network requests.")
    ] = False,
    no_write: Annotated[
        bool, typer.Option("--no-write", help="Preview recommendations without writing files.")
    ] = False,
) -> SourceReport:
    """Detect project stack and emit top 3-5 lean web/docs/MCP source recommendations."""
    cfg = ForgeConfig.load()
    bounded_max = max(3, min(5, max_sources))
    report = run_sourcing(
        target,
        config=cfg,
        out_dir=out_dir,
        min_sources=min(3, bounded_max),
        max_sources=bounded_max,
        dry_run=dry_run,
        write_artifacts=not no_write,
    )
    _render_console_summary(report)
    return report


sourcer_app.command("source")(cmd_source)


__all__: list[str] = [
    "ARTIFICIAL_ANALYSIS_MCP_URL",
    "BindingType",
    "DetectedSignal",
    "IntelligenceSourcer",
    "OPENROUTER_MODELS_URL",
    "ProbeResult",
    "ScoredSource",
    "SourceCandidate",
    "SourceCatalog",
    "SourceReport",
    "SourceScorer",
    "StackDetectionResult",
    "StackDetector",
    "build_recommended_mcp_config",
    "cmd_source",
    "render_sources_markdown",
    "run_sourcing",
    "sourcer_app",
]
