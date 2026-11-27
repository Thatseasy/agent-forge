"""Helpers for resolving single-skill folders or multi-module unit skill packs."""

from __future__ import annotations

from pathlib import Path


def resolve_skill_dirs(skill_path: Path) -> list[Path]:
    """Return concrete skill folders containing `SKILL.md` for a skill or pack/unit."""
    if (skill_path / "SKILL.md").is_file():
        return [skill_path]
    skills_sub = skill_path / "skills"
    if (skill_path / "unit.yaml").is_file() and skills_sub.is_dir():
        nested = sorted(
            p for p in skills_sub.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()
        )
        if nested:
            return nested
    if not skill_path.is_dir():
        return []
    return sorted(
        p for p in skill_path.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()
    )


def skill_pack_name(skill_path: Path) -> str:
    """Return canonical display name for a skill folder, pack, or unit."""
    if (
        not (skill_path / "SKILL.md").is_file()
        and skill_path.name == "skills"
        and skill_path.parent.name
    ):
        return skill_path.parent.name
    return skill_path.name
