"""Stage 5 SemVer & SHA-256 lockfile manager (`skills-lock.json`)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from forge.config import ForgeConfig, estimate_tokens, write_json_utf8_lf


class LockedSkillEntry(BaseModel):
    """Tracked skill metadata and cryptographic SHA-256 hash."""

    name: str
    path: str
    version: str = "0.1.0"
    sha256: str
    source_url: str = "local"
    last_benchmarked: str = ""
    pass_pow_3: float | None = None
    value_score: float | None = None
    token_estimate: int = 0
    status: Literal["active", "canary", "deprecated"] = "active"


class SkillsLockfile(BaseModel):
    """Top-level `skills-lock.json` document."""

    lockfile_version: str = "1.0"
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    skills: dict[str, LockedSkillEntry] = Field(default_factory=dict)


class LockVerifyResult(BaseModel):
    """Verification result comparing disk state against `skills-lock.json`."""

    valid: bool
    mismatches: list[str] = Field(default_factory=list)
    untracked: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


def compute_skill_sha256(skill_dir: Path) -> str:
    """Compute deterministic SHA-256 over all files inside `skill_dir`."""
    h = hashlib.sha256()
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.name == ".DS_Store" or path.suffix == ".pyc":
            continue
        rel = path.relative_to(skill_dir).as_posix()
        h.update(rel.encode())
        h.update(b"\x00")
        h.update(path.read_bytes())
        h.update(b"\x00")
    return h.hexdigest()


def _bump_patch(semver: str) -> str:
    parts = semver.split(".")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
    return "0.1.1"


def _discover_skill_dirs(config: ForgeConfig) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for loc, base in (("active", config.agents_skills_dir), ("library", config.library_skills_dir)):
        if not base.exists():
            continue
        for d in sorted(base.iterdir()):
            if d.is_dir() and (d / "SKILL.md").exists():
                found.append((loc, d))
    return found


class LockfileManager:
    """Generates and verifies `skills-lock.json` integrity."""

    def __init__(self, config: ForgeConfig) -> None:
        self.config = config

    def update(self) -> SkillsLockfile:
        """Scan active & library skills, preserve/bump SemVer, and write `skills-lock.json`."""
        existing = self._load_existing()
        entries: dict[str, LockedSkillEntry] = {}

        for loc, sdir in _discover_skill_dirs(self.config):
            rel_path = sdir.relative_to(self.config.root).as_posix()
            digest = compute_skill_sha256(sdir)
            skill_text = (sdir / "SKILL.md").read_text(encoding="utf-8", errors="replace")
            tok_est = estimate_tokens(skill_text)

            source_url = "local"
            meta_file = sdir / "DISCOVERY_META.json"
            if meta_file.exists():
                try:
                    source_url = str(
                        json.loads(meta_file.read_text("utf-8")).get("source_url", "local")
                    )
                except json.JSONDecodeError:
                    pass

            last_bench = ""
            pass_pow_3: float | None = None
            v_score: float | None = None
            bench_file = sdir / "BENCHMARK.json"
            if bench_file.exists():
                try:
                    bdata = json.loads(bench_file.read_text("utf-8"))
                    last_bench = str(bdata.get("created_at") or "")
                    arm = (bdata.get("arms") or {}).get("underdog") or (
                        bdata.get("arms") or {}
                    ).get("hype")
                    if isinstance(arm, dict):
                        pass_pow_3 = arm.get("pass_pow_k")
                        v_score = arm.get("value_score")
                except json.JSONDecodeError:
                    pass

            prev = existing.skills.get(rel_path)
            version = "0.1.0"
            status: Literal["active", "canary", "deprecated"] = (
                "active" if loc == "active" else "canary"
            )
            if prev is not None:
                version = prev.version if prev.sha256 == digest else _bump_patch(prev.version)
                status = prev.status

            entries[rel_path] = LockedSkillEntry(
                name=sdir.name,
                path=rel_path,
                version=version,
                sha256=digest,
                source_url=source_url,
                last_benchmarked=last_bench,
                pass_pow_3=pass_pow_3,
                value_score=v_score,
                token_estimate=tok_est,
                status=status,
            )

        lockfile = SkillsLockfile(skills=entries)
        write_json_utf8_lf(self.config.lockfile_path, lockfile.model_dump())
        return lockfile

    def verify(self) -> LockVerifyResult:
        """Verify disk skills match `skills-lock.json` without untracked modifications."""
        if not self.config.lockfile_path.exists():
            return LockVerifyResult(valid=False, missing=["skills-lock.json missing"])

        lockfile = self._load_existing()
        disk_dirs = {
            sdir.relative_to(self.config.root).as_posix(): sdir
            for _, sdir in _discover_skill_dirs(self.config)
        }

        mismatches: list[str] = []
        missing: list[str] = []
        untracked: list[str] = []

        for rel_path, entry in lockfile.skills.items():
            sdir = disk_dirs.get(rel_path)
            if sdir is None:
                missing.append(rel_path)
                continue
            current_sha = compute_skill_sha256(sdir)
            if current_sha != entry.sha256:
                mismatches.append(rel_path)

        for rel_path in disk_dirs:
            if rel_path not in lockfile.skills:
                untracked.append(rel_path)

        valid = not mismatches and not missing and not untracked
        return LockVerifyResult(
            valid=valid,
            mismatches=mismatches,
            untracked=untracked,
            missing=missing,
        )

    def _load_existing(self) -> SkillsLockfile:
        if not self.config.lockfile_path.exists():
            return SkillsLockfile()
        try:
            data = json.loads(self.config.lockfile_path.read_text(encoding="utf-8"))
            return SkillsLockfile.model_validate(data)
        except (OSError, json.JSONDecodeError):
            return SkillsLockfile()
