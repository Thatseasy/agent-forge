"""Stage 4 On-Demand Retro & Anti-Context-Collapse Nudge Engine."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from forge.config import ForgeConfig, write_text_utf8_lf

NudgeTarget = Literal[
    "agents_md",
    "skill_update",
    "new_hook",
    "new_linter",
    "delete_stale_rule",
]
MAX_NUDGES_PER_RUN = 10


class NudgeProposal(BaseModel):
    """Single incremental, canonically routed improvement nudge."""

    id: str
    slug: str
    mode: str
    target: NudgeTarget
    title: str
    rationale: str
    proposed_change: str
    backlog_file: str = ""
    applied: bool = False


class RetroEngine:
    """Extracts capped incremental nudges across session, outcome, and audit modes."""

    def __init__(self, config: ForgeConfig) -> None:
        self.config = config

    def run(
        self,
        mode: str = "session",
        transcript_path: Path | None = None,
        dry_run: bool = False,
    ) -> list[NudgeProposal]:
        """Run retro analysis (`session|outcome|audit`), capping output at 10 nudges."""
        normalized_mode = mode.lower().strip()
        if normalized_mode == "session":
            raw_nudges = self._retro_session(transcript_path, dry_run=dry_run)
        elif normalized_mode == "outcome":
            raw_nudges = self._retro_outcome(dry_run=dry_run)
        elif normalized_mode == "audit":
            raw_nudges = self._retro_audit()
        else:
            raise ValueError(f"Unsupported retro mode: {mode!r}")

        capped = raw_nudges[:MAX_NUDGES_PER_RUN]
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        backlog_dir = self.config.nudges_dir / "backlog"
        backlog_dir.mkdir(parents=True, exist_ok=True)

        saved: list[NudgeProposal] = []
        for nudge in capped:
            fname = f"{today}-{nudge.slug}.md"
            out_path = backlog_dir / fname
            content = (
                "---\n"
                f"id: {nudge.id}\n"
                f"mode: {nudge.mode}\n"
                f"target: {nudge.target}\n"
                f"applied: false\n"
                "---\n\n"
                f"# {nudge.title}\n\n"
                f"**Target:** `{nudge.target}`\n\n"
                f"## Rationale\n{nudge.rationale}\n\n"
                f"## Proposed Incremental Change\n{nudge.proposed_change}\n"
            )
            write_text_utf8_lf(out_path, content)
            saved.append(nudge.model_copy(update={"backlog_file": str(out_path)}))
        return saved

    def apply_nudge(self, nudge_id: str) -> Path:
        """Apply a backlog nudge by ID or filename stem and mark it applied."""
        backlog_dir = self.config.nudges_dir / "backlog"
        if not backlog_dir.exists():
            raise FileNotFoundError(f"No nudge backlog found at {backlog_dir}")

        candidates = sorted(backlog_dir.glob("*.md"))
        match: Path | None = None
        for p in candidates:
            text = p.read_text(encoding="utf-8", errors="replace")
            if f"id: {nudge_id}" in text or nudge_id in p.stem:
                match = p
                break
        if match is None:
            raise FileNotFoundError(f"Nudge `{nudge_id}` not found in {backlog_dir}")

        text = match.read_text(encoding="utf-8", errors="replace")
        updated = text.replace("applied: false", "applied: true", 1)
        if "## Applied" not in updated:
            updated += f"\n## Applied\n- Applied at {datetime.now(UTC).isoformat()}\n"
        write_text_utf8_lf(match, updated)
        return match

    def _retro_session(self, transcript_path: Path | None, dry_run: bool) -> list[NudgeProposal]:
        nudges: list[NudgeProposal] = []
        metrics_path = self.config.state_dir / "metrics.jsonl"
        events = self._load_session_events(transcript_path, metrics_path, dry_run=dry_run)

        error_counts: dict[str, int] = {}
        correction_hits: list[str] = []
        for ev in events:
            err = str(ev.get("error") or "").strip()
            if err:
                error_counts[err] = error_counts.get(err, 0) + 1
            text = str(ev.get("content") or "")
            if ev.get("type") == "USER_INPUT" and re.search(
                r"(?i)\b(never|don't|stop|always|wrong|nicht|immer|falsch)\b", text
            ):
                correction_hits.append(text.strip()[:160])

        for idx, (err, count) in enumerate(sorted(error_counts.items()), start=1):
            target: NudgeTarget = "new_linter" if "lint" in err.lower() else "new_hook"
            nudges.append(
                NudgeProposal(
                    id=f"session-err-{idx}",
                    slug=self._slug(f"guard-{err[:28]}"),
                    mode="session",
                    target=target,
                    title=f"Prevent recurring tool failure ({count}x)",
                    rationale=f"Observed `{err}` {count} time(s) in session telemetry.",
                    proposed_change=f"Add targeted pre-check or lint rule for: `{err}`.",
                )
            )

        for idx, corr in enumerate(correction_hits, start=1):
            target = "skill_update" if "skill" in corr.lower() else "agents_md"
            nudges.append(
                NudgeProposal(
                    id=f"session-corr-{idx}",
                    slug=self._slug(f"correction-{idx}-{corr[:22]}"),
                    mode="session",
                    target=target,
                    title=f"Capture user correction #{idx}",
                    rationale=f"Repeated user steer detected: `{corr}`",
                    proposed_change=f"Add one-line Scar-Tissue rule for: {corr}",
                )
            )

        if not nudges and dry_run:
            nudges.append(
                NudgeProposal(
                    id="session-dry-1",
                    slug="enforce-utf8-no-bom",
                    mode="session",
                    target="new_linter",
                    title="Enforce UTF-8 without BOM in pre-commit",
                    rationale="Mock session transcript recorded PowerShell BOM warning.",
                    proposed_change="Verify UTF-8 without BOM across generated JSON/MD outputs.",
                )
            )
        return nudges

    def _load_session_events(
        self,
        transcript_path: Path | None,
        metrics_path: Path,
        dry_run: bool,
    ) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        if metrics_path.exists():
            for ln in metrics_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if ln.strip():
                    try:
                        events.append(json.loads(ln))
                    except json.JSONDecodeError:
                        continue

        target_tx = transcript_path
        if target_tx is None and not dry_run:
            brain_root = Path.home() / ".gemini" / "antigravity" / "brain"
            if brain_root.exists():
                found = sorted(
                    brain_root.glob("*/.system_generated/logs/transcript.jsonl"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                target_tx = found[0] if found else None

        if target_tx and target_tx.exists():
            for ln in target_tx.read_text(encoding="utf-8", errors="replace").splitlines():
                if ln.strip():
                    try:
                        events.append(json.loads(ln))
                    except json.JSONDecodeError:
                        continue
        elif dry_run and not events:
            events.extend(
                [
                    {
                        "type": "USER_INPUT",
                        "content": "Always write JSON without BOM and use uv run for Python.",
                    },
                    {"tool": "run_command", "error": "ruff E501 line too long"},
                    {"tool": "run_command", "error": "ruff E501 line too long"},
                ]
            )
        return events

    def _retro_outcome(self, dry_run: bool) -> list[NudgeProposal]:
        nudges: list[NudgeProposal] = []
        try:
            proc = subprocess.run(
                ["git", "log", "-n", "25", "--pretty=format:%h|%s"],
                cwd=self.config.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            lines = proc.stdout.splitlines() if proc.returncode == 0 else []
        except (OSError, subprocess.SubprocessError):
            lines = []

        for idx, line in enumerate(lines, start=1):
            if re.search(r"(?i)\b(revert|fixup|hotfix)\b", line):
                nudges.append(
                    NudgeProposal(
                        id=f"outcome-git-{idx}",
                        slug=self._slug(f"outcome-{line[:24]}"),
                        mode="outcome",
                        target="new_hook",
                        title="Guard against reverted/fixup commit pattern",
                        rationale=f"Detected post-merge corrective commit: `{line}`",
                        proposed_change=(
                            "Add pre-commit test/gate check covering the reverted area."
                        ),
                    )
                )

        if not nudges and dry_run:
            nudges.append(
                NudgeProposal(
                    id="outcome-dry-1",
                    slug="post-24h-ci-smoke",
                    mode="outcome",
                    target="new_hook",
                    title="Verify hook parity after 24h outcome window",
                    rationale="Dry-run outcome window found no regressions in local git log.",
                    proposed_change="Keep existing Stage 2 gate checks active in CI.",
                )
            )
        return nudges

    def _retro_audit(self) -> list[NudgeProposal]:
        nudges: list[NudgeProposal] = []
        agents_text = (
            (self.config.root / "AGENTS.md").read_text(encoding="utf-8", errors="replace")
            if (self.config.root / "AGENTS.md").exists()
            else ""
        )
        src_forge = self.config.root / "src" / "forge"
        if src_forge.exists():
            for pkg in sorted(src_forge.iterdir()):
                if pkg.is_dir() and not pkg.name.startswith("__"):
                    if f"{pkg.name}/" not in agents_text:
                        nudges.append(
                            NudgeProposal(
                                id=f"audit-doc-{pkg.name}",
                                slug=f"document-{pkg.name}",
                                mode="audit",
                                target="agents_md",
                                title=f"Document `src/forge/{pkg.name}/` in AGENTS.md",
                                rationale=(
                                    f"Package `src/forge/{pkg.name}/` is absent from AGENTS.md."
                                ),
                                proposed_change=(
                                    f"Add concise routing pointer for `{pkg.name}/` in AGENTS.md."
                                ),
                            )
                        )
        if not nudges:
            nudges.append(
                NudgeProposal(
                    id="audit-clean",
                    slug="docs-in-sync",
                    mode="audit",
                    target="delete_stale_rule",
                    title="No AGENTS.md / src drift detected",
                    rationale="All src/forge packages are referenced in AGENTS.md.",
                    proposed_change="No stale routing rules require removal.",
                )
            )
        return nudges

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")[:40] or "nudge"
