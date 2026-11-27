"""Pydantic manifest models for canonical `library/units/<domain>/` specialist units."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from forge.config import write_text_utf8_lf


class UnitManifest(BaseModel):
    """Canonical schema stored in `library/units/<domain>/unit.yaml`."""

    domain: str
    version: str = "0.1.0"
    description: str = ""
    profile: str = "superarchivist-boilerplate"
    base_skills: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    corpus_dir: str = "corpus"
    eval_suites: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    sanitized_verified: bool = False
    compliance_notes: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, unit_dir: Path) -> UnitManifest:
        manifest_path = unit_dir / "unit.yaml"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Missing unit.yaml at {manifest_path}")
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(raw)

    def save(self, unit_dir: Path) -> Path:
        manifest_path = unit_dir / "unit.yaml"
        rendered = yaml.safe_dump(self.model_dump(), sort_keys=False, allow_unicode=True)
        return write_text_utf8_lf(manifest_path, rendered)
