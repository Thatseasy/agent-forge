"""Read-only project documentation, agent artifact, and tech-stack auditor."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from forge.config import write_json_utf8_lf, write_text_utf8_lf

Classification = Literal["exemplary", "adequate", "under-documented", "empty"]
DocStatus = Literal["healthy", "stub_or_drift", "oversized", "missing"]
Priority = Literal["high", "medium", "low"]

IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".next",
        "dist",
        "build",
        "out",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "qa-artifacts",
        "staging",
        "archive",
    }
)

TEMPLATE_DRIFT_MARKERS: tuple[str, ...] = (
    "create-next-app",
    "bootstrapped with [`create-next-app`]",
    "learn about next.js features and api",
    "todo: write readme",
)


class DocHealthRecord(BaseModel):
    """Health status of one canonical documentation artifact."""

    artifact: str
    present: bool = False
    path: str = ""
    size_bytes: int = 0
    line_count: int = 0
    modified_at: str = ""
    age_days: float | None = None
    status: DocStatus = "missing"
    notes: str = ""


class AgentArtifactSummary(BaseModel):
    """Inventory of AI agent skills, hooks, rules, prompts, and guardrails."""

    agents_skills: list[str] = Field(default_factory=list)
    claude_skills: list[str] = Field(default_factory=list)
    valid_skill_specs: list[str] = Field(default_factory=list)
    has_hooks_json: bool = False
    hooks_valid_schema: bool = False
    hook_scripts: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    prompts: list[str] = Field(default_factory=list)
    library_skills: list[str] = Field(default_factory=list)
    has_pre_commit: bool = False
    has_skills_lock: bool = False
    has_ci_workflows: bool = False
    uses_agents_pointer_in_claude_md: bool = False


class AuditRecommendation(BaseModel):
    """Concrete actionable recommendation for closing documentation or harness gaps."""

    priority: Priority
    category: str
    target_path: str
    action: str


class ProjectAuditReport(BaseModel):
    """Complete read-only audit report for a single project directory."""

    name: str
    path: str
    exists: bool = True
    nested_root: str = ""
    classification: Classification = "under-documented"
    doc_score: int = 0
    tech_stack: list[str] = Field(default_factory=list)
    docs: list[DocHealthRecord] = Field(default_factory=list)
    agent_artifacts: AgentArtifactSummary = Field(default_factory=AgentArtifactSummary)
    supplementary_docs: list[str] = Field(default_factory=list)
    has_env_file: bool = False
    has_env_example: bool = False
    strengths: list[str] = Field(default_factory=list)
    drift_and_gaps: list[str] = Field(default_factory=list)
    recommendations: list[AuditRecommendation] = Field(default_factory=list)


class PortfolioAuditReport(BaseModel):
    """Aggregated portfolio audit across multiple project directories."""

    generated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    project_count: int = 0
    exemplary_projects: list[str] = Field(default_factory=list)
    adequate_projects: list[str] = Field(default_factory=list)
    under_documented_projects: list[str] = Field(default_factory=list)
    empty_projects: list[str] = Field(default_factory=list)
    projects: list[ProjectAuditReport] = Field(default_factory=list)
    cross_portfolio_findings: list[str] = Field(default_factory=list)


def _iter_project_files(root: Path, max_depth: int = 3) -> list[Path]:
    """Walk `root` up to `max_depth` while skipping vendor/build directories (read-only)."""
    collected: list[Path] = []
    if not root.is_dir():
        return collected

    base_parts = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        depth = len(current.parts) - base_parts
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
        if depth >= max_depth:
            dirnames[:] = []
        for fname in sorted(filenames):
            collected.append(current / fname)
    return collected


def _detect_effective_root(project_dir: Path) -> tuple[Path, str]:
    """Handle repositories wrapped in a single matching nested folder."""
    if not project_dir.is_dir():
        return project_dir, ""
    top_entries = [p for p in project_dir.iterdir() if p.name not in IGNORED_DIRS]
    if len(top_entries) == 1 and top_entries[0].is_dir():
        inner = top_entries[0]
        if (
            inner.name == project_dir.name
            or (inner / "package.json").exists()
            or (inner / "pyproject.toml").exists()
        ):
            return inner, inner.name
    return project_dir, ""


def _read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _parse_frontmatter_dict(raw_text: str) -> dict[str, Any]:
    lines = raw_text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}
    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx == -1:
        return {}
    try:
        loaded = yaml.safe_load("\n".join(lines[1:end_idx]))
        return loaded if isinstance(loaded, dict) else {}
    except yaml.YAMLError:
        return {}


def _stat_meta(path: Path, now: datetime) -> tuple[int, str, float | None]:
    try:
        st = path.stat()
    except OSError:
        return 0, "", None
    mtime = datetime.fromtimestamp(st.st_mtime, tz=UTC)
    age_days = round(max(0.0, (now - mtime).total_seconds() / 86400.0), 2)
    return st.st_size, mtime.isoformat(), age_days


def _inspect_single_doc(
    scan_root: Path,
    project_root: Path,
    rel_candidate: str,
    artifact_label: str,
    now: datetime,
) -> DocHealthRecord:
    path = scan_root / rel_candidate
    if not path.is_file():
        return DocHealthRecord(artifact=artifact_label, present=False, status="missing")

    size_bytes, mod_iso, age_days = _stat_meta(path, now)
    text = _read_text_safe(path)
    line_count = len(text.splitlines())
    rel_from_project = str(path.relative_to(project_root)).replace("\\", "/")

    status: DocStatus = "healthy"
    notes = ""
    lowered = text.lower()

    if artifact_label == "README.md":
        if any(marker in lowered for marker in TEMPLATE_DRIFT_MARKERS):
            status = "stub_or_drift"
            notes = "Contains unmodified framework starter template (template drift)."
        elif size_bytes < 80 or line_count < 3:
            status = "stub_or_drift"
            notes = "Minimal stub README; lacks project purpose, setup, and verification steps."
    elif artifact_label == "AGENTS.md":
        if line_count >= 100 or size_bytes >= 24_000:
            status = "oversized"
            notes = f"Exceeds compact routing budget ({line_count} lines / {size_bytes} bytes)."
        elif size_bytes < 40:
            status = "stub_or_drift"
            notes = "Very short AGENTS.md; consider adding project commands and invariants."
    elif size_bytes < 30:
        status = "stub_or_drift"
        notes = "File exists but is nearly empty."

    return DocHealthRecord(
        artifact=artifact_label,
        present=True,
        path=rel_from_project,
        size_bytes=size_bytes,
        line_count=line_count,
        modified_at=mod_iso,
        age_days=age_days,
        status=status,
        notes=notes,
    )


def _inspect_dir_docs(
    scan_root: Path,
    project_root: Path,
    candidates: tuple[str, ...],
    artifact_label: str,
    now: datetime,
) -> DocHealthRecord:
    for rel in candidates:
        folder = scan_root / rel
        if folder.is_dir():
            md_files = sorted(p for p in folder.rglob("*.md") if p.is_file())
            if md_files:
                total_bytes = sum(p.stat().st_size for p in md_files)
                newest = max(md_files, key=lambda p: p.stat().st_mtime)
                _, mod_iso, age_days = _stat_meta(newest, now)
                total_lines = sum(len(_read_text_safe(p).splitlines()) for p in md_files)
                return DocHealthRecord(
                    artifact=artifact_label,
                    present=True,
                    path=str(folder.relative_to(project_root)).replace("\\", "/"),
                    size_bytes=total_bytes,
                    line_count=total_lines,
                    modified_at=mod_iso,
                    age_days=age_days,
                    status="healthy",
                    notes=f"{len(md_files)} markdown record(s).",
                )
    return DocHealthRecord(artifact=artifact_label, present=False, status="missing")


def _detect_tech_stack(scan_root: Path, files: list[Path]) -> list[str]:
    if not files:
        return ["empty"]

    # Exclude test fixtures and evaluation harnesses when inferring the repo's own tech stack.
    repo_files = [
        p
        for p in files
        if not any(
            part in {"fixtures", "evals", "testdata"} for part in p.relative_to(scan_root).parts
        )
    ]
    if not repo_files:
        repo_files = files

    stack: list[str] = []
    rel_names = {str(p.relative_to(scan_root)).replace("\\", "/").lower() for p in repo_files}
    basenames = {p.name.lower() for p in repo_files}

    pkg_deps: set[str] = set()
    for p in repo_files:
        if p.name == "package.json":
            try:
                data = json.loads(_read_text_safe(p))
                if isinstance(data, dict):
                    for key in ("dependencies", "devDependencies"):
                        deps = data.get(key)
                        if isinstance(deps, dict):
                            pkg_deps.update(str(k).lower() for k in deps)
            except json.JSONDecodeError:
                pass

    py_text = ""
    for p in repo_files:
        if p.name == "pyproject.toml":
            py_text += "\n" + _read_text_safe(p).lower()

    if "uv.lock" in basenames or ("pyproject.toml" in basenames and "uv" in py_text):
        stack.append("python-uv")
    elif (
        "pyproject.toml" in basenames
        or "requirements.txt" in basenames
        or any(p.suffix.lower() == ".py" for p in repo_files)
    ):
        stack.append("python")

    if "next" in pkg_deps or any(n.startswith("next.config.") for n in basenames):
        stack.append("nextjs")
    if "react" in pkg_deps:
        stack.append("react")
    if "tsconfig.json" in basenames or "typescript" in pkg_deps:
        stack.append("typescript")
    if "package.json" in basenames and "nodejs" not in stack:
        stack.append("nodejs")
    if (
        "playwright" in pkg_deps
        or "@playwright/test" in pkg_deps
        or "playwright" in py_text
        or any(n.startswith("playwright.config.") for n in basenames)
    ):
        stack.append("playwright")
    if any("supabase" in d for d in pkg_deps) or (scan_root / "supabase").is_dir():
        stack.append("supabase")
    if "dockerfile" in basenames or any(n.endswith(".dockerfile") for n in basenames):
        stack.append("docker")
    if any(n.startswith(".github/workflows/") and n.endswith((".yml", ".yaml")) for n in rel_names):
        stack.append("github-actions")

    if not stack and any(p.suffix.lower() == ".md" for p in files):
        stack.append("markdown-research")

    return stack or ["generic"]


def _inspect_agent_artifacts(scan_root: Path) -> AgentArtifactSummary:
    summary = AgentArtifactSummary()

    def _collect_skills(skills_dir: Path) -> tuple[list[str], list[str]]:
        names: list[str] = []
        valid: list[str] = []
        if not skills_dir.is_dir():
            return names, valid
        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("_"):
                continue
            skill_md = entry / "SKILL.md"
            if skill_md.is_file():
                names.append(entry.name)
                text = _read_text_safe(skill_md)
                meta = _parse_frontmatter_dict(text)
                desc = str(meta.get("description") or "").lower()
                if (
                    meta.get("name")
                    and desc
                    and "use when" in desc
                    and len(text.splitlines()) < 500
                ):
                    valid.append(entry.name)
        return names, valid

    agents_skills, valid_agents = _collect_skills(scan_root / ".agents" / "skills")
    claude_skills, _ = _collect_skills(scan_root / ".claude" / "skills")
    lib_skills, _ = _collect_skills(scan_root / "library" / "skills")
    summary.agents_skills = agents_skills
    summary.claude_skills = claude_skills
    summary.valid_skill_specs = valid_agents
    summary.library_skills = lib_skills

    hooks_json = scan_root / ".agents" / "hooks.json"
    if hooks_json.is_file():
        summary.has_hooks_json = True
        try:
            parsed = json.loads(_read_text_safe(hooks_json))
            if isinstance(parsed, dict) and any(
                isinstance(v, dict)
                and any(k in v for k in ("PreInvocation", "PreToolUse", "PostToolUse", "Stop"))
                for v in parsed.values()
            ):
                summary.hooks_valid_schema = True
        except json.JSONDecodeError:
            summary.hooks_valid_schema = False

    hooks_dir = scan_root / ".agents" / "hooks"
    if hooks_dir.is_dir():
        summary.hook_scripts = sorted(
            p.name for p in hooks_dir.iterdir() if p.is_file() and not p.name.startswith("_")
        )

    rules_dir = scan_root / ".agents" / "rules"
    if rules_dir.is_dir():
        summary.rules = sorted(p.name for p in rules_dir.glob("*.md") if p.is_file())

    for prompt_dir_name in ("prompts", ".agents/prompts", "library/nudges/qa_charters"):
        pdir = scan_root / prompt_dir_name
        if pdir.is_dir():
            summary.prompts.extend(
                sorted(
                    str(p.relative_to(scan_root)).replace("\\", "/")
                    for p in pdir.rglob("*.md")
                    if p.is_file()
                )
            )

    claude_md = scan_root / "CLAUDE.md"
    if claude_md.is_file() and "@AGENTS.md" in _read_text_safe(claude_md):
        summary.uses_agents_pointer_in_claude_md = True

    summary.has_pre_commit = (scan_root / ".pre-commit-config.yaml").is_file()
    summary.has_skills_lock = (scan_root / "skills-lock.json").is_file()
    summary.has_ci_workflows = (scan_root / ".github" / "workflows").is_dir() and any(
        (scan_root / ".github" / "workflows").glob("*.y*ml")
    )
    return summary


def _score_and_classify(
    files: list[Path],
    docs: list[DocHealthRecord],
    agent: AgentArtifactSummary,
    supplementary: list[str],
    nested_root: str,
    has_env_file: bool,
    has_env_example: bool,
) -> tuple[int, Classification, list[str], list[str], list[AuditRecommendation]]:
    if not files:
        return (
            0,
            "empty",
            [],
            ["Directory is empty; no code, docs, or agent artifacts found."],
            [
                AuditRecommendation(
                    priority="medium",
                    category="bootstrap",
                    target_path="README.md",
                    action="Initialize project scaffold or archive empty directory if unused.",
                )
            ],
        )

    by_name = {d.artifact: d for d in docs}
    score = 0
    strengths: list[str] = []
    gaps: list[str] = []
    recs: list[AuditRecommendation] = []

    readme = by_name["README.md"]
    if readme.status == "healthy":
        score += 18
        strengths.append(f"Clear README.md ({readme.line_count} lines) documents project setup.")
    elif readme.status == "stub_or_drift":
        score += 4
        gaps.append(f"README.md drifted or is a stub: {readme.notes}")
        recs.append(
            AuditRecommendation(
                priority="high",
                category="documentation",
                target_path=readme.path or "README.md",
                action=(
                    "Replace starter/stub README.md with project architecture, "
                    "setup, and test commands."
                ),
            )
        )
    else:
        gaps.append("Missing README.md at project root.")
        recs.append(
            AuditRecommendation(
                priority="high",
                category="documentation",
                target_path="README.md",
                action=(
                    "Add concise README.md covering purpose, tech stack, and run/verify commands."
                ),
            )
        )

    agents_doc = by_name["AGENTS.md"]
    gemini_doc = by_name["GEMINI.md"]
    claude_doc = by_name["CLAUDE.md"]

    if agents_doc.present:
        if agents_doc.status == "healthy":
            score += 20
            strengths.append(
                f"Compact AGENTS.md routing map ({agents_doc.line_count} lines, <100-line budget)."
            )
        elif agents_doc.status == "oversized":
            score += 10
            gaps.append(
                f"AGENTS.md is oversized ({agents_doc.line_count} lines); risk of context bloat."
            )
            recs.append(
                AuditRecommendation(
                    priority="medium",
                    category="agent-guidance",
                    target_path=agents_doc.path or "AGENTS.md",
                    action=(
                        "Trim AGENTS.md below 100 lines and move details into "
                        "targeted skills/rules."
                    ),
                )
            )
        else:
            score += 8
            gaps.append("AGENTS.md is a minimal stub.")
    elif gemini_doc.present or claude_doc.present:
        score += 10
        gaps.append("Has GEMINI.md/CLAUDE.md without canonical AGENTS.md single source of truth.")
        recs.append(
            AuditRecommendation(
                priority="medium",
                category="agent-guidance",
                target_path="AGENTS.md",
                action=(
                    "Consolidate instructions into AGENTS.md and point "
                    "CLAUDE.md/GEMINI.md via @AGENTS.md."
                ),
            )
        )
    else:
        gaps.append("Missing AGENTS.md (agents have no canonical orientation or invariants).")
        recs.append(
            AuditRecommendation(
                priority="high",
                category="agent-guidance",
                target_path="AGENTS.md",
                action=(
                    "Create compact AGENTS.md (<100 lines) with entrypoints, rules, "
                    "and verification commands."
                ),
            )
        )

    if agent.uses_agents_pointer_in_claude_md:
        score += 5
        strengths.append("CLAUDE.md delegates to @AGENTS.md (avoids instruction drift).")

    adrs = by_name["docs/decisions/"]
    if adrs.present:
        score += 12
        strengths.append(f"Architecture Decision Records maintained ({adrs.notes}).")
    else:
        gaps.append("Missing docs/decisions/ ADRs (architectural choices are undocumented).")
        recs.append(
            AuditRecommendation(
                priority="medium",
                category="architecture",
                target_path="docs/decisions/ADR-0001.md",
                action=(
                    "Record key stack/architecture decisions as numbered ADRs "
                    "under docs/decisions/."
                ),
            )
        )

    todo_doc = by_name["tasks/todo.md"]
    if todo_doc.present:
        score += 8
        strengths.append("Tracks active plan & checklists in tasks/todo.md.")
    else:
        gaps.append("Missing tasks/todo.md execution checklist.")
        recs.append(
            AuditRecommendation(
                priority="low",
                category="workflow",
                target_path="tasks/todo.md",
                action="Add tasks/todo.md to track multi-step plans and verification results.",
            )
        )

    lessons_doc = by_name["tasks/lessons.md"]
    if lessons_doc.present:
        score += 10
        strengths.append("Captures recurring failure patterns in tasks/lessons.md.")
    else:
        gaps.append("Missing tasks/lessons.md (scar-tissue learnings not persisted).")
        recs.append(
            AuditRecommendation(
                priority="medium",
                category="workflow",
                target_path="tasks/lessons.md",
                action="Create tasks/lessons.md to log bug root causes and preventive guardrails.",
            )
        )

    changelog_doc = by_name["CHANGELOG.md"]
    if changelog_doc.present:
        score += 5
        strengths.append("Maintains CHANGELOG.md / docs/history/ audit trail.")

    if agent.agents_skills:
        score += 10
        strengths.append(f"Active .agents/skills/ ({', '.join(agent.agents_skills)}).")
        if len(agent.valid_skill_specs) < len(agent.agents_skills):
            gaps.append("Some .agents/skills/*/SKILL.md files lack `Use when` trigger guidance.")
    elif agent.claude_skills:
        score += 4
        gaps.append("Legacy .claude/skills/ present; not yet ported to .agents/skills/.")
        recs.append(
            AuditRecommendation(
                priority="medium",
                category="agent-guidance",
                target_path=".agents/skills/",
                action=(
                    "Port .claude/skills/ into .agents/skills/ with Antigravity trigger metadata."
                ),
            )
        )
    else:
        gaps.append("No project-local .agents/skills/ found.")

    if agent.has_hooks_json:
        if agent.hooks_valid_schema:
            score += 8
            strengths.append(
                f"Schema-valid .agents/hooks.json with {len(agent.hook_scripts)} hook script(s)."
            )
        else:
            score += 3
            gaps.append(".agents/hooks.json exists but failed Antigravity suite schema check.")
            recs.append(
                AuditRecommendation(
                    priority="high",
                    category="harness",
                    target_path=".agents/hooks.json",
                    action=(
                        "Normalize .agents/hooks.json to named suite schema "
                        "with PreToolUse/Stop hooks."
                    ),
                )
            )

    if agent.rules:
        score += 6
        strengths.append(f"Scoped .agents/rules/ ({', '.join(agent.rules)}).")

    if agent.has_pre_commit or agent.has_skills_lock or agent.has_ci_workflows:
        score += 4
        guard_items = [
            label
            for flag, label in (
                (agent.has_pre_commit, ".pre-commit-config.yaml"),
                (agent.has_skills_lock, "skills-lock.json"),
                (agent.has_ci_workflows, ".github/workflows/"),
            )
            if flag
        ]
        strengths.append(f"Automated quality/integrity gates: {', '.join(guard_items)}.")

    if supplementary and score < 50:
        score += min(8, 2 * len(supplementary))
        strengths.append(f"Contains {len(supplementary)} supplementary doc/research file(s).")

    if nested_root:
        gaps.append(
            f"Nested repository wrapper detected (`{nested_root}/`); "
            "root lacks top-level manifests."
        )
        recs.append(
            AuditRecommendation(
                priority="low",
                category="structure",
                target_path=nested_root,
                action="Flatten nested wrapper directory or document monorepo root in README.md.",
            )
        )

    if has_env_file and not has_env_example:
        gaps.append(
            ".env present without .env.example template (risk of undocumented config keys)."
        )
        recs.append(
            AuditRecommendation(
                priority="high",
                category="security-config",
                target_path=".env.example",
                action=(
                    "Add .env.example with placeholder variable names "
                    "(never commit real .env secrets)."
                ),
            )
        )

    bounded_score = max(0, min(100, score))
    has_core_harness = (
        agents_doc.present
        and readme.status == "healthy"
        and bool(agent.agents_skills)
        and (agent.has_hooks_json or bool(agent.rules) or adrs.present)
    )
    if bounded_score >= 65 and has_core_harness:
        classification: Classification = "exemplary"
    elif bounded_score >= 45:
        classification = "adequate"
    else:
        classification = "under-documented"

    return bounded_score, classification, strengths, gaps, recs


class ProjectAuditor:
    """Read-only portfolio auditor for documentation health, agent artifacts, and drift."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime.now(UTC)

    def audit_project(self, project_dir: Path) -> ProjectAuditReport:
        """Audit a single project directory strictly read-only."""
        resolved = project_dir.resolve()
        if not resolved.exists() or not resolved.is_dir():
            return ProjectAuditReport(
                name=project_dir.name,
                path=str(resolved),
                exists=False,
                classification="empty",
                drift_and_gaps=["Directory does not exist."],
            )

        scan_root, nested_root = _detect_effective_root(resolved)
        files = _iter_project_files(scan_root, max_depth=3)

        docs = [
            _inspect_single_doc(scan_root, resolved, "README.md", "README.md", self._now),
            _inspect_single_doc(scan_root, resolved, "AGENTS.md", "AGENTS.md", self._now),
            _inspect_single_doc(scan_root, resolved, "GEMINI.md", "GEMINI.md", self._now),
            _inspect_single_doc(scan_root, resolved, "CLAUDE.md", "CLAUDE.md", self._now),
            _inspect_dir_docs(
                scan_root, resolved, ("docs/decisions", "docs/adr"), "docs/decisions/", self._now
            ),
            _inspect_single_doc(scan_root, resolved, "tasks/todo.md", "tasks/todo.md", self._now),
            _inspect_single_doc(
                scan_root, resolved, "tasks/lessons.md", "tasks/lessons.md", self._now
            ),
            _inspect_single_doc(scan_root, resolved, "CHANGELOG.md", "CHANGELOG.md", self._now),
        ]

        supplementary: list[str] = []
        for p in files:
            if p.suffix.lower() != ".md":
                continue
            rel = str(p.relative_to(scan_root)).replace("\\", "/")
            if rel in {
                "README.md",
                "AGENTS.md",
                "GEMINI.md",
                "CLAUDE.md",
                "tasks/todo.md",
                "tasks/lessons.md",
                "CHANGELOG.md",
            }:
                continue
            if rel.startswith((".agents/", ".claude/", "docs/decisions/", "docs/adr/", "library/")):
                continue
            supplementary.append(rel)

        has_env_file = (scan_root / ".env").is_file() or any(p.name == ".env" for p in files)
        has_env_example = (scan_root / ".env.example").is_file() or any(
            p.name == ".env.example" for p in files
        )

        tech_stack = _detect_tech_stack(scan_root, files)
        agent_summary = _inspect_agent_artifacts(scan_root)
        score, classification, strengths, gaps, recs = _score_and_classify(
            files=files,
            docs=docs,
            agent=agent_summary,
            supplementary=supplementary[:20],
            nested_root=nested_root,
            has_env_file=has_env_file,
            has_env_example=has_env_example,
        )

        return ProjectAuditReport(
            name=resolved.name,
            path=str(resolved),
            exists=True,
            nested_root=nested_root,
            classification=classification,
            doc_score=score,
            tech_stack=tech_stack,
            docs=docs,
            agent_artifacts=agent_summary,
            supplementary_docs=supplementary[:20],
            has_env_file=has_env_file,
            has_env_example=has_env_example,
            strengths=strengths,
            drift_and_gaps=gaps,
            recommendations=recs,
        )

    def audit_portfolio(self, project_dirs: list[Path]) -> PortfolioAuditReport:
        """Audit multiple project directories and synthesize cross-portfolio findings."""
        reports = [self.audit_project(p) for p in project_dirs]
        name_counts: dict[str, int] = {}
        for r in reports:
            name_counts[r.name] = name_counts.get(r.name, 0) + 1
        reports = [
            r.model_copy(update={"name": f"{Path(r.path).parent.name}/{r.name}"})
            if name_counts.get(r.name, 0) > 1
            else r
            for r in reports
        ]
        exemplary = [r.name for r in reports if r.classification == "exemplary"]
        adequate = [r.name for r in reports if r.classification == "adequate"]
        under_doc = [r.name for r in reports if r.classification == "under-documented"]
        empty = [r.name for r in reports if r.classification == "empty"]

        findings: list[str] = []
        if exemplary:
            findings.append(
                f"Exemplary project(s) ({', '.join(exemplary)}) pair a compact <100-line "
                "AGENTS.md routing map with modular .agents/skills/, scoped .agents/rules/, "
                "and deterministic hooks/pre-commit guards."
            )
        drifted_readmes = [
            r.name
            for r in reports
            for d in r.docs
            if d.artifact == "README.md" and d.status == "stub_or_drift"
        ]
        if drifted_readmes:
            findings.append(f"README template drift detected in: {', '.join(drifted_readmes)}.")
        missing_adrs = [
            r.name
            for r in reports
            if r.classification != "empty"
            and any(d.artifact == "docs/decisions/" and not d.present for d in r.docs)
        ]
        if missing_adrs:
            findings.append(
                f"Missing docs/decisions/ ADRs in {len(missing_adrs)} active project(s): "
                f"{', '.join(missing_adrs)}."
            )
        missing_lessons = [
            r.name
            for r in reports
            if r.classification != "empty"
            and any(d.artifact == "tasks/lessons.md" and not d.present for d in r.docs)
        ]
        if missing_lessons:
            findings.append(
                f"Missing tasks/lessons.md scar-tissue log in: {', '.join(missing_lessons)}."
            )
        env_without_example = [r.name for r in reports if r.has_env_file and not r.has_env_example]
        if env_without_example:
            findings.append(
                f".env present without .env.example in: {', '.join(env_without_example)}."
            )

        return PortfolioAuditReport(
            generated_at=self._now.isoformat(),
            project_count=len(reports),
            exemplary_projects=exemplary,
            adequate_projects=adequate,
            under_documented_projects=under_doc,
            empty_projects=empty,
            projects=reports,
            cross_portfolio_findings=findings,
        )


def render_archive_audit_markdown(report: PortfolioAuditReport) -> str:
    """Render a concise, actionable Markdown report from a `PortfolioAuditReport`."""
    lines: list[str] = [
        "# Portfolio Archive & Documentation Audit",
        "",
        f"- **Generated at:** `{report.generated_at}`",
        f"- **Projects scanned:** {report.project_count}",
        f"- **Exemplary:** {', '.join(report.exemplary_projects) or 'none'}",
        f"- **Adequate:** {', '.join(report.adequate_projects) or 'none'}",
        f"- **Under-documented:** {', '.join(report.under_documented_projects) or 'none'}",
        f"- **Empty / missing:** {', '.join(report.empty_projects) or 'none'}",
        "",
        "## Portfolio Summary",
        "",
        "| Project | Class | Score | Tech Stack | README | AGENTS.md | ADRs | Lessons | Skills |",
        "| :--- | :--- | ---: | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for proj in report.projects:
        doc_map = {d.artifact: d for d in proj.docs}
        skills_count = len(proj.agent_artifacts.agents_skills) + len(
            proj.agent_artifacts.claude_skills
        )

        readme_st = doc_map["README.md"].status if "README.md" in doc_map else "missing"
        agents_st = doc_map["AGENTS.md"].status if "AGENTS.md" in doc_map else "missing"
        adr_st = doc_map["docs/decisions/"].status if "docs/decisions/" in doc_map else "missing"
        lessons_st = (
            doc_map["tasks/lessons.md"].status if "tasks/lessons.md" in doc_map else "missing"
        )

        lines.append(
            "| "
            f"{proj.name} | {proj.classification} | {proj.doc_score}/100 | "
            f"{', '.join(proj.tech_stack) or 'n/a'} | "
            f"{readme_st} | {agents_st} | {adr_st} | {lessons_st} | "
            f"{skills_count} |"
        )

    lines.extend(["", "## Cross-Portfolio Patterns & Drift", ""])
    for finding in report.cross_portfolio_findings:
        lines.append(f"- {finding}")

    lines.extend(["", "## Per-Project Breakdown", ""])
    for proj in report.projects:
        lines.append(f"### {proj.name} (`{proj.classification}`, {proj.doc_score}/100)")
        lines.append("")
        lines.append(f"- **Path:** `{proj.path}`")
        if proj.nested_root:
            lines.append(f"- **Nested root:** `{proj.nested_root}/`")
        lines.append(f"- **Tech stack:** {', '.join(proj.tech_stack)}")
        if proj.strengths:
            lines.append("- **What works well:**")
            for s in proj.strengths:
                lines.append(f"  - {s}")
        if proj.drift_and_gaps:
            lines.append("- **Drift & gaps:**")
            for g in proj.drift_and_gaps:
                lines.append(f"  - {g}")
        if proj.recommendations:
            lines.append("- **Actionable recommendations:**")
            for rec in proj.recommendations:
                lines.append(
                    f"  - `[{rec.priority.upper()}]` ({rec.category}) `{rec.target_path}`: "
                    f"{rec.action}"
                )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_audit_reports(report: PortfolioAuditReport, out_dir: Path) -> tuple[Path, Path]:
    """Write `ARCHIVE_AUDIT.md` and `ARCHIVE_AUDIT.json` using UTF-8 without BOM and LF."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered_md = render_archive_audit_markdown(report)
    md_path = write_text_utf8_lf(out_dir / "ARCHIVE_AUDIT.md", rendered_md)
    json_path = write_json_utf8_lf(out_dir / "ARCHIVE_AUDIT.json", report.model_dump())
    return md_path, json_path
