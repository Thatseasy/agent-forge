"""Stack detection for inferring project tech domains and dependency signals."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

_DEP_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)")


class DetectedSignal(BaseModel):
    """Weighted tech domain inference supported by concrete file/dependency evidence."""

    domain: str
    confidence: float
    evidence: list[str] = Field(default_factory=list)


class StackDetectionResult(BaseModel):
    """Structured tech-stack profile extracted from a project directory."""

    project_dir: str
    project_name: str
    domains: list[str] = Field(default_factory=list)
    signals: list[DetectedSignal] = Field(default_factory=list)
    packages: list[str] = Field(default_factory=list)
    detected_files: list[str] = Field(default_factory=list)
    env_hints: list[str] = Field(default_factory=list)


def _normalize_pkg(raw_spec: str) -> str:
    match = _DEP_NAME_RE.match(raw_spec)
    if not match:
        return ""
    return match.group(1).lower().replace("_", "-")


def _extract_pyproject_deps(data: dict[str, Any]) -> list[str]:
    deps: list[str] = []
    project = data.get("project")
    if isinstance(project, dict):
        for item in project.get("dependencies") or []:
            if isinstance(item, str) and (name := _normalize_pkg(item)):
                deps.append(name)
        opt = project.get("optional-dependencies")
        if isinstance(opt, dict):
            for group in opt.values():
                if isinstance(group, list):
                    for item in group:
                        if isinstance(item, str) and (name := _normalize_pkg(item)):
                            deps.append(name)

    dep_groups = data.get("dependency-groups")
    if isinstance(dep_groups, dict):
        for group in dep_groups.values():
            if isinstance(group, list):
                for item in group:
                    if isinstance(item, str) and (name := _normalize_pkg(item)):
                        deps.append(name)

    tool = data.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict) and isinstance(poetry.get("dependencies"), dict):
            for key in poetry["dependencies"]:
                if isinstance(key, str) and key.lower() != "python":
                    deps.append(key.lower().replace("_", "-"))
    return deps


def _read_env_key_names(env_example: Path) -> list[str]:
    """Extract only non-secret variable names from template files (never values or `.env`)."""
    keys: list[str] = []
    try:
        text = env_example.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return keys
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key.startswith("export "):
            key = key[7:].strip()
        if key and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            keys.append(key)
    return keys


class _SignalAccumulator:
    """Collects confidence scores and unique evidence lines per domain."""

    def __init__(self) -> None:
        self._scores: dict[str, float] = {}
        self._evidence: dict[str, list[str]] = {}

    def add(self, domain: str, boost: float, evidence: str) -> None:
        prev = self._scores.get(domain, 0.0)
        self._scores[domain] = min(1.0, round(prev + boost, 4))
        ev_list = self._evidence.setdefault(domain, [])
        if evidence not in ev_list:
            ev_list.append(evidence)

    def build(self) -> list[DetectedSignal]:
        signals = [
            DetectedSignal(domain=dom, confidence=score, evidence=self._evidence[dom])
            for dom, score in self._scores.items()
        ]
        signals.sort(key=lambda s: (-s.confidence, s.domain))
        return signals


_PYTHON_PKG_DOMAINS: dict[str, tuple[str, float]] = {
    "playwright": ("playwright-qa", 0.55),
    "pytest-playwright": ("playwright-qa", 0.55),
    "supabase": ("supabase", 0.65),
    "notion-client": ("notion", 0.70),
    "openai": ("ai-evals", 0.45),
    "anthropic": ("ai-evals", 0.45),
    "litellm": ("ai-evals", 0.55),
    "google-genai": ("ai-evals", 0.50),
    "pydantic-ai": ("ai-evals", 0.60),
    "langfuse": ("ai-evals", 0.65),
    "ragas": ("ai-evals", 0.70),
    "deepeval": ("ai-evals", 0.70),
    "scikit-learn": ("ai-evals", 0.25),
}

_NODE_PKG_DOMAINS: dict[str, tuple[str, float]] = {
    "next": ("nextjs-react", 0.65),
    "react": ("nextjs-react", 0.35),
    "react-dom": ("nextjs-react", 0.20),
    "@supabase/supabase-js": ("supabase", 0.75),
    "@supabase/ssr": ("supabase", 0.55),
    "supabase": ("supabase", 0.55),
    "@playwright/test": ("playwright-qa", 0.75),
    "playwright": ("playwright-qa", 0.65),
    "playwright-core": ("playwright-qa", 0.55),
    "@notionhq/client": ("notion", 0.80),
    "vercel": ("vercel", 0.65),
    "@vercel/analytics": ("vercel", 0.50),
    "@vercel/speed-insights": ("vercel", 0.45),
    "ai": ("vercel", 0.45),
    "@ai-sdk/openai": ("ai-evals", 0.55),
    "@ai-sdk/anthropic": ("ai-evals", 0.55),
    "openai": ("ai-evals", 0.45),
    "@anthropic-ai/sdk": ("ai-evals", 0.45),
    "promptfoo": ("ai-evals", 0.70),
    "braintrust": ("ai-evals", 0.70),
    "typescript": ("typescript-node", 0.45),
}

_ENV_KEY_DOMAINS: list[tuple[tuple[str, ...], str, float]] = [
    (("SUPABASE_", "NEXT_PUBLIC_SUPABASE_"), "supabase", 0.55),
    (("NOTION_",), "notion", 0.70),
    (("VERCEL_",), "vercel", 0.60),
    (("PLAYWRIGHT_",), "playwright-qa", 0.45),
    (
        (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "OPENROUTER_API_KEY",
            "PERPLEXITY_API_KEY",
            "FORGE_JUDGE_MODEL",
            "AA_API_KEY",
        ),
        "ai-evals",
        0.45,
    ),
]


class StackDetector:
    """Inspects a project directory to infer tech domains and stack evidence."""

    def detect(self, project_dir: Path) -> StackDetectionResult:
        """Analyze manifests, config files, and `.env.example` hints under `project_dir`."""
        resolved = project_dir.resolve()
        acc = _SignalAccumulator()
        packages: list[str] = []
        detected_files: list[str] = []
        env_hints: list[str] = []
        project_name = resolved.name

        if not resolved.exists() or not resolved.is_dir():
            return StackDetectionResult(
                project_dir=str(resolved),
                project_name=project_name,
            )

        project_name = self._inspect_pyproject(
            resolved, acc, packages, detected_files, project_name
        )
        self._inspect_requirements(resolved, acc, packages, detected_files)
        project_name = self._inspect_package_json(
            resolved, acc, packages, detected_files, project_name
        )
        self._inspect_markers(resolved, acc, detected_files)
        self._inspect_env_templates(resolved, acc, detected_files, env_hints)

        signals = acc.build()
        unique_packages = sorted(set(packages))
        return StackDetectionResult(
            project_dir=str(resolved),
            project_name=project_name,
            domains=[s.domain for s in signals],
            signals=signals,
            packages=unique_packages,
            detected_files=sorted(set(detected_files)),
            env_hints=sorted(set(env_hints)),
        )

    def _inspect_pyproject(
        self,
        root: Path,
        acc: _SignalAccumulator,
        packages: list[str],
        detected_files: list[str],
        default_name: str,
    ) -> str:
        pyproject = root / "pyproject.toml"
        uv_lock = root / "uv.lock"
        if uv_lock.is_file():
            detected_files.append("uv.lock")
            acc.add("python-uv", 0.55, "uv.lock")

        if not pyproject.is_file():
            return default_name

        detected_files.append("pyproject.toml")
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8", errors="replace"))
        except tomllib.TOMLDecodeError:
            acc.add("python-uv", 0.35, "pyproject.toml")
            return default_name

        name = default_name
        proj = data.get("project")
        if isinstance(proj, dict) and isinstance(proj.get("name"), str) and proj["name"].strip():
            name = proj["name"].strip()

        tool = data.get("tool")
        if isinstance(tool, dict) and "uv" in tool:
            acc.add("python-uv", 0.45, "pyproject.toml:tool.uv")
        else:
            acc.add("python-uv", 0.40, "pyproject.toml")

        deps = _extract_pyproject_deps(data)
        packages.extend(deps)
        for pkg in deps:
            if pkg in _PYTHON_PKG_DOMAINS:
                domain, boost = _PYTHON_PKG_DOMAINS[pkg]
                acc.add(domain, boost, f"pyproject.toml:{pkg}")
        return name

    def _inspect_requirements(
        self,
        root: Path,
        acc: _SignalAccumulator,
        packages: list[str],
        detected_files: list[str],
    ) -> None:
        req = root / "requirements.txt"
        if not req.is_file():
            return
        detected_files.append("requirements.txt")
        acc.add("python-uv", 0.30, "requirements.txt")
        for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "-")):
                continue
            if pkg := _normalize_pkg(stripped):
                packages.append(pkg)
                if pkg in _PYTHON_PKG_DOMAINS:
                    domain, boost = _PYTHON_PKG_DOMAINS[pkg]
                    acc.add(domain, boost, f"requirements.txt:{pkg}")

    def _inspect_package_json(
        self,
        root: Path,
        acc: _SignalAccumulator,
        packages: list[str],
        detected_files: list[str],
        default_name: str,
    ) -> str:
        pkg_json = root / "package.json"
        if not pkg_json.is_file():
            return default_name
        detected_files.append("package.json")
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            acc.add("typescript-node", 0.30, "package.json")
            return default_name
        if not isinstance(data, dict):
            return default_name

        name = default_name
        if isinstance(data.get("name"), str) and data["name"].strip():
            name = data["name"].strip()

        all_deps: dict[str, Any] = {}
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            sec = data.get(section)
            if isinstance(sec, dict):
                all_deps.update(sec)

        if all_deps:
            acc.add("typescript-node", 0.25, "package.json")

        for pkg in sorted(all_deps):
            lower_pkg = pkg.lower()
            packages.append(lower_pkg)
            if lower_pkg in _NODE_PKG_DOMAINS:
                domain, boost = _NODE_PKG_DOMAINS[lower_pkg]
                acc.add(domain, boost, f"package.json:{pkg}")
            elif lower_pkg.startswith("@ai-sdk/"):
                acc.add("vercel", 0.40, f"package.json:{pkg}")
                acc.add("ai-evals", 0.45, f"package.json:{pkg}")
        return name

    def _inspect_markers(
        self,
        root: Path,
        acc: _SignalAccumulator,
        detected_files: list[str],
    ) -> None:
        single_markers: list[tuple[str, str, float]] = [
            ("tsconfig.json", "typescript-node", 0.45),
            ("vercel.json", "vercel", 0.70),
            ("Dockerfile", "docker-infra", 0.70),
            ("docker-compose.yml", "docker-infra", 0.65),
            ("docker-compose.yaml", "docker-infra", 0.65),
            ("compose.yaml", "docker-infra", 0.65),
            ("promptfooconfig.yaml", "ai-evals", 0.75),
            ("promptfooconfig.yml", "ai-evals", 0.75),
        ]
        for filename, domain, boost in single_markers:
            if (root / filename).is_file():
                detected_files.append(filename)
                acc.add(domain, boost, filename)

        glob_markers: list[tuple[str, str, float]] = [
            ("next.config.*", "nextjs-react", 0.70),
            ("playwright.config.*", "playwright-qa", 0.75),
        ]
        for pattern, domain, boost in glob_markers:
            for match in sorted(root.glob(pattern)):
                if match.is_file():
                    detected_files.append(match.name)
                    acc.add(domain, boost, match.name)

        dir_markers: list[tuple[str, str, float]] = [
            ("supabase", "supabase", 0.80),
            (".vercel", "vercel", 0.65),
            ("evals", "ai-evals", 0.60),
        ]
        for dirname, domain, boost in dir_markers:
            target = root / dirname
            if target.is_dir():
                detected_files.append(f"{dirname}/")
                acc.add(domain, boost, f"{dirname}/")

    def _inspect_env_templates(
        self,
        root: Path,
        acc: _SignalAccumulator,
        detected_files: list[str],
        env_hints: list[str],
    ) -> None:
        for tpl_name in (".env.example", ".env.sample", ".env.template"):
            tpl_path = root / tpl_name
            if not tpl_path.is_file():
                continue
            detected_files.append(tpl_name)
            for key in _read_env_key_names(tpl_path):
                env_hints.append(key)
                upper = key.upper()
                for prefixes, domain, boost in _ENV_KEY_DOMAINS:
                    if any(upper.startswith(p) for p in prefixes):
                        acc.add(domain, boost, f"{tpl_name}:{key}")
