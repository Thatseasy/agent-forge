"""Stage 2 Gate orchestrator combining security, spec validation, normalization, and dedup."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from forge.config import ForgeConfig, write_json_utf8_lf
from forge.gate.dedup import DedupResult, check_semantic_duplicates
from forge.gate.normalizer import NormalizationResult, normalize_skill_dir
from forge.gate.security import SecurityScanResult, scan_skill_security
from forge.gate.validator import SpecValidationResult, validate_skill_spec

GateStatus = Literal["passed", "needs_porting", "failed_spec", "failed_security"]


class GateReport(BaseModel):
    """Consolidated Stage 2 Gate decision written to `GATE_REPORT.json`."""

    skill_slug: str
    skill_dir: str
    evaluated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    status: GateStatus
    security: SecurityScanResult
    spec: SpecValidationResult
    normalization: NormalizationResult
    dedup: DedupResult
    moved_to_archive: str = ""


class GatePipeline:
    """Runs deterministic 0-USD checks on staged or library skills."""

    def __init__(self, config: ForgeConfig) -> None:
        self.config = config

    def evaluate_skill(
        self,
        skill_dir: Path,
        apply_fixes: bool = True,
        move_rejected: bool = True,
        run_external: bool = True,
    ) -> GateReport:
        """Evaluate one skill folder, write GATE_REPORT.json, and quarantine security failures."""
        resolved = skill_dir.resolve()
        security = scan_skill_security(resolved, run_external=run_external)
        spec = validate_skill_spec(resolved, run_external=run_external)
        normalization = normalize_skill_dir(resolved, apply_fixes=apply_fixes)
        dedup = check_semantic_duplicates(resolved, self.config.library_skills_dir)

        if not security.safe:
            status: GateStatus = "failed_security"
        elif not spec.passed:
            status = "failed_spec"
        elif normalization.needs_porting:
            status = "needs_porting"
        else:
            status = "passed"

        report = GateReport(
            skill_slug=resolved.name,
            skill_dir=str(resolved),
            status=status,
            security=security,
            spec=spec,
            normalization=normalization,
            dedup=dedup,
        )

        target_for_report = resolved
        if status == "failed_security" and move_rejected:
            rejected_dir = self.config.archive_dir / "rejected_security" / resolved.name
            if rejected_dir.exists():
                shutil.rmtree(rejected_dir)
            rejected_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(resolved), str(rejected_dir))
            report = report.model_copy(update={"moved_to_archive": str(rejected_dir)})
            target_for_report = rejected_dir

        write_json_utf8_lf(target_for_report / "GATE_REPORT.json", report.model_dump())
        return report

    def evaluate_all(self, apply_fixes: bool = True) -> list[GateReport]:
        """Evaluate every candidate folder in `staging/inbox/`."""
        inbox = self.config.staging_inbox_dir
        if not inbox.exists():
            return []
        reports: list[GateReport] = []
        for candidate in sorted(inbox.iterdir()):
            if candidate.is_dir():
                reports.append(self.evaluate_skill(candidate, apply_fixes=apply_fixes))
        return reports
