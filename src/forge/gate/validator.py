"""Stage 2 Spec & Hygiene validator for SKILL.md against agentskills.io & Antigravity rules."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from forge.config import estimate_tokens


class SpecValidationResult(BaseModel):
    """Structured result of SKILL.md specification and hygiene checks."""

    passed: bool
    name: str = ""
    description: str = ""
    line_count: int = 0
    token_estimate: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    external_tools: dict[str, str] = Field(default_factory=dict)


def parse_frontmatter(raw_text: str) -> tuple[dict[str, Any], str]:
    """Split YAML frontmatter from Markdown body if delimited by `---`."""
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
    yaml_block = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :])
    try:
        loaded = yaml.safe_load(yaml_block)
        meta = loaded if isinstance(loaded, dict) else {}
    except yaml.YAMLError:
        meta = {}
    return meta, body


def _run_optional_tool(cmd: str, args: list[str], cwd: Path) -> str | None:
    exe = shutil.which(cmd)
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        out = (proc.stdout or proc.stderr or "").strip()
        return f"exit={proc.returncode}: {out[:300]}"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"error: {exc}"


def validate_skill_spec(skill_dir: Path, run_external: bool = True) -> SpecValidationResult:
    """Validate SKILL.md frontmatter, lengths, token bounds, and optional CLI linters."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return SpecValidationResult(passed=False, errors=["Missing SKILL.md"])

    raw_bytes = skill_md.read_bytes()
    errors: list[str] = []
    warnings: list[str] = []

    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        errors.append("SKILL.md contains UTF-8 BOM (forbidden on Windows/Antigravity)")

    text = raw_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines()
    line_count = len(lines)
    token_est = estimate_tokens(text)

    meta, _body = parse_frontmatter(text)
    if not meta:
        errors.append("Missing or invalid YAML frontmatter block")

    name = str(meta.get("name") or "").strip()
    desc = str(meta.get("description") or "").strip()

    if not name:
        errors.append("Frontmatter missing required `name`")
    elif len(name) > 64:
        errors.append(f"`name` exceeds 64 chars ({len(name)})")

    if not desc:
        errors.append("Frontmatter missing required `description`")
    elif len(desc) > 1024:
        errors.append(f"`description` exceeds 1024 chars ({len(desc)})")
    else:
        lowered = desc.lower()
        if "use when" not in lowered:
            warnings.append("`description` should include `Use when ...` trigger guidance")
        if "do not use" not in lowered:
            warnings.append("`description` should include `Do not use ...` boundary guidance")

    if line_count > 500:
        errors.append(f"SKILL.md exceeds hard 500-line limit ({line_count} lines)")
    elif line_count >= 400:
        warnings.append(f"SKILL.md approaching bloat threshold ({line_count} lines >= 400)")

    if token_est > 5000:
        errors.append(f"SKILL.md exceeds hard 5,000-token limit (~{token_est} tokens)")

    external_tools: dict[str, str] = {}
    if run_external:
        agnix_out = _run_optional_tool("agnix", [str(skill_dir)], skill_dir)
        if agnix_out is not None:
            external_tools["agnix"] = agnix_out
            if agnix_out.startswith("exit=") and not agnix_out.startswith("exit=0:"):
                warnings.append(f"agnix reported issue: {agnix_out}")

        sv_out = _run_optional_tool("skill-validator", ["check", str(skill_dir)], skill_dir)
        if sv_out is not None:
            external_tools["skill-validator"] = sv_out
            if sv_out.startswith("exit=") and not sv_out.startswith("exit=0:"):
                warnings.append(f"skill-validator reported issue: {sv_out}")

    return SpecValidationResult(
        passed=len(errors) == 0,
        name=name,
        description=desc,
        line_count=line_count,
        token_estimate=token_est,
        warnings=warnings,
        errors=errors,
        external_tools=external_tools,
    )
