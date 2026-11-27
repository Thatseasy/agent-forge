"""Stage 2 normalizer: strips packaging bloat, ports Claude syntax, checks --help."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from forge.config import write_text_utf8_lf

CLUTTER_NAMES = frozenset(
    {
        ".DS_Store",
        "Thumbs.db",
        "LICENSE",
        "LICENSE.md",
        "LICENSE.txt",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
    }
)

CLAUDE_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\$ARGUMENTS\b"), "$ARGUMENTS", "User input arguments"),
    (re.compile(r"Task\s*\("), "Task(...)", "invoke_subagent"),
    (re.compile(r"\.claude/skills/"), ".claude/skills/", ".agents/skills/"),
    (re.compile(r"\.claude/hooks/"), ".claude/hooks/", ".agents/hooks/"),
]


class NormalizationResult(BaseModel):
    """Summary of cleanup, Claude-Code porting detection, and script --help checks."""

    needs_porting: bool = False
    removed_clutter: list[str] = Field(default_factory=list)
    claude_syntax_hits: list[str] = Field(default_factory=list)
    scripts_missing_help: list[str] = Field(default_factory=list)
    ported_patch_written: str = ""


def _script_supports_help(script_path: Path) -> bool:
    text = script_path.read_text(encoding="utf-8", errors="replace")
    lower = text.lower()
    if script_path.suffix.lower() == ".py":
        return any(
            tok in lower for tok in ("argparse", "typer", "click", "--help", "optparse", "sys.argv")
        )
    return "--help" in lower or "-h" in lower or "usage:" in lower


def normalize_skill_dir(skill_dir: Path, apply_fixes: bool = True) -> NormalizationResult:
    """Clean packaging clutter, detect Claude-Code syntax, and check scripts/ for --help."""
    removed: list[str] = []
    for entry in list(skill_dir.rglob("*")):
        if entry.name in CLUTTER_NAMES and entry.exists():
            removed.append(str(entry.relative_to(skill_dir)).replace("\\", "/"))
            if apply_fixes:
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)

    skill_md = skill_dir / "SKILL.md"
    syntax_hits: list[str] = []
    ported_patch = ""
    if skill_md.exists():
        original = skill_md.read_text(encoding="utf-8", errors="replace")
        patched = original
        for pattern, label, replacement in CLAUDE_PATTERNS:
            if pattern.search(original):
                syntax_hits.append(label)
                patched = pattern.sub(replacement, patched)
        if syntax_hits and apply_fixes:
            patch_path = skill_dir / "SKILL.antigravity.md"
            write_text_utf8_lf(patch_path, patched)
            ported_patch = str(patch_path)

    missing_help: list[str] = []
    scripts_dir = skill_dir / "scripts"
    if scripts_dir.is_dir():
        for script in scripts_dir.iterdir():
            if script.is_file() and script.suffix.lower() in {".py", ".sh", ".ps1", ".js"}:
                if not _script_supports_help(script):
                    missing_help.append(script.name)

    return NormalizationResult(
        needs_porting=len(syntax_hits) > 0,
        removed_clutter=sorted(set(removed)),
        claude_syntax_hits=syntax_hits,
        scripts_missing_help=missing_help,
        ported_patch_written=ported_patch,
    )
