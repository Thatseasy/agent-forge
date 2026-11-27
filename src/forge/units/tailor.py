"""Skill tailoring pipeline blending sanitized training data into Stage-2-gated unit skills."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from forge.config import ForgeConfig, write_text_utf8_lf
from forge.gate import GatePipeline
from forge.gate.validator import parse_frontmatter
from forge.sanitize import assert_corpus_sanitized
from forge.units.manager import UnitManager
from forge.units.models import UnitManifest

HEADING_OR_RULE_RE = re.compile(r"^(?:#{1,3}\s+(.+)|[-*]\s+(.{8,140}))$")


class TailorReport(BaseModel):
    """Summary of skill tailoring, Stage-2 gate status, and synthetic eval suite."""

    domain: str
    base_skill_name: str
    derived_skill_slug: str
    skill_path: str
    eval_suite_path: str
    gate_status: str
    corpus_files_count: int
    extracted_signals: list[str] = Field(default_factory=list)
    dry_run: bool = False


def resolve_base_skill(config: ForgeConfig, base_skill: str) -> Path:
    """Resolve base skill from explicit path, repo library/.agents, or user `~/.agents/skills`."""
    cand_path = Path(base_skill)
    if (cand_path / "SKILL.md").is_file():
        return cand_path.resolve()

    slug = base_skill.strip().lower()
    search_roots = (
        config.library_skills_dir,
        config.agents_skills_dir,
        config.root / "staging" / "inbox",
        Path.home() / ".agents" / "skills",
    )
    for root_dir in search_roots:
        target = root_dir / slug
        if (target / "SKILL.md").is_file():
            return target.resolve()
    raise FileNotFoundError(
        f"Base skill {base_skill!r} not found as path or under library/.agents/~/.agents/skills."
    )


def _extract_corpus_signals(corpus_path: Path, files: list[str]) -> list[str]:
    root = corpus_path.parent if corpus_path.is_file() else corpus_path
    signals: list[str] = []
    seen: set[str] = set()

    for rel in files:
        fpath = corpus_path if corpus_path.is_file() else (root / rel)
        text = fpath.read_text(encoding="utf-8", errors="replace")
        ext = fpath.suffix.lower()

        if ext in {".md", ".txt", ".markdown"}:
            for raw_line in text.splitlines():
                m = HEADING_OR_RULE_RE.match(raw_line.strip())
                if m:
                    candidate = (m.group(1) or m.group(2) or "").strip()
                    key = candidate.lower()
                    if candidate and key not in seen:
                        seen.add(key)
                        signals.append(candidate)
        elif ext == ".json":
            try:
                obj = json.loads(text)
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if isinstance(v, str) and 4 <= len(v) <= 120:
                            entry = f"{k}: {v}"
                            if entry.lower() not in seen:
                                seen.add(entry.lower())
                                signals.append(entry)
            except json.JSONDecodeError:
                pass
        elif ext == ".csv":
            header = text.splitlines()[0] if text.splitlines() else ""
            if header:
                entry = f"CSV schema columns: {header[:110]}"
                if entry.lower() not in seen:
                    seen.add(entry.lower())
                    signals.append(entry)
        if len(signals) >= 12:
            break

    return signals[:12]


def _render_tailored_skill(
    domain: str,
    base_meta: dict[str, object],
    base_body: str,
    signals: list[str],
    compliance_notes: list[str],
    llm_refiner: Callable[[str], str] | None = None,
) -> tuple[str, str]:
    base_name = str(base_meta.get("name") or "base-skill").strip().lower()
    derived_slug = f"{domain}-{base_name}"[:64].strip("-")
    base_desc = str(base_meta.get("description") or f"{base_name} workflow").strip()
    tailored_desc = (
        f"Tailored {domain} specialist extending {base_name} with verified domain playbooks "
        f"and guardrails. {base_desc}"
    )[:500]

    signal_bullets = "\n".join(f"- {s}" for s in signals[:8]) or (
        f"- Apply structured {domain} playbook guidelines cleanly."
    )
    compliance_block = "\n".join(f"- {c}" for c in compliance_notes[:4]) or (
        "- Never emit raw PII or unverified claims outside sanitized artifacts."
    )
    compact_base = "\n".join(base_body.strip().splitlines()[:60])

    prompt_context = (
        f"Domain: {domain}\nBase Skill: {base_name}\nSignals:\n{signal_bullets}\n\n"
        f"Base Instructions:\n{compact_base}"
    )
    if llm_refiner is not None:
        refined_body = llm_refiner(prompt_context).strip()
        if refined_body and not refined_body.startswith("---"):
            rendered = (
                f"---\nname: {derived_slug}\ndescription: {json.dumps(tailored_desc)}\n---\n\n"
                f"{refined_body}\n"
            )
            return derived_slug, rendered

    rendered = (
        f"---\n"
        f"name: {derived_slug}\n"
        f"description: {json.dumps(tailored_desc)}\n"
        f"---\n\n"
        f"# {derived_slug}\n\n"
        f"## Core Base Workflow ({base_name})\n\n"
        f"{compact_base}\n\n"
        f"## {domain} Sanitized Playbook Signals\n\n"
        f"{signal_bullets}\n\n"
        f"## Compliance & Redlines\n\n"
        f"{compliance_block}\n"
    )
    return derived_slug, rendered


def _build_synthetic_suite_yaml(domain: str, derived_slug: str, signals: list[str]) -> str:
    top = signals[:3] or [f"{domain} baseline scenario"]
    tasks = []
    for idx, sig in enumerate(top, start=1):
        tasks.append(
            {
                "id": f"task-{idx}-{domain}",
                "prompt": (
                    f"Apply the {derived_slug} domain workflow to handle: {sig}. "
                    "Keep responses compliant, structured, and free of placeholders."
                ),
                "verifier_cmd": "pytest -q",
                "edge_case": idx > 1,
            }
        )
    suite = {
        "suite_id": f"{derived_slug}-eval",
        "domain": domain,
        "description": f"Synthetic Stage 3 evaluation suite for {derived_slug}.",
        "fixture_dir": "fixture",
        "tasks": tasks,
        "near_miss_prompts": [
            "Write a Kubernetes Helm chart for Redis Sentinel failover.",
            "Debug a Rust async Tokio lifetime compiler error.",
            "Refactor CSS grid breakpoints on a static landing page.",
        ],
        "soft_rubric": [
            f"Applies {domain} domain guardrails and terminology accurately.",
            "Preserves concise structured output without speculative fabrications.",
            "Respects sanitized compliance constraints.",
        ],
    }
    return yaml.safe_dump(suite, sort_keys=False, allow_unicode=True)


def tailor_unit_skill(
    config: ForgeConfig,
    *,
    domain: str,
    base_skill: str,
    corpus_path: Path,
    dry_run: bool = False,
    llm_refiner: Callable[[str], str] | None = None,
) -> TailorReport:
    """Blend a base skill with a strictly sanitized corpus into `library/units/<domain>/`."""
    # Mandatory `#8` guard: reject any raw PII/secret corpus before touching prompts or output
    verified_files = assert_corpus_sanitized(corpus_path, config)
    base_dir = resolve_base_skill(config, base_skill)

    raw_base = (base_dir / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    base_meta, base_body = parse_frontmatter(raw_base)

    mgr = UnitManager(config)
    udir = mgr.unit_dir(domain)
    if not udir.exists() and not dry_run:
        mgr.init_unit(domain)

    manifest = (
        UnitManifest.load(udir)
        if (udir / "unit.yaml").is_file()
        else UnitManifest(domain=udir.name)
    )
    signals = _extract_corpus_signals(corpus_path, verified_files)
    derived_slug, skill_markdown = _render_tailored_skill(
        udir.name,
        base_meta,
        base_body,
        signals,
        manifest.compliance_notes,
        llm_refiner=llm_refiner,
    )

    dest_skill_dir = udir / "skills" / derived_slug
    dest_skill_md = dest_skill_dir / "SKILL.md"
    eval_suite_dir = udir / "evals" / f"{derived_slug}-eval"
    eval_suite_yaml = eval_suite_dir / "suite.yaml"

    gate_status = "dry_run"
    if not dry_run:
        write_text_utf8_lf(dest_skill_md, skill_markdown)
        gate_rep = GatePipeline(config).evaluate_skill(dest_skill_dir, apply_fixes=True)
        gate_status = gate_rep.status
        if gate_status in {"failed_security", "failed_spec"}:
            raise ValueError(
                f"Tailored skill `{derived_slug}` failed Stage 2 gate ({gate_status}): "
                f"{gate_rep.spec.warnings}"
            )

        write_text_utf8_lf(
            eval_suite_yaml,
            _build_synthetic_suite_yaml(udir.name, derived_slug, signals),
        )
        fixture_dir = eval_suite_dir / "fixture"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        readme = fixture_dir / "README.md"
        if not readme.exists():
            write_text_utf8_lf(readme, f"# Fixture for {derived_slug}\n")

        if base_dir.name not in manifest.base_skills:
            manifest.base_skills.append(base_dir.name)
        if derived_slug not in manifest.skills:
            manifest.skills.append(derived_slug)
        if eval_suite_dir.name not in manifest.eval_suites:
            manifest.eval_suites.append(eval_suite_dir.name)
        manifest.sanitized_verified = True
        manifest.save(udir)

    return TailorReport(
        domain=udir.name,
        base_skill_name=base_dir.name,
        derived_skill_slug=derived_slug,
        skill_path=str(dest_skill_md),
        eval_suite_path=str(eval_suite_yaml),
        gate_status=gate_status,
        corpus_files_count=len(verified_files),
        extracted_signals=signals,
        dry_run=dry_run,
    )
