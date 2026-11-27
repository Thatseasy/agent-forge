"""Extract high-signal reusable patterns and skills from audited projects into staging/inbox/."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from forge.archivist.auditor import PortfolioAuditReport, ProjectAuditReport
from forge.config import ForgeConfig, estimate_tokens, write_json_utf8_lf, write_text_utf8_lf

CLAUDE_PORT_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\.claude/skills/"), ".agents/skills/"),
    (re.compile(r"\.claude/hooks/"), ".agents/hooks/"),
    (re.compile(r"\$ARGUMENTS\b"), "user input arguments"),
    (re.compile(r"Task\s*\("), "invoke_subagent("),
)


class ExtractedSkillCandidate(BaseModel):
    """Metadata for a single extracted portfolio skill staged for Stage 2 Gate."""

    slug: str
    name: str
    source_project: str
    source_path: str
    pattern_kind: Literal["project_skill", "architectural_pattern"]
    skill_dir: str
    skill_md_path: str
    line_count: int
    token_estimate: int
    rationale: str


class ExtractionReport(BaseModel):
    """Summary of all skills extracted from a portfolio audit."""

    extracted_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    output_dir: str
    candidates: list[ExtractedSkillCandidate] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


def _slugify_portfolio(raw: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-")
    if not cleaned:
        cleaned = "pattern"
    if not cleaned.startswith("portfolio-"):
        cleaned = f"portfolio-{cleaned}"
    return cleaned[:64]


def _split_frontmatter(raw_text: str) -> tuple[dict[str, Any], str]:
    lines = raw_text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}, raw_text
    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx == -1:
        return {}, raw_text
    try:
        loaded = yaml.safe_load("\n".join(lines[1:end_idx]))
        meta = loaded if isinstance(loaded, dict) else {}
    except yaml.YAMLError:
        meta = {}
    body = "\n".join(lines[end_idx + 1 :]).strip()
    return meta, body


def _sanitize_skill_text(text: str) -> str:
    cleaned = text.lstrip("\ufeff")
    for pattern, repl in CLAUDE_PORT_REPLACEMENTS:
        cleaned = pattern.sub(repl, cleaned)
    return cleaned


def _ensure_trigger_description(desc: str, fallback_topic: str) -> str:
    base = " ".join(desc.split()).strip()
    if not base:
        base = f"Apply proven {fallback_topic} workflow extracted from portfolio projects."
    lowered = base.lower()
    if "use when" not in lowered:
        base = f"{base} Use when applying {fallback_topic} conventions."
    if "do not use" not in base.lower():
        base = f"{base} Do not use for unrelated UI styling or ad-hoc experiments."
    if len(base) > 1000:
        base = base[:997].rstrip() + "..."
    return base


def _format_skill_md(name: str, description: str, body: str, max_body_lines: int = 320) -> str:
    frontmatter = yaml.safe_dump(
        {"name": name[:64], "description": description},
        sort_keys=False,
        allow_unicode=True,
        width=1000,
    ).strip()
    body_lines = body.strip().splitlines()
    if len(body_lines) > max_body_lines:
        body_lines = body_lines[:max_body_lines] + [
            "",
            "*(Truncated by PatternExtractor to stay well within the 500-line context budget.)*",
        ]
    rendered = f"---\n{frontmatter}\n---\n\n" + "\n".join(body_lines).rstrip() + "\n"
    return _sanitize_skill_text(rendered)


def _has_python_cli_pattern(project: ProjectAuditReport, project_root: Path) -> bool:
    if "python-uv" not in project.tech_stack and "python" not in project.tech_stack:
        return False
    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        text = pyproject.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False
    return "typer" in text or "ruff" in text or "mypy" in text


def _build_routing_harness_skill(project: ProjectAuditReport) -> tuple[str, str, str]:
    slug = "portfolio-routing-harness"
    desc = (
        "Structure project documentation with a compact AGENTS.md routing map, @AGENTS.md "
        "pointers, scoped rules, and tasks/lessons.md scar-tissue tracking. "
        "Use when onboarding an agent harness or auditing project docs. "
        "Do not use for runtime application code refactoring or CSS layout edits."
    )
    body = f"""# Portfolio Routing Harness Pattern

Distilled from exemplary portfolio repository `{project.name}` (`{project.doc_score}/100`).

## Core Principles

1. **Single Compact Routing Map (`AGENTS.md`):** Keep `AGENTS.md` under 100 lines and 24 KB.
   Store entrypoints, module layout, and mandatory invariants only; delegate details to skills.
2. **Cross-Agent Pointer (`CLAUDE.md` / `GEMINI.md`):** Point secondary agent files to
   `@AGENTS.md` instead of duplicating rules across multiple files.
3. **Scoped Rules (`.agents/rules/`):** Attach `glob` or `model_decision` frontmatter so
   specialized guardrails load only when matching files or workflows are active.
4. **Decision & Scar-Tissue Memory:** Record architectural choices in
   `docs/decisions/ADR-XXXX.md` and capture real failure root causes in `tasks/lessons.md`.
5. **Deterministic Verification Hooks:** Configure `.agents/hooks.json` (`PreInvocation`,
   `PreToolUse`, `PostToolUse`, `Stop`) and `.pre-commit-config.yaml` for automatic checks.

## Checklist

- Verify `README.md` describes actual project commands (never leave starter boilerplate).
- Verify `.env.example` exists whenever `.env` is used, with placeholder keys only.
- Enforce UTF-8 without BOM and LF line endings across config, Markdown, and source files.
"""
    return slug, desc, body


def _build_python_utf8_cli_skill(project: ProjectAuditReport) -> tuple[str, str, str]:
    slug = "portfolio-python-utf8-cli"
    desc = (
        "Harden Python CLI modules with deterministic record normalization, Windows-safe "
        "UTF-8 without BOM and LF file writes, strict exit codes, and uv verification. "
        "Use when refactoring Python CLI tools or fixing file encoding bugs. "
        "Do not use for frontend React styling or SQL database migrations."
    )
    body = f"""# Python UTF-8 & Deterministic CLI Hardening

Extracted from exemplary `python-uv` repository `{project.name}`.

## Rules for Python CLI Modules

1. **Windows-Safe File Output (UTF-8, No BOM, LF):**
   - Always normalize line endings (`text.replace("\\r\\n", "\\n").replace("\\r", "\\n")`)
     and write with `encoding="utf-8"` and `newline="\\n"`.
   - Never emit a UTF-8 BOM (`\\ufeff`), and serialize JSON manifests with
     `json.dumps(..., indent=2, sort_keys=True, ensure_ascii=False) + "\\n"`.
2. **Deterministic Record & Manifest Processing:**
   - Deduplicate records case-insensitively through one shared normalization helper while
     preserving clean canonical casing and ignoring blank/whitespace entries.
   - Handle empty input lists gracefully and emit deterministic sorted keys in manifests.
3. **Explicit CLI Contract (`--help` & Exit Codes):**
   - Provide clear `--help` output via `argparse` or `typer`.
   - Validate input file paths upfront; print a concise error and return a non-zero exit
     code (`sys.exit(1)` or `typer.Exit(code=1)`) when required files are missing.
4. **Small Single-Purpose Functions & Strict Typing:**
   - Separate pure transformation helpers from CLI argument parsing so unit tests can
     import functions directly without subprocess overhead.
   - Annotate all signatures explicitly (`disallow_untyped_defs = true`) with early returns.
5. **Mandatory Verification Loop (`uv run`):**
   - Run `uv run ruff check`, `uv run mypy`, and `uv run pytest -q` after every edit.
"""
    return slug, desc, body


class PatternExtractor:
    """Extracts reusable skills and architecture patterns from audited projects."""

    def __init__(self, config: ForgeConfig | None = None) -> None:
        self.config = config

    def _default_out_dir(self) -> Path:
        if self.config is not None:
            return self.config.staging_inbox_dir
        return Path.cwd() / "staging" / "inbox"

    def _write_candidate(
        self,
        out_dir: Path,
        slug: str,
        description: str,
        body: str,
        source_project: str,
        source_path: str,
        pattern_kind: Literal["project_skill", "architectural_pattern"],
        rationale: str,
    ) -> ExtractedSkillCandidate | None:
        safe_slug = _slugify_portfolio(slug)
        skill_dir = out_dir / safe_slug
        skill_md_content = _format_skill_md(safe_slug, description, body)
        line_count = len(skill_md_content.splitlines())
        token_est = estimate_tokens(skill_md_content)
        if line_count >= 500 or token_est >= 5000:
            return None

        skill_md_path = write_text_utf8_lf(skill_dir / "SKILL.md", skill_md_content)
        write_json_utf8_lf(
            skill_dir / "DISCOVERY_META.json",
            {
                "slug": safe_slug,
                "name": safe_slug,
                "source_url": source_path,
                "source_type": f"portfolio-{pattern_kind}",
                "source_project": source_project,
                "extracted_at": datetime.now(UTC).isoformat(),
                "hype_stars": 0,
                "tag": "underdog",
                "staging_path": str(skill_dir),
            },
        )
        return ExtractedSkillCandidate(
            slug=safe_slug,
            name=safe_slug,
            source_project=source_project,
            source_path=source_path,
            pattern_kind=pattern_kind,
            skill_dir=str(skill_dir),
            skill_md_path=str(skill_md_path),
            line_count=line_count,
            token_estimate=token_est,
            rationale=rationale,
        )

    def _extract_project_skills(
        self,
        project: ProjectAuditReport,
        out_dir: Path,
        seen_slugs: set[str],
        skipped: list[str],
    ) -> list[ExtractedSkillCandidate]:
        extracted: list[ExtractedSkillCandidate] = []
        project_root = Path(project.path)
        if not project_root.is_dir():
            return extracted

        skill_roots = (
            project_root / ".agents" / "skills",
            project_root / ".claude" / "skills",
        )
        for s_root in skill_roots:
            if not s_root.is_dir():
                continue
            for entry in sorted(s_root.iterdir()):
                if not entry.is_dir() or entry.name.startswith("_"):
                    continue
                # Skip internal pipeline skills or skills already curated in library/skills
                if project.name == "agent-forge" and entry.name.startswith("forge-"):
                    skipped.append(f"{project.name}/{entry.name}: internal forge-* pipeline skill")
                    continue
                if (
                    self.config is not None
                    and (self.config.library_skills_dir / entry.name / "SKILL.md").is_file()
                ):
                    skipped.append(
                        f"{project.name}/{entry.name}: already present in library/skills"
                    )
                    continue
                skill_md = entry / "SKILL.md"
                if not skill_md.is_file():
                    continue
                raw = skill_md.read_text(encoding="utf-8", errors="replace")
                meta, body = _split_frontmatter(raw)
                if not body.strip():
                    skipped.append(f"{project.name}/{entry.name}: empty body")
                    continue
                slug = _slugify_portfolio(entry.name)
                if slug in seen_slugs:
                    continue
                desc = _ensure_trigger_description(
                    str(meta.get("description") or ""),
                    entry.name.replace("-", " "),
                )
                provenance_note = (
                    f"> Extracted read-only from `{project.name}` (`{entry.name}/SKILL.md`).\n\n"
                )
                candidate = self._write_candidate(
                    out_dir=out_dir,
                    slug=slug,
                    description=desc,
                    body=provenance_note + body,
                    source_project=project.name,
                    source_path=str(skill_md),
                    pattern_kind="project_skill",
                    rationale=f"Reusable skill `{entry.name}` discovered in `{project.name}`.",
                )
                if candidate is not None:
                    seen_slugs.add(candidate.slug)
                    extracted.append(candidate)
                else:
                    skipped.append(f"{project.name}/{entry.name}: exceeded size/token budget")
        return extracted

    def extract(
        self,
        report: PortfolioAuditReport,
        out_dir: Path | None = None,
        include_project_skills: bool = True,
    ) -> ExtractionReport:
        """Extract reusable portfolio skill candidates into `staging/inbox/portfolio-<slug>/`."""
        target_dir = (out_dir or self._default_out_dir()).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        candidates: list[ExtractedSkillCandidate] = []
        skipped: list[str] = []
        seen_slugs: set[str] = set()

        # 1. Extract architectural patterns from exemplary projects first
        exemplary = [p for p in report.projects if p.classification == "exemplary"]
        for proj in exemplary:
            proj_root = Path(proj.path)
            if "portfolio-routing-harness" not in seen_slugs:
                slug, desc, body = _build_routing_harness_skill(proj)
                cand = self._write_candidate(
                    out_dir=target_dir,
                    slug=slug,
                    description=desc,
                    body=body,
                    source_project=proj.name,
                    source_path=proj.path,
                    pattern_kind="architectural_pattern",
                    rationale=(
                        f"Exemplary AGENTS.md + rules + hooks harness observed in `{proj.name}`."
                    ),
                )
                if cand is not None:
                    seen_slugs.add(cand.slug)
                    candidates.append(cand)

            if "portfolio-python-utf8-cli" not in seen_slugs and _has_python_cli_pattern(
                proj, proj_root
            ):
                slug, desc, body = _build_python_utf8_cli_skill(proj)
                cand = self._write_candidate(
                    out_dir=target_dir,
                    slug=slug,
                    description=desc,
                    body=body,
                    source_project=proj.name,
                    source_path=proj.path,
                    pattern_kind="architectural_pattern",
                    rationale=(
                        f"Exemplary uv + Typer + UTF-8/LF CLI discipline observed in `{proj.name}`."
                    ),
                )
                if cand is not None:
                    seen_slugs.add(cand.slug)
                    candidates.append(cand)

        # 2. Extract standalone portable skills from audited projects
        if include_project_skills:
            for proj in report.projects:
                candidates.extend(
                    self._extract_project_skills(proj, target_dir, seen_slugs, skipped)
                )

        return ExtractionReport(
            output_dir=str(target_dir),
            candidates=candidates,
            skipped=skipped,
        )
