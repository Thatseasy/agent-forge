"""Stage 5 Cloud-Sync Exporters: GitHub, Obsidian, Notion, and rclone Drive/S3."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from forge.config import ForgeConfig, write_text_utf8_lf
from forge.sync.lockfile import LockfileManager


class CloudSyncResult(BaseModel):
    """Structured result for Obsidian, Notion, Drive, or GitHub sync actions."""

    target: str
    dry_run: bool
    exported_files: list[str] = Field(default_factory=list)
    commands_run: list[str] = Field(default_factory=list)
    notes: str = ""


class CloudExporter:
    """Exports lockfile leaderboards, nudges, and heavy artifacts to external targets."""

    def __init__(self, config: ForgeConfig, http_client: httpx.Client | None = None) -> None:
        self.config = config
        self.http_client = http_client

    def export_obsidian(self, dry_run: bool = False) -> CloudSyncResult:
        """Render Leaderboard.md, Nudges.md, and per-skill cards into Obsidian vault or staging."""
        vault = (
            Path(self.config.obsidian_vault_path)
            if self.config.obsidian_vault_path and not dry_run
            else (self.config.root / "staging" / "obsidian_export")
        )
        vault.mkdir(parents=True, exist_ok=True)
        skills_cards_dir = vault / "Skills"
        skills_cards_dir.mkdir(parents=True, exist_ok=True)

        lock_mgr = LockfileManager(self.config)
        lockfile = (
            lock_mgr._load_existing() if self.config.lockfile_path.exists() else lock_mgr.update()
        )

        # Leaderboard.md
        lb_lines = [
            "# Agent-Forge Skill Leaderboard",
            "",
            "| Skill | Version | Status | Tokens | pass^3 | ValueScore |",
            "| :--- | :--- | :--- | ---: | ---: | ---: |",
        ]
        exported: list[str] = []
        for entry in sorted(lockfile.skills.values(), key=lambda s: s.name):
            p3 = f"{entry.pass_pow_3:.3f}" if entry.pass_pow_3 is not None else "-"
            vs = f"{entry.value_score:.3f}" if entry.value_score is not None else "-"
            lb_lines.append(
                f"| [[Skills/{entry.name}\\|{entry.name}]] | {entry.version} | "
                f"{entry.status} | {entry.token_estimate} | {p3} | {vs} |"
            )
            card_path = skills_cards_dir / f"{entry.name}.md"
            card_text = (
                f"# {entry.name}\n\n"
                f"- **Path:** `{entry.path}`\n"
                f"- **Version:** `{entry.version}` ({entry.status})\n"
                f"- **SHA-256:** `{entry.sha256}`\n"
                f"- **Source:** {entry.source_url}\n"
                f"- **Token Estimate:** {entry.token_estimate}\n"
                f"- **pass^3:** {p3} | **ValueScore:** {vs}\n"
            )
            write_text_utf8_lf(card_path, card_text)
            exported.append(str(card_path))

        lb_path = vault / "Leaderboard.md"
        write_text_utf8_lf(lb_path, "\n".join(lb_lines) + "\n")
        exported.append(str(lb_path))

        # Nudges.md
        nudge_lines = ["# Evolutionary Nudge Backlog", ""]
        backlog_dir = self.config.nudges_dir / "backlog"
        if backlog_dir.exists():
            for nf in sorted(backlog_dir.glob("*.md")):
                nudge_lines.append(f"- `{nf.name}`")
        if len(nudge_lines) == 2:
            nudge_lines.append("- _No pending nudges._")
        nudges_path = vault / "Nudges.md"
        write_text_utf8_lf(nudges_path, "\n".join(nudge_lines) + "\n")
        exported.append(str(nudges_path))

        return CloudSyncResult(
            target="obsidian",
            dry_run=dry_run,
            exported_files=exported,
            notes=f"Exported {len(exported)} Markdown file(s) to {vault}",
        )

    def export_notion(self, dry_run: bool = False) -> CloudSyncResult:
        """Upsert lockfile skill metrics into Notion database (or dry-run preview JSON)."""
        lock_mgr = LockfileManager(self.config)
        lockfile = (
            lock_mgr._load_existing() if self.config.lockfile_path.exists() else lock_mgr.update()
        )
        pages = [
            {
                "parent": {"database_id": self.config.notion_database_id or "dry-run-db"},
                "properties": {
                    "Name": {"title": [{"text": {"content": e.name}}]},
                    "Version": {"rich_text": [{"text": {"content": e.version}}]},
                    "Status": {"select": {"name": e.status}},
                    "ValueScore": {"number": e.value_score or 0.0},
                },
            }
            for e in lockfile.skills.values()
        ]
        if dry_run:
            preview_path = self.config.root / "staging" / "notion_dry_run.json"
            write_text_utf8_lf(preview_path, json.dumps(pages, indent=2) + "\n")
            return CloudSyncResult(
                target="notion",
                dry_run=True,
                exported_files=[str(preview_path)],
                notes=f"Prepared {len(pages)} Notion page payload(s)",
            )

        if not self.config.notion_api_key or not self.config.notion_database_id:
            raise ValueError("Missing NOTION_API_KEY or NOTION_DATABASE_ID in .env")

        headers = {
            "Authorization": f"Bearer {self.config.notion_api_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }
        client = self.http_client or httpx.Client(timeout=20.0)
        try:
            for payload in pages:
                resp = client.post(
                    "https://api.notion.com/v1/pages",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
        finally:
            if self.http_client is None:
                client.close()

        return CloudSyncResult(
            target="notion",
            dry_run=False,
            notes=f"Pushed {len(pages)} Notion entries",
        )

    def export_drive(self, dry_run: bool = False) -> CloudSyncResult:
        """Sync heavy QA videos and archived bundles via `rclone copy`."""
        remote = self.config.rclone_remote or "drive:agent-forge-artifacts"
        commands: list[str] = []
        for folder_name in ("qa-artifacts", "archive"):
            src = self.config.root / folder_name
            if not src.exists():
                continue
            cmd = ["rclone", "copy", str(src), f"{remote}/{folder_name}"]
            if dry_run:
                cmd.append("--dry-run")
            commands.append(" ".join(cmd))
            if not dry_run:
                if not shutil.which("rclone"):
                    raise RuntimeError("rclone not found in PATH")
                subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)

        return CloudSyncResult(
            target="drive",
            dry_run=dry_run,
            commands_run=commands,
            notes=f"Prepared/executed {len(commands)} rclone copy command(s)",
        )

    def prepare_github_push(self, dry_run: bool = True) -> CloudSyncResult:
        """Verify lockfile integrity and summarize or push Git changes."""
        verify = LockfileManager(self.config).verify()
        if not verify.valid:
            raise RuntimeError(
                f"Lockfile verification failed: mismatches={verify.mismatches}, "
                f"untracked={verify.untracked}, missing={verify.missing}"
            )
        status_proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.config.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        dirty = [ln for ln in status_proc.stdout.splitlines() if ln.strip()]
        cmds = ["git status --porcelain"]
        if not dry_run:
            subprocess.run(["git", "push"], cwd=self.config.root, check=True)
            cmds.append("git push")
        return CloudSyncResult(
            target="github",
            dry_run=dry_run,
            commands_run=cmds,
            notes=f"Lockfile verified OK; {len(dirty)} uncommitted path(s) detected.",
        )
