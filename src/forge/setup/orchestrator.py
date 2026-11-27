"""Unified idempotent setup orchestrator composing archivist, sourcer, sync, and docs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from forge.archivist import ProjectAuditor
from forge.config import ForgeConfig, write_json_utf8_lf, write_text_utf8_lf
from forge.setup.planner import ProjectAssessment, ProjectAssessor
from forge.sourcer import build_recommended_mcp_config, render_sources_markdown, run_sourcing
from forge.sync.workspace import SyncReport, WorkspaceSyncer, validate_hooks_schema

ActionKind = Literal["create", "skip_existing", "sync", "would_create", "would_sync"]


class PlannedAction(BaseModel):
    """Single planned or executed workspace artifact operation."""

    path: str
    action: ActionKind
    detail: str = ""


class SetupReport(BaseModel):
    """Consolidated report emitted by `SetupOrchestrator.run`."""

    target_dir: str
    dry_run: bool
    profile: str
    assessment: ProjectAssessment
    archivist_summary: dict[str, Any] = Field(default_factory=dict)
    sourcer_summary: dict[str, Any] = Field(default_factory=dict)
    sync_report: SyncReport | None = None
    actions: list[PlannedAction] = Field(default_factory=list)
    created_files: list[str] = Field(default_factory=list)
    skipped_files: list[str] = Field(default_factory=list)
    hooks_schema_valid: bool = True


def _render_agents_md(assessment: ProjectAssessment, sourcer_summary: dict[str, Any]) -> str:
    stack_str = ", ".join(assessment.detected_stack) if assessment.detected_stack else "general"
    top_sources = sourcer_summary.get("top_sources") or []
    source_lines = []
    for item in top_sources[:5]:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("id") or "source")
            url = str(item.get("url") or item.get("binding_type") or "")
            source_lines.append(f"- `{name}` ({url})" if url else f"- `{name}`")
    sources_block = (
        "\n".join(source_lines)
        if source_lines
        else "- See `SOURCES_RECOMMENDATION.md` and `mcp_config.recommended.json`"
    )

    return (
        f"# {assessment.project_name} — Agent Routing Map\n\n"
        f"Maturity: `{assessment.maturity}` | Stack: `{stack_str}` | "
        f"Profile: `{assessment.recommended_profile}`\n\n"
        "## Workspace Map\n\n"
        "- Architecture Decisions: `docs/decisions/`\n"
        "- Active Plan & Lessons: `tasks/todo.md`, `tasks/lessons.md`\n"
        "- Agent Skills & Hooks: `.agents/skills/`, `.agents/hooks.json`\n"
        "- Recommended MCPs & Sources: `mcp_config.recommended.json`, "
        "`SOURCES_RECOMMENDATION.md`\n\n"
        "## Primary Sourced References\n\n"
        f"{sources_block}\n\n"
        "## Working Rules\n\n"
        "1. Plan non-trivial work in `tasks/todo.md` and log corrections in `tasks/lessons.md`.\n"
        "2. Run exploratory live QA (`forge qa` or browser walkthrough) before `*.spec.ts`.\n"
        "3. Keep all text, JSON, YAML, and Markdown files UTF-8 without BOM and LF (`\\n`).\n"
        "4. Verify lint, typecheck, and tests before marking any task done.\n"
    )


def _render_adr_0001(assessment: ProjectAssessment, sourcer_summary: dict[str, Any]) -> str:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    stack_str = ", ".join(assessment.detected_stack) if assessment.detected_stack else "general"
    top_sources = sourcer_summary.get("top_sources") or []
    source_bullets = "\n".join(
        f"- {s.get('name', s.get('id', 'source'))}: {s.get('url', '')}"
        for s in top_sources[:5]
        if isinstance(s, dict)
    )
    if not source_bullets:
        source_bullets = "- Baseline Superarchivist + Sourcer catalog defaults"

    return (
        f"# ADR-0001: Initial Architecture & Agent Harness Baseline\n\n"
        f"- **Status:** Accepted\n"
        f"- **Date:** {today}\n"
        f"- **Maturity Classification:** `{assessment.maturity}`\n\n"
        "## Context\n\n"
        f"`{assessment.project_name}` was assessed with detected stack `{stack_str}`. "
        "To prevent context drift, secret leaks, and untested UI changes as the repository "
        "evolves, a minimal deterministic agent harness is required.\n\n"
        "## Decision\n\n"
        f"1. Apply the `{assessment.recommended_profile}` workspace profile "
        "(`.agents/skills/`, `.agents/hooks.json`, `library/nudges/skill-rules.json`).\n"
        "2. Enforce UTF-8 without BOM, LF line endings, and pre-commit checks.\n"
        "3. Wire primary documentation and token-lean MCPs via `mcp_config.recommended.json`:\n"
        f"{source_bullets}\n\n"
        "## Consequences\n\n"
        "- Existing hand-written docs remain untouched on subsequent `forge setup` runs.\n"
        "- Agents follow live-first QA and two-try skill-routing guardrails automatically.\n"
    )


def _render_tasks_todo(assessment: ProjectAssessment) -> str:
    items = "\n".join(f"- [ ] {rec}" for rec in assessment.recommendations)
    if not items:
        items = "- [ ] Review generated harness files and run baseline tests."
    return (
        f"# {assessment.project_name} — Active Task Plan\n\n"
        "## Harness Onboarding\n\n"
        f"{items}\n"
        "- [ ] Review `mcp_config.recommended.json` and enable needed MCP servers\n"
        "- [ ] Run project lint, typecheck, and smoke tests\n\n"
        "## Review Notes\n\n"
        "- Bootstrapped idempotently via `forge setup`.\n"
    )


def _render_tasks_lessons() -> str:
    return (
        "# Lessons Learned\n\n"
        "Record user corrections, recurring failure modes, and preventive rules here.\n\n"
        "## Format\n\n"
        "- **Date / Area:** Short title\n"
        "- **Root Cause:** Why the issue happened\n"
        "- **Guardrail:** Concrete check or invariant to enforce next time\n"
    )


def _render_precommit_config(assessment: ProjectAssessment) -> str:
    lines = [
        "repos:",
        "  - repo: https://github.com/pre-commit/pre-commit-hooks",
        "    rev: v5.0.0",
        "    hooks:",
        "      - id: check-json",
        "      - id: check-yaml",
        "      - id: end-of-file-fixer",
        "      - id: mixed-line-ending",
        "        args: ['--fix=lf']",
    ]
    local_hooks: list[str] = []
    stack = set(assessment.detected_stack)
    if "python-uv" in stack or "python" in stack:
        local_hooks.extend(
            [
                "      - id: ruff-check",
                "        name: Ruff Lint Check",
                "        entry: uv run ruff check .",
                "        language: system",
                "        pass_filenames: false",
                "      - id: mypy-check",
                "        name: Mypy Type Check",
                "        entry: uv run mypy src",
                "        language: system",
                "        pass_filenames: false",
            ]
        )
    if "nextjs-react" in stack or "nodejs" in stack or "typescript" in stack:
        if assessment.signals.has_package_json:
            local_hooks.extend(
                [
                    "      - id: node-lint",
                    "        name: Package Lint Check",
                    "        entry: npm run lint",
                    "        language: system",
                    "        pass_filenames: false",
                ]
            )
        if assessment.signals.has_tsconfig:
            local_hooks.extend(
                [
                    "      - id: tsc-check",
                    "        name: TypeScript Type Check",
                    "        entry: npx tsc --noEmit",
                    "        language: system",
                    "        pass_filenames: false",
                ]
            )
    if local_hooks:
        lines.extend(["  - repo: local", "    hooks:", *local_hooks])
    return "\n".join(lines) + "\n"


def _render_fallback_mcp_config(assessment: ProjectAssessment) -> dict[str, Any]:
    servers: dict[str, Any] = {
        "chrome-devtools": {
            "command": "npx",
            "args": [
                "-y",
                "chrome-devtools-mcp@latest",
                "--slim",
                "--no-usage-statistics",
                "--no-performance-crux",
            ],
        }
    }
    if "supabase" in assessment.detected_stack:
        servers["supabase-docs"] = {
            "command": "mcp2cli",
            "args": ["--session-start", "--toon", "--url", "https://supabase.com/llms.txt"],
        }
    return {"mcpServers": servers}


class SetupOrchestrator:
    """Composes assessment, archivist audit, sourcer, doc scaffolding, and WorkspaceSyncer."""

    def __init__(self, config: ForgeConfig) -> None:
        self.config = config
        self.assessor = ProjectAssessor()
        self.syncer = WorkspaceSyncer(config)

    def _run_archivist(self, target_dir: Path, dry_run: bool) -> dict[str, Any]:
        """Run read-only project documentation audit via `forge.archivist`."""
        report = ProjectAuditor().audit_project(target_dir)
        return {
            "available": True,
            "target": str(target_dir),
            "dry_run": dry_run,
            "classification": report.classification,
            "doc_score": report.doc_score,
            "tech_stack": list(report.tech_stack),
            "strengths": list(report.strengths),
            "drift_and_gaps": list(report.drift_and_gaps),
            "recommendations": [rec.model_dump() for rec in report.recommendations],
        }

    def _run_sourcer(self, target_dir: Path, dry_run: bool) -> dict[str, Any]:
        """Run stack source discovery via `forge.sourcer` without writing during dry-run."""
        report = run_sourcing(
            target_dir,
            config=self.config,
            dry_run=dry_run,
            write_artifacts=False,
        )
        top_sources = [
            {
                "id": item.candidate.id,
                "name": item.candidate.name,
                "url": item.candidate.url,
                "binding_type": item.binding_type,
                "total_score": item.total_score,
            }
            for item in report.top_sources
        ]
        return {
            "available": True,
            "target": str(target_dir),
            "dry_run": dry_run,
            "detected_domains": list(report.detection.domains),
            "top_sources": top_sources,
            "mcp_config": build_recommended_mcp_config(report),
            "sources_markdown": render_sources_markdown(report),
            "sources_report": report.model_dump(mode="json"),
        }

    def run(
        self,
        target_dir: Path,
        *,
        profile: str = "",
        dry_run: bool = False,
        live_source: bool = False,
    ) -> SetupReport:
        """Assess `target_dir` and idempotently scaffold docs, sources, rules, and hooks."""
        resolved_target = target_dir.resolve()
        if not resolved_target.exists():
            raise FileNotFoundError(f"Target directory does not exist: {resolved_target}")

        assessment = self.assessor.assess(resolved_target, profile_override=profile)
        chosen_profile = assessment.recommended_profile

        archivist_summary = self._run_archivist(resolved_target, dry_run=dry_run)
        sourcer_summary = self._run_sourcer(resolved_target, dry_run=dry_run or not live_source)

        actions: list[PlannedAction] = []
        created_files: list[str] = []
        skipped_files: list[str] = []

        # 1. Idempotent documentation & config scaffolding (never overwrite existing hand-crafted)
        decisions_dir = resolved_target / "docs" / "decisions"
        has_any_adr = decisions_dir.is_dir() and any(decisions_dir.glob("*.md"))

        doc_targets: list[tuple[Path, str, bool, str]] = [
            (
                resolved_target / "AGENTS.md",
                _render_agents_md(assessment, sourcer_summary),
                any(
                    (resolved_target / name).is_file()
                    for name in ("AGENTS.md", "GEMINI.md", "CLAUDE.md")
                ),
                "Agent routing map",
            ),
            (
                resolved_target / "docs" / "decisions" / "ADR-0001.md",
                _render_adr_0001(assessment, sourcer_summary),
                has_any_adr,
                "Initial architecture decision record",
            ),
            (
                resolved_target / "tasks" / "todo.md",
                _render_tasks_todo(assessment),
                (resolved_target / "tasks" / "todo.md").is_file(),
                "Task checklist",
            ),
            (
                resolved_target / "tasks" / "lessons.md",
                _render_tasks_lessons(),
                (resolved_target / "tasks" / "lessons.md").is_file(),
                "Lessons learned log",
            ),
            (
                resolved_target / ".pre-commit-config.yaml",
                _render_precommit_config(assessment),
                (resolved_target / ".pre-commit-config.yaml").is_file(),
                "Pre-commit hygiene checks",
            ),
        ]

        for dest_path, content, already_exists, detail in doc_targets:
            rel = dest_path.relative_to(resolved_target).as_posix()
            if already_exists:
                actions.append(PlannedAction(path=rel, action="skip_existing", detail=detail))
                skipped_files.append(rel)
            elif dry_run:
                actions.append(PlannedAction(path=rel, action="would_create", detail=detail))
            else:
                write_text_utf8_lf(dest_path, content)
                actions.append(PlannedAction(path=rel, action="create", detail=detail))
                created_files.append(rel)

        # 2. Recommended MCP config & primary sources summary (idempotent: preserve if exists)
        mcp_path = resolved_target / "mcp_config.recommended.json"
        mcp_rel = "mcp_config.recommended.json"
        mcp_payload = sourcer_summary.get("mcp_config") or _render_fallback_mcp_config(assessment)
        if mcp_path.is_file():
            actions.append(
                PlannedAction(path=mcp_rel, action="skip_existing", detail="MCP config exists")
            )
            skipped_files.append(mcp_rel)
        elif dry_run:
            actions.append(
                PlannedAction(path=mcp_rel, action="would_create", detail="Recommended MCP config")
            )
        else:
            if isinstance(mcp_payload, str):
                mcp_obj = json.loads(mcp_payload)
            else:
                mcp_obj = mcp_payload
            write_json_utf8_lf(mcp_path, mcp_obj)
            actions.append(
                PlannedAction(path=mcp_rel, action="create", detail="Recommended MCP config")
            )
            created_files.append(mcp_rel)

        sources_md = sourcer_summary.get("sources_markdown")
        if isinstance(sources_md, str) and sources_md:
            rec_path = resolved_target / "SOURCES_RECOMMENDATION.md"
            rec_rel = "SOURCES_RECOMMENDATION.md"
            if rec_path.is_file():
                actions.append(
                    PlannedAction(path=rec_rel, action="skip_existing", detail="Sources doc exists")
                )
                skipped_files.append(rec_rel)
            elif dry_run:
                actions.append(
                    PlannedAction(
                        path=rec_rel, action="would_create", detail="Primary sources guide"
                    )
                )
            else:
                write_text_utf8_lf(rec_path, sources_md)
                actions.append(
                    PlannedAction(path=rec_rel, action="create", detail="Primary sources guide")
                )
                created_files.append(rec_rel)

        # 3. Sync curated skills, Antigravity hooks, and filtered skill-rules.json
        sync_report = self.syncer.sync(chosen_profile, resolved_target, dry_run=dry_run)
        for planned_file in sync_report.planned_files:
            try:
                rel = Path(planned_file).resolve().relative_to(resolved_target).as_posix()
            except ValueError:
                rel = planned_file
            actions.append(
                PlannedAction(
                    path=rel,
                    action="would_sync" if dry_run else "sync",
                    detail=f"profile={chosen_profile}",
                )
            )
            if not dry_run:
                created_files.append(rel)

        hooks_ok = sync_report.hooks_schema_valid
        hooks_path = resolved_target / ".agents" / "hooks.json"
        if not dry_run and hooks_path.is_file():
            hooks_ok = validate_hooks_schema(hooks_path)

        return SetupReport(
            target_dir=str(resolved_target),
            dry_run=dry_run,
            profile=chosen_profile,
            assessment=assessment,
            archivist_summary=archivist_summary,
            sourcer_summary=sourcer_summary,
            sync_report=sync_report,
            actions=actions,
            created_files=created_files,
            skipped_files=skipped_files,
            hooks_schema_valid=hooks_ok,
        )
