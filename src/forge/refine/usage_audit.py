"""Stage 4 telemetry-driven skill usage auditor and safe deprecation archiver."""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from forge.config import ForgeConfig, estimate_tokens, write_json_utf8_lf, write_text_utf8_lf
from forge.gate.dedup import check_semantic_duplicates


class SkillUsageRecord(BaseModel):
    """Usage, token load, overlap, and pruning status for a single skill."""

    skill_name: str
    skill_path: str
    location: str
    activations_in_window: int
    line_count: int
    token_estimate: int
    overlap_max: float
    flagged_reasons: list[str] = Field(default_factory=list)
    archived_to: str = ""


class UsageAuditReport(BaseModel):
    """Consolidated report emitted by `forge.py prune`."""

    window_days: int
    audited_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    total_skills: int
    flagged_count: int
    records: list[SkillUsageRecord] = Field(default_factory=list)


def _count_activations(
    metrics_path: Path,
    transcript_paths: list[Path],
    cutoff: datetime,
) -> dict[str, int]:
    counts: dict[str, int] = {}

    if metrics_path.exists():
        for ln in metrics_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not ln.strip():
                continue
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            ts_str = str(rec.get("timestamp") or "")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                    if ts < cutoff:
                        continue
                except ValueError:
                    pass
            skill_loaded = rec.get("skill_loaded")
            if isinstance(skill_loaded, str) and skill_loaded:
                name = Path(skill_loaded).parent.name.lower()
                counts[name] = counts.get(name, 0) + 1

    for tx in transcript_paths:
        if not tx.exists():
            continue
        text = tx.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"skills[\\/]([a-z0-9_-]+)[\\/]SKILL\.md", text, re.I):
            name = m.group(1).lower()
            counts[name] = counts.get(name, 0) + 1

    return counts


def audit_and_prune_skills(
    config: ForgeConfig,
    days: int = 30,
    apply_archive: bool = False,
    dry_run: bool = False,
    transcript_paths: list[Path] | None = None,
) -> UsageAuditReport:
    """Flag unused/bloated/overlapping skills; archive confirmed library skills outside .agents/."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    metrics_path = config.state_dir / "metrics.jsonl"
    counts = _count_activations(metrics_path, transcript_paths or [], cutoff)

    roots: list[tuple[str, Path]] = [
        ("library", config.library_skills_dir),
        ("active", config.agents_skills_dir),
    ]
    records: list[SkillUsageRecord] = []
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    for loc_label, base_dir in roots:
        if not base_dir.exists():
            continue
        for sdir in sorted(base_dir.iterdir()):
            skill_md = sdir / "SKILL.md"
            if not sdir.is_dir() or not skill_md.exists():
                continue
            text = skill_md.read_text(encoding="utf-8", errors="replace")
            line_count = len(text.splitlines())
            tok_est = estimate_tokens(text)
            activations = counts.get(sdir.name.lower(), 0)
            dedup = check_semantic_duplicates(sdir, config.library_skills_dir, threshold=0.50)

            reasons: list[str] = []
            if activations == 0:
                reasons.append(f"0 activations in {days} days")
            if line_count > 500:
                reasons.append(f"line_count {line_count} > 500")
            if dedup.duplicate_flagged:
                reasons.append(f"overlap {dedup.max_similarity:.2f} >= 0.50")

            archived_to = ""
            if apply_archive and not dry_run and reasons and loc_label == "library":
                dest = config.archive_dir / "deprecated" / f"{today}-{sdir.name}"
                if dest.exists():
                    shutil.rmtree(dest)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(sdir), str(dest))
                write_text_utf8_lf(
                    dest / "DEPRECATION_REASON.md",
                    (
                        f"# Deprecated: {sdir.name}\n\n"
                        f"- Date: {today}\n"
                        f"- Reasons: {', '.join(reasons)}\n"
                    ),
                )
                archived_to = str(dest)

            records.append(
                SkillUsageRecord(
                    skill_name=sdir.name,
                    skill_path=str(sdir),
                    location=loc_label,
                    activations_in_window=activations,
                    line_count=line_count,
                    token_estimate=tok_est,
                    overlap_max=dedup.max_similarity,
                    flagged_reasons=reasons,
                    archived_to=archived_to,
                )
            )

    report = UsageAuditReport(
        window_days=days,
        total_skills=len(records),
        flagged_count=sum(1 for r in records if r.flagged_reasons),
        records=records,
    )
    write_json_utf8_lf(config.root / "staging" / "usage_audit_report.json", report.model_dump())
    return report
