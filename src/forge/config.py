"""Central configuration and UTF-8/LF file utilities for agent-forge."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic import BaseModel, Field

from forge.paths import (
    resolve_agent_forge_home,
    resolve_package_assets_dir,
    resolve_state_root,
)


def find_repo_root(start: Path | None = None) -> Path:
    """Resolve the active forge root (`FORGE_ROOT`, `AGENT_FORGE_HOME`, checkout, or home)."""
    root, _ = resolve_state_root(start)
    return root


def write_text_utf8_lf(path: Path, content: str) -> Path:
    """Write text strictly as UTF-8 without BOM and LF line endings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    path.write_text(normalized, encoding="utf-8", newline="\n")
    return path


def write_json_utf8_lf(path: Path, data: Any, indent: int = 2) -> Path:
    """Serialize JSON strictly as UTF-8 without BOM and LF line endings."""
    return write_text_utf8_lf(path, json.dumps(data, indent=indent, ensure_ascii=False) + "\n")


def estimate_tokens(text: str) -> int:
    """Estimate LLM token count deterministically (~4 chars/token heuristic)."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


class ForgeConfig(BaseModel):
    """Resolved workspace paths, environment keys, and model defaults."""

    root: Path
    home_dir: Path = Field(default_factory=resolve_agent_forge_home)
    assets_dir: Path = Field(default_factory=resolve_package_assets_dir)
    perplexity_api_key: str = ""
    firecrawl_api_key: str = ""
    exa_api_key: str = ""
    context7_api_key: str = ""
    gemini_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    aa_api_key: str = ""
    judge_model: str = Field(default="gpt-5.6-luna")
    video_lite_model: str = Field(default="gemini-3.5-flash-lite")
    video_flash_model: str = Field(default="gemini-3.8-flash")
    obsidian_vault_path: str = ""
    notion_api_key: str = ""
    notion_database_id: str = ""
    rclone_remote: str = ""

    @property
    def state_dir(self) -> Path:
        return self.root / ".agents" / "state"

    @property
    def agents_skills_dir(self) -> Path:
        return self.root / ".agents" / "skills"

    @property
    def staging_inbox_dir(self) -> Path:
        return self.root / "staging" / "inbox"

    @property
    def library_dir(self) -> Path:
        return self.root / "library"

    @property
    def library_skills_dir(self) -> Path:
        return self.library_dir / "skills"

    @property
    def nudges_dir(self) -> Path:
        return self.library_dir / "nudges"

    @property
    def profiles_dir(self) -> Path:
        return self.library_dir / "profiles"

    @property
    def units_dir(self) -> Path:
        return self.library_dir / "units"

    @property
    def evals_suites_dir(self) -> Path:
        return self.root / "evals" / "suites"

    @property
    def evals_held_out_dir(self) -> Path:
        return self.root / "evals" / "held-out"

    @property
    def archive_dir(self) -> Path:
        return self.root / "archive"

    @property
    def lockfile_path(self) -> Path:
        return self.root / "skills-lock.json"

    @property
    def units_lockfile_path(self) -> Path:
        return self.root / "units-lock.json"

    def resolve_profile_path(self, profile_name: str) -> Path | None:
        """Resolve `<profile>.yaml` from active root first, then bundled package assets."""
        for base in (self.profiles_dir, self.assets_dir / "profiles"):
            cand = base / f"{profile_name}.yaml"
            if cand.is_file():
                return cand
        return None

    def resolve_skill_dir(self, skill_name: str) -> Path | None:
        """Resolve skill folder from library, `.agents/skills`, installed units, or assets."""
        candidates: list[Path] = [
            self.library_skills_dir / skill_name,
            self.agents_skills_dir / skill_name,
        ]
        if self.units_dir.is_dir():
            for unit_dir in sorted(self.units_dir.iterdir()):
                if unit_dir.is_dir():
                    candidates.append(unit_dir / "skills" / skill_name)
        candidates.append(self.assets_dir / "skills" / skill_name)
        for cand in candidates:
            if cand.is_dir() and (cand / "SKILL.md").is_file():
                return cand
        return None

    def resolve_nudge_file(self, filename: str) -> Path | None:
        """Resolve nudge config file from active root or bundled package assets."""
        for base in (self.nudges_dir, self.assets_dir / "nudges"):
            cand = base / filename
            if cand.is_file():
                return cand
        return None

    def resolve_cli_file(self, filename: str) -> Path | None:
        """Resolve catalog/snapshot file from active root or bundled package assets."""
        for base in (self.library_dir / "clis", self.assets_dir / "clis"):
            cand = base / filename
            if cand.is_file():
                return cand
        return None

    def resolve_hooks_dir(self) -> Path | None:
        """Resolve workflow hook scripts directory from active root or bundled assets."""
        for cand in (self.root / ".agents" / "hooks", self.assets_dir / "hooks"):
            if cand.is_dir():
                return cand
        return None

    def resolve_hooks_json(self) -> Path | None:
        """Resolve `.agents/hooks.json` template from active root or bundled assets."""
        for cand in (self.root / ".agents" / "hooks.json", self.assets_dir / "hooks.json"):
            if cand.is_file():
                return cand
        return None

    @classmethod
    def load(cls, root: Path | None = None) -> ForgeConfig:
        """Load config from env, `AGENT_FORGE_HOME/secrets.env`, and checkout `.env`."""
        resolved_root, _ = resolve_state_root(root)
        resolved_home = resolve_agent_forge_home()
        resolved_assets = resolve_package_assets_dir()

        secrets_file = resolved_home / "secrets.env"
        env_file = resolved_root / ".env"
        secrets_vals = dotenv_values(secrets_file) if secrets_file.is_file() else {}
        env_vals = dotenv_values(env_file) if env_file.is_file() else {}
        file_vals = {**env_vals, **secrets_vals}

        def _get(key: str, default: str = "") -> str:
            if key in os.environ and os.environ[key]:
                return os.environ[key].strip()
            val = file_vals.get(key)
            return str(val).strip() if val else default

        return cls(
            root=resolved_root,
            home_dir=resolved_home,
            assets_dir=resolved_assets,
            perplexity_api_key=_get("PERPLEXITY_API_KEY"),
            firecrawl_api_key=_get("FIRECRAWL_API_KEY"),
            exa_api_key=_get("EXA_API_KEY"),
            context7_api_key=_get("CONTEXT7_API_KEY"),
            gemini_api_key=_get("GEMINI_API_KEY"),
            openai_api_key=_get("OPENAI_API_KEY"),
            anthropic_api_key=_get("ANTHROPIC_API_KEY"),
            aa_api_key=_get("AA_API_KEY"),
            judge_model=_get("FORGE_JUDGE_MODEL", "gpt-5.6-luna"),
            video_lite_model=_get("FORGE_VIDEO_LITE_MODEL", "gemini-3.5-flash-lite"),
            video_flash_model=_get("FORGE_VIDEO_FLASH_MODEL", "gemini-3.8-flash"),
            obsidian_vault_path=_get("OBSIDIAN_VAULT_PATH"),
            notion_api_key=_get("NOTION_API_KEY"),
            notion_database_id=_get("NOTION_DATABASE_ID"),
            rclone_remote=_get("RCLONE_REMOTE"),
        )
