"""Stage 4 Token-Pruning & Section Ablation for winning SKILL.md candidates."""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from forge.config import estimate_tokens, write_text_utf8_lf
from forge.gate.validator import parse_frontmatter


class SectionSlice(BaseModel):
    """Logical Markdown section extracted from SKILL.md."""

    heading: str
    content: str
    token_estimate: int


class PruneProposal(BaseModel):
    """Structured token-pruning proposal and script-offload recommendations."""

    skill_dir: str
    tokens_before: int
    tokens_after: int
    dead_sections: list[str] = Field(default_factory=list)
    script_extraction_candidates: list[str] = Field(default_factory=list)
    pruned_path: str = ""
    diff_preview: str = ""


def _split_sections(body: str) -> list[SectionSlice]:
    sections: list[SectionSlice] = []
    current_heading = "_intro"
    current_lines: list[str] = []

    for line in body.splitlines():
        if re.match(r"^#{1,3}\s+", line):
            if current_lines or current_heading != "_intro":
                text = "\n".join(current_lines).strip()
                sections.append(
                    SectionSlice(
                        heading=current_heading,
                        content=text,
                        token_estimate=estimate_tokens(text),
                    )
                )
            current_heading = line.strip()
            current_lines = []
        else:
            current_lines.append(line)

    tail = "\n".join(current_lines).strip()
    if tail or current_heading != "_intro":
        sections.append(
            SectionSlice(
                heading=current_heading,
                content=tail,
                token_estimate=estimate_tokens(tail),
            )
        )
    return sections


def _vanilla_saturated_keywords(bench_data: dict[str, Any]) -> set[str]:
    """Find task domains where vanilla baseline already scored 100% pass rate."""
    records = bench_data.get("trial_records") or []
    vanilla_by_task: dict[str, list[bool]] = {}
    for rec in records:
        if rec.get("arm") == "vanilla":
            tid = str(rec.get("task_id") or "")
            vanilla_by_task.setdefault(tid, []).append(bool(rec.get("passed")))

    saturated: set[str] = set()
    for tid, outcomes in vanilla_by_task.items():
        if outcomes and all(outcomes):
            for tok in re.findall(r"[a-z0-9]{4,}", tid.lower()):
                if tok not in {"task", "edge", "case"}:
                    saturated.add(tok)
    return saturated


def _has_repeated_shell_sequence(section_text: str) -> bool:
    shell_blocks = re.findall(r"```(?:bash|sh|powershell|pwsh|shell)\n([\s\S]*?)```", section_text)
    for block in shell_blocks:
        cmds = [ln for ln in block.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        if len(cmds) >= 3:
            return True
    return False


def propose_skill_pruning(
    skill_dir: Path,
    benchmark_json_path: Path | None = None,
    write_pruned: bool = True,
) -> PruneProposal:
    """Analyze SKILL.md against BENCHMARK.json and generate `SKILL.pruned.md`."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"Missing SKILL.md in {skill_dir}")

    original = skill_md.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_frontmatter(original)
    lines = original.splitlines()
    fm_block = ""
    if meta and lines and lines[0].strip() == "---":
        end_idx = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), -1)
        if end_idx != -1:
            fm_block = "\n".join(lines[: end_idx + 1])

    bench_path = benchmark_json_path or (skill_dir / "BENCHMARK.json")
    bench_data: dict[str, Any] = {}
    if bench_path.exists():
        bench_data = json.loads(bench_path.read_text(encoding="utf-8"))

    saturated_kw = _vanilla_saturated_keywords(bench_data)
    sections = _split_sections(body)

    kept_parts: list[str] = []
    dead_sections: list[str] = []
    script_candidates: list[str] = []

    for sec in sections:
        heading_lower = sec.heading.lower()
        content_lower = sec.content.lower()
        is_generic_bloat = any(
            phrase in content_lower
            for phrase in (
                "general coding practices",
                "abstract base classes",
                "multi-paragraph docstrings",
            )
        )
        hits_saturated = any(kw in heading_lower for kw in saturated_kw) and (
            sec.token_estimate > 40 or is_generic_bloat
        )

        if sec.heading != "_intro" and (hits_saturated or is_generic_bloat):
            dead_sections.append(sec.heading)
            continue

        updated_content = sec.content
        if _has_repeated_shell_sequence(sec.content):
            script_candidates.append(sec.heading)
            slug = re.sub(r"[^a-z0-9]+", "_", sec.heading.lower()).strip("_") or "run"
            updated_content += (
                f"\n\n> Tip: Extract repeated shell steps into `scripts/{slug}.py --help`.\n"
            )

        if sec.heading == "_intro":
            if updated_content:
                kept_parts.append(updated_content)
        else:
            kept_parts.append(f"{sec.heading}\n\n{updated_content}".strip())

    pruned_body = "\n\n".join(p for p in kept_parts if p).strip()
    pruned_full = f"{fm_block}\n\n{pruned_body}\n" if fm_block else f"{pruned_body}\n"

    pruned_path = skill_dir / "SKILL.pruned.md"
    if write_pruned:
        write_text_utf8_lf(pruned_path, pruned_full)

    diff_lines = list(
        difflib.unified_diff(
            original.splitlines(),
            pruned_full.splitlines(),
            fromfile="SKILL.md",
            tofile="SKILL.pruned.md",
            lineterm="",
        )
    )
    return PruneProposal(
        skill_dir=str(skill_dir),
        tokens_before=estimate_tokens(original),
        tokens_after=estimate_tokens(pruned_full),
        dead_sections=dead_sections,
        script_extraction_candidates=script_candidates,
        pruned_path=str(pruned_path) if write_pruned else "",
        diff_preview="\n".join(diff_lines),
    )
