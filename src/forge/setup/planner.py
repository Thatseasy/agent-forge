"""Project maturity assessor and documentation/harness planner for `forge setup`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

MaturityTier = Literal["prototype_3h_chat", "active_product", "mature_repo"]

_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".next",
        "dist",
        "build",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "staging",
        "archive",
    }
)
_SOURCE_EXTS = frozenset({".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"})


class ProjectSignals(BaseModel):
    """Deterministic file and manifest signals observed inside a target repository."""

    has_package_json: bool = False
    has_pyproject_toml: bool = False
    has_tsconfig: bool = False
    has_readme: bool = False
    has_agents_md: bool = False
    has_adr: bool = False
    has_tasks_todo: bool = False
    has_tasks_lessons: bool = False
    has_hooks_json: bool = False
    has_skills: bool = False
    has_precommit: bool = False
    has_ci: bool = False
    has_tests: bool = False
    has_env_example: bool = False
    source_file_count: int = 0


class ProjectAssessment(BaseModel):
    """Maturity classification and setup plan for a target workspace."""

    target_dir: str
    project_name: str
    maturity: MaturityTier
    detected_stack: list[str] = Field(default_factory=list)
    recommended_profile: str = "superarchivist-boilerplate"
    signals: ProjectSignals
    existing_docs: list[str] = Field(default_factory=list)
    missing_artifacts: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


def _iter_project_files(root: Path) -> list[Path]:
    files: list[Path] = []
    if not root.exists():
        return files
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in _IGNORED_DIRS for part in rel_parts):
            continue
        files.append(path)
    return files


def _detect_stack_from_files(root: Path, files: list[Path]) -> list[str]:
    stack: list[str] = []

    def _add(tag: str) -> None:
        if tag not in stack:
            stack.append(tag)

    pkg_path = root / "package.json"
    if pkg_path.exists():
        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            pkg = {}
        deps: dict[str, object] = {}
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            val = pkg.get(key)
            if isinstance(val, dict):
                deps.update(val)
        dep_names = {str(k).lower() for k in deps}
        if "next" in dep_names or "react" in dep_names:
            _add("nextjs-react")
        if any("supabase" in name for name in dep_names):
            _add("supabase")
        if "@playwright/test" in dep_names or "playwright" in dep_names:
            _add("playwright-qa")
        if "typescript" in dep_names or (root / "tsconfig.json").exists():
            _add("typescript")
        if not stack:
            _add("nodejs")

    pyproject_path = root / "pyproject.toml"
    if pyproject_path.exists():
        text = pyproject_path.read_text(encoding="utf-8", errors="replace").lower()
        if (root / "uv.lock").exists() or "uv" in text or "[dependency-groups]" in text:
            _add("python-uv")
        else:
            _add("python")
        if "playwright" in text:
            _add("playwright-qa")
        if "litellm" in text or "openai" in text or "google-genai" in text:
            _add("ai-evals")

    if (root / "supabase").is_dir():
        _add("supabase")
    if any(p.name.startswith("next.config.") for p in files):
        _add("nextjs-react")
    if any(p.name.startswith("playwright.config.") for p in files):
        _add("playwright-qa")
    if (root / "Dockerfile").exists():
        _add("docker")

    return stack


class ProjectAssessor:
    """Read-only inspector that classifies project maturity and required harness artifacts."""

    def assess(self, target_dir: Path, profile_override: str = "") -> ProjectAssessment:
        """Inspect `target_dir` and return its maturity tier, stack, and missing artifacts."""
        resolved = target_dir.resolve()
        files = _iter_project_files(resolved)
        rel_paths = {p.relative_to(resolved).as_posix() for p in files}

        has_agents_md = any(
            (resolved / name).is_file() for name in ("AGENTS.md", "GEMINI.md", "CLAUDE.md")
        )
        decisions_dir = resolved / "docs" / "decisions"
        has_adr = decisions_dir.is_dir() and any(decisions_dir.glob("*.md"))
        skills_dir = resolved / ".agents" / "skills"
        has_skills = skills_dir.is_dir() and any(skills_dir.glob("*/SKILL.md"))
        workflows_dir = resolved / ".github" / "workflows"
        has_ci = workflows_dir.is_dir() and any(
            p.suffix.lower() in {".yml", ".yaml"} for p in workflows_dir.iterdir() if p.is_file()
        )
        has_tests = (resolved / "tests").is_dir() or any(
            ".spec." in p.name or ".test." in p.name or p.name.startswith("test_") for p in files
        )
        source_count = sum(1 for p in files if p.suffix.lower() in _SOURCE_EXTS)

        readme_path = resolved / "README.md"
        has_readme = readme_path.is_file() and readme_path.stat().st_size > 20

        signals = ProjectSignals(
            has_package_json=(resolved / "package.json").is_file(),
            has_pyproject_toml=(resolved / "pyproject.toml").is_file(),
            has_tsconfig=(resolved / "tsconfig.json").is_file(),
            has_readme=has_readme,
            has_agents_md=has_agents_md,
            has_adr=has_adr,
            has_tasks_todo=(resolved / "tasks" / "todo.md").is_file(),
            has_tasks_lessons=(resolved / "tasks" / "lessons.md").is_file(),
            has_hooks_json=(resolved / ".agents" / "hooks.json").is_file(),
            has_skills=has_skills,
            has_precommit=(resolved / ".pre-commit-config.yaml").is_file(),
            has_ci=has_ci,
            has_tests=has_tests,
            has_env_example=(resolved / ".env.example").is_file(),
            source_file_count=source_count,
        )

        maturity = self._classify_maturity(signals)
        detected_stack = _detect_stack_from_files(resolved, files)

        doc_candidates: tuple[str, ...] = (
            "README.md",
            "AGENTS.md",
            "GEMINI.md",
            "CLAUDE.md",
            "tasks/todo.md",
            "tasks/lessons.md",
            ".pre-commit-config.yaml",
            "mcp_config.recommended.json",
        )
        existing_docs: list[str] = sorted(p for p in doc_candidates if p in rel_paths)
        if has_adr:
            existing_docs.extend(
                sorted(
                    p.relative_to(resolved).as_posix()
                    for p in decisions_dir.glob("*.md")
                    if p.is_file()
                )
            )

        missing: list[str] = []
        if not signals.has_agents_md:
            missing.append("AGENTS.md")
        if not signals.has_adr:
            missing.append("docs/decisions/ADR-0001.md")
        if not signals.has_tasks_todo:
            missing.append("tasks/todo.md")
        if not signals.has_tasks_lessons:
            missing.append("tasks/lessons.md")
        if not signals.has_precommit:
            missing.append(".pre-commit-config.yaml")
        if "mcp_config.recommended.json" not in rel_paths:
            missing.append("mcp_config.recommended.json")
        if not signals.has_hooks_json:
            missing.append(".agents/hooks.json")

        recommendations = self._build_recommendations(maturity, signals, detected_stack)
        profile = profile_override.strip() or "superarchivist-boilerplate"

        return ProjectAssessment(
            target_dir=str(resolved),
            project_name=resolved.name,
            maturity=maturity,
            detected_stack=detected_stack,
            recommended_profile=profile,
            signals=signals,
            existing_docs=existing_docs,
            missing_artifacts=missing,
            recommendations=recommendations,
        )

    @staticmethod
    def _classify_maturity(signals: ProjectSignals) -> MaturityTier:
        doc_count = sum(
            [
                signals.has_readme,
                signals.has_agents_md,
                signals.has_adr,
                signals.has_tasks_todo,
                signals.has_tasks_lessons,
            ]
        )
        harness_count = sum(
            [
                signals.has_hooks_json,
                signals.has_skills,
                signals.has_precommit,
                signals.has_ci,
            ]
        )
        if (
            not signals.has_agents_md
            and not signals.has_adr
            and not signals.has_hooks_json
            and not signals.has_tasks_todo
            and not signals.has_tasks_lessons
            and not signals.has_ci
            and not signals.has_tests
            and signals.source_file_count <= 15
        ):
            return "prototype_3h_chat"
        if (
            signals.has_agents_md
            and signals.has_adr
            and signals.has_tests
            and (harness_count >= 2 or doc_count >= 4)
        ):
            return "mature_repo"
        return "active_product"

    @staticmethod
    def _build_recommendations(
        maturity: MaturityTier,
        signals: ProjectSignals,
        stack: list[str],
    ) -> list[str]:
        recs: list[str] = []
        if maturity == "prototype_3h_chat":
            recs.append(
                "Prototype detected: bootstrap AGENTS.md, ADR-0001, tasks/, and Antigravity hooks."
            )
        elif maturity == "active_product":
            recs.append(
                "Active product: fill missing harness/doc gaps while preserving existing docs."
            )
        else:
            recs.append(
                "Mature repository: preserve existing ADRs/docs; sync missing rules/sources."
            )

        if not signals.has_tests:
            if "playwright-qa" in stack or "nextjs-react" in stack:
                recs.append(
                    "Run live-first exploratory QA (`forge qa`) before authoring *.spec.ts."
                )
            else:
                recs.append("Add automated smoke tests before introducing new features.")
        if not signals.has_env_example and "supabase" in stack:
            recs.append("Document required NEXT_PUBLIC_SUPABASE_* keys in .env.example.")
        return recs
